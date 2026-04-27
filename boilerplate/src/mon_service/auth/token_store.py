# -*- coding: utf-8 -*-
"""
Token Store S3 avec cache mémoire TTL 5 minutes.

Si S3 n'est pas configuré, les tokens sont gérés en mémoire uniquement
(bootstrap key). Quand S3 est configuré, les tokens sont stockés dans
_system/tokens.json sur le bucket S3.

Pattern :
    init_token_store()     → Appelé au démarrage (charge depuis S3)
    get_token_store()      → Getter singleton (retourne None si pas configuré)
"""

import sys
import time
import json
import hashlib
from typing import Optional
from urllib.parse import quote

from ..config import get_settings

# =============================================================================
# Vault configuration helpers
# =============================================================================


def get_vault_application_token(settings) -> str:
    """Return the MCP Vault application token.

    Token file has priority over environment variable to support Docker/systemd
    secret injection in production.
    """
    token_file = getattr(settings, "mcp_vault_token_file", "") or ""
    if token_file:
        from pathlib import Path
        path = Path(token_file)
        if path.exists():
            return path.read_text().strip()
    return (getattr(settings, "mcp_vault_token", "") or "").strip()


def validate_vault_settings(settings) -> str:
    """Validate Vault TokenStore settings and return the application token.

    Raises ValueError with admin-readable messages for misconfiguration.
    """
    if not (getattr(settings, "mcp_vault_id", "") or "").strip():
        raise ValueError("MCP_VAULT_ID is required when TOKEN_STORE_BACKEND=vault")
    token = get_vault_application_token(settings)
    if not token:
        raise ValueError("MCP_VAULT_TOKEN_FILE or MCP_VAULT_TOKEN is required when TOKEN_STORE_BACKEND=vault")
    return token


# =============================================================================
# Token Store singleton
# =============================================================================

_token_store = None


def get_token_store() -> Optional[object]:
    """Retourne le Token Store (None si S3 non configuré)."""
    return _token_store


def get_token_store_status() -> dict:
    """Return non-sensitive status information about the active token store."""
    settings = get_settings()
    backend = getattr(settings, "token_store_backend", "s3").strip().lower()
    store = get_token_store()

    status = {
        "backend": backend,
        "configured": False,
        "loaded": store is not None,
        "tokens_count": store.count() if store else 0,
        "cache_ttl": int(getattr(settings, "token_store_cache_ttl", 300) or 300),
    }

    if backend == "s3":
        status["configured"] = bool(settings.s3_endpoint_url and settings.s3_bucket_name)
        status["bucket_name"] = settings.s3_bucket_name if settings.s3_bucket_name else ""
        return status

    if backend == "vault":
        status["vault_id"] = getattr(settings, "mcp_vault_id", "") or ""
        status["path"] = getattr(settings, "mcp_vault_token_store_path", "") or ""
        status["configured"] = bool(status["vault_id"] and get_vault_application_token(settings))
        return status

    return status


def init_token_store():
    """Initialise le Token Store au démarrage selon TOKEN_STORE_BACKEND."""
    global _token_store
    settings = get_settings()
    backend = getattr(settings, "token_store_backend", "s3").strip().lower()

    if backend == "s3":
        if settings.s3_endpoint_url and settings.s3_bucket_name:
            _token_store = S3TokenStore(settings)
            _token_store.load()
            print(f"🔑 Token Store S3 initialisé ({_token_store.count()} tokens)", file=sys.stderr)
        else:
            _token_store = None
            print("🔑 Token Store S3 non configuré (bootstrap key uniquement)", file=sys.stderr)
        return

    if backend == "vault":
        validate_vault_settings(settings)
        _token_store = VaultTokenStore(settings)
        _token_store.load()
        print(f"🔑 Token Store Vault initialisé ({_token_store.count()} tokens)", file=sys.stderr)
        return

    raise ValueError(f"Unsupported TOKEN_STORE_BACKEND: {backend}")


# =============================================================================
# S3TokenStore — Stockage S3 + cache mémoire TTL
# =============================================================================

class S3TokenStore:
    """
    Gestion des tokens d'accès MCP.

    - Stockage sur S3 : _system/tokens.json
    - Cache mémoire avec TTL de 5 minutes
    - CRUD : create, list, info, revoke
    """

    CACHE_TTL = 300  # 5 minutes
    S3_KEY = "_system/tokens.json"

    def __init__(self, settings):
        self.settings = settings
        self._tokens: dict = {}  # hash → token_info
        self._cache_time: float = 0
        self._s3_client = None

    def _get_s3(self):
        """Lazy-load du client S3 boto3.

        Cloud Temple / Dell ECS requires SigV2 for object data operations
        (GET/PUT/DELETE), while many generic S3-compatible providers accept
        SigV4. Keep the signature version configurable and default to the
        Cloud Temple-compatible value.
        """
        if self._s3_client is None:
            import boto3
            from botocore.config import Config

            config = Config(
                region_name=self.settings.s3_region_name,
                signature_version=getattr(self.settings, "s3_signature_version", "s3"),
                s3={"addressing_style": getattr(self.settings, "s3_addressing_style", "path")},
                retries={"max_attempts": 3, "mode": "adaptive"},
            )
            self._s3_client = boto3.client(
                "s3",
                endpoint_url=self.settings.s3_endpoint_url,
                aws_access_key_id=self.settings.s3_access_key_id,
                aws_secret_access_key=self.settings.s3_secret_access_key,
                config=config,
            )
        return self._s3_client

    def load(self):
        """Charge les tokens depuis S3."""
        try:
            s3 = self._get_s3()
            resp = s3.get_object(Bucket=self.settings.s3_bucket_name, Key=self.S3_KEY)
            data = json.loads(resp["Body"].read().decode())
            self._tokens = {t["hash"]: t for t in data.get("tokens", [])}
            self._cache_time = time.time()
        except Exception as e:
            if "NoSuchKey" in str(e) or "404" in str(e):
                self._tokens = {}
                self._cache_time = time.time()
            else:
                print(f"⚠️  Token Store S3 : {e}", file=sys.stderr)

    def _save(self):
        """Sauvegarde les tokens sur S3."""
        try:
            s3 = self._get_s3()
            data = json.dumps(
                {"tokens": list(self._tokens.values())},
                indent=2, default=str,
            )
            s3.put_object(
                Bucket=self.settings.s3_bucket_name,
                Key=self.S3_KEY,
                Body=data.encode(),
                ContentType="application/json",
            )
        except Exception as e:
            print(f"⚠️  Token Store S3 save : {e}", file=sys.stderr)

    def _maybe_refresh(self):
        """Rafraîchit le cache si le TTL est dépassé."""
        if time.time() - self._cache_time > self.CACHE_TTL:
            self.load()

    def get_by_hash(self, token_hash: str) -> Optional[dict]:
        """Cherche un token par son hash SHA-256. Vérifie l'expiration."""
        self._maybe_refresh()
        token = self._tokens.get(token_hash)
        if token and token.get("expires_at"):
            from datetime import datetime, timezone
            try:
                expires = datetime.fromisoformat(token["expires_at"])
                if datetime.now(timezone.utc) > expires:
                    return None  # Token expiré
            except (ValueError, TypeError):
                # ⚠️ FAIL-CLOSE : si expires_at est corrompu, rejeter le token.
                # Un `pass` ici serait un fail-open : le token passerait
                # malgré une date d'expiration invalide.
                return None
        return token

    def create(self, client_name: str, permissions: list, allowed_resources: list = None,
               expires_in_days: int = 90, email: str = "") -> dict:
        """Crée un nouveau token et le sauvegarde sur S3."""
        import secrets
        from datetime import datetime, timezone, timedelta

        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

        now = datetime.now(timezone.utc)
        expires_at = None
        if expires_in_days and expires_in_days > 0:
            expires_at = (now + timedelta(days=expires_in_days)).isoformat()

        token_info = {
            "hash": token_hash,
            "client_name": client_name,
            "permissions": permissions,
            "allowed_resources": allowed_resources or [],
            "email": email,
            "created_at": now.isoformat(),
            "expires_at": expires_at,
            "revoked": False,
        }

        self._tokens[token_hash] = token_info
        self._save()

        return {"raw_token": raw_token, **token_info}

    def list_all(self) -> list:
        """Liste tous les tokens (sans les hash complets)."""
        self._maybe_refresh()
        return [
            {
                "client_name": t["client_name"],
                "permissions": t["permissions"],
                "email": t.get("email", ""),
                "hash_prefix": t["hash"][:12],
                "expires_at": t.get("expires_at"),
                "revoked": t.get("revoked", False),
            }
            for t in self._tokens.values()
        ]

    def revoke(self, hash_prefix: str) -> bool:
        """Révoque un token par préfixe de hash (≥8 caractères requis)."""
        # ⚠️ Min 8 chars pour éviter de révoquer le mauvais token
        # avec un préfixe trop court (collision de hash).
        if len(hash_prefix) < 8:
            return False
        for h, t in self._tokens.items():
            if h.startswith(hash_prefix):
                t["revoked"] = True
                t["revoked_at"] = __import__("datetime").datetime.now(
                    __import__("datetime").timezone.utc
                ).isoformat()
                self._save()
                return True
        return False

    def update(self, hash_prefix: str, permissions: list = None,
               allowed_resources: list = None) -> dict:
        """
        Modifie les permissions et/ou ressources autorisées d'un token.

        Seuls les champs fournis (non None) sont modifiés.
        ⚠️ hash_prefix doit faire ≥ 8 caractères (anti-collision).
        """
        if len(hash_prefix) < 8:
            return {"status": "error", "message": "Hash prefix trop court (min 8 caractères)"}

        for h, t in self._tokens.items():
            if h.startswith(hash_prefix):
                updated_fields = []
                if permissions is not None:
                    t["permissions"] = permissions
                    updated_fields.append("permissions")
                if allowed_resources is not None:
                    t["allowed_resources"] = allowed_resources
                    updated_fields.append("allowed_resources")

                if not updated_fields:
                    return {"status": "error", "message": "Aucun champ à modifier"}

                # Invalider le cache pour forcer le rechargement
                self._cache_time = 0
                self._save()

                return {
                    "status": "updated",
                    "client_name": t.get("client_name", "?"),
                    "hash_prefix": h[:12],
                    "updated_fields": updated_fields,
                    "permissions": t.get("permissions", []),
                    "allowed_resources": t.get("allowed_resources", []),
                }

        return {"status": "error", "message": f"Token {hash_prefix[:12]}… non trouvé"}

    def count(self) -> int:
        """Nombre de tokens actifs (non révoqués)."""
        return sum(1 for t in self._tokens.values() if not t.get("revoked", False))


# =============================================================================
# VaultTokenStore — Stockage MCP Vault + cache mémoire TTL
# =============================================================================

class VaultTokenStore:
    """TokenStore backend persisted as one JSON secret in MCP Vault.

    V1 format stores the same logical payload as S3TokenStore under:

        vault: settings.mcp_vault_id
        path:  settings.mcp_vault_token_store_path

    Only `load`, `get_by_hash`, `list_all` and `count` are implemented in this
    step. Mutating operations are implemented in the next step.
    """

    def __init__(self, settings):
        self.settings = settings
        self._tokens: dict = {}
        self._cache_time: float = 0
        self._vault_token = get_vault_application_token(settings)

    @property
    def CACHE_TTL(self) -> int:
        return int(getattr(self.settings, "token_store_cache_ttl", 300) or 300)

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._vault_token}"}

    def _secret_url(self) -> str:
        base = self.settings.mcp_vault_url.rstrip("/")
        path = quote(self.settings.mcp_vault_token_store_path, safe="")
        return f"{base}/admin/api/vaults/{self.settings.mcp_vault_id}/secrets/{path}"

    def load(self):
        """Charge les tokens depuis MCP Vault.

        - 404 => store vide
        - 401/403 => erreur permission/auth claire
        - 5xx/timeout => erreur Vault indisponible
        """
        import httpx

        try:
            resp = httpx.get(
                self._secret_url(),
                headers=self._headers(),
                timeout=float(getattr(self.settings, "mcp_vault_timeout", 5.0) or 5.0),
            )
        except httpx.TimeoutException as exc:
            self._tokens = {}
            self._cache_time = time.time()
            raise RuntimeError("MCP Vault unavailable: timeout while loading token store") from exc
        except httpx.HTTPError as exc:
            self._tokens = {}
            self._cache_time = time.time()
            raise RuntimeError(f"MCP Vault unavailable while loading token store: {exc}") from exc

        if resp.status_code == 404:
            self._tokens = {}
            self._cache_time = time.time()
            return

        if resp.status_code in (401, 403):
            self._tokens = {}
            self._cache_time = time.time()
            raise RuntimeError(f"MCP Vault permission denied while loading token store (HTTP {resp.status_code})")

        if resp.status_code >= 500:
            self._tokens = {}
            self._cache_time = time.time()
            raise RuntimeError(f"MCP Vault unavailable while loading token store (HTTP {resp.status_code})")

        if resp.status_code >= 300:
            self._tokens = {}
            self._cache_time = time.time()
            raise RuntimeError(f"MCP Vault error while loading token store (HTTP {resp.status_code})")

        payload = resp.json()
        data = payload.get("data", {}) if isinstance(payload, dict) else {}
        tokens = data.get("tokens", []) if isinstance(data, dict) else []
        if tokens is None:
            tokens = []
        if not isinstance(tokens, list):
            raise RuntimeError("MCP Vault token store payload is invalid: data.tokens must be a list")

        self._tokens = {t["hash"]: t for t in tokens if isinstance(t, dict) and "hash" in t}
        self._cache_time = time.time()

    def _maybe_refresh(self):
        """Rafraîchit le cache si le TTL est dépassé."""
        if time.time() - self._cache_time > self.CACHE_TTL:
            self.load()

    def get_by_hash(self, token_hash: str) -> Optional[dict]:
        """Cherche un token par son hash SHA-256. Vérifie l'expiration."""
        self._maybe_refresh()
        token = self._tokens.get(token_hash)
        if token and token.get("expires_at"):
            from datetime import datetime, timezone
            try:
                expires = datetime.fromisoformat(token["expires_at"])
                if datetime.now(timezone.utc) > expires:
                    return None
            except (ValueError, TypeError):
                return None
        return token

    def list_all(self) -> list:
        """Liste tous les tokens (sans hash complet)."""
        self._maybe_refresh()
        return [
            {
                "client_name": t["client_name"],
                "permissions": t["permissions"],
                "policy_id": t.get("policy_id", ""),
                "email": t.get("email", ""),
                "hash_prefix": t["hash"][:12],
                "allowed_resources": t.get("allowed_resources", []),
                "created_at": t.get("created_at", ""),
                "expires_at": t.get("expires_at"),
                "revoked": t.get("revoked", False),
                "revoked_at": t.get("revoked_at", ""),
            }
            for t in self._tokens.values()
        ]

    def _save(self):
        """Sauvegarde les tokens dans MCP Vault."""
        import httpx

        url = f"{self.settings.mcp_vault_url.rstrip('/')}/admin/api/vaults/{self.settings.mcp_vault_id}/secrets"
        body = {
            "path": self.settings.mcp_vault_token_store_path,
            "type": "custom",
            "data": {"tokens": list(self._tokens.values())},
        }
        try:
            resp = httpx.post(
                url,
                headers={**self._headers(), "Content-Type": "application/json"},
                json=body,
                timeout=float(getattr(self.settings, "mcp_vault_timeout", 5.0) or 5.0),
            )
        except httpx.TimeoutException as exc:
            raise RuntimeError("MCP Vault unavailable: timeout while saving token store") from exc
        except httpx.HTTPError as exc:
            raise RuntimeError(f"MCP Vault unavailable while saving token store: {exc}") from exc

        if resp.status_code in (401, 403):
            raise RuntimeError(f"MCP Vault permission denied while saving token store (HTTP {resp.status_code})")
        if resp.status_code >= 500:
            raise RuntimeError(f"MCP Vault unavailable while saving token store (HTTP {resp.status_code})")
        if resp.status_code >= 300:
            raise RuntimeError(f"MCP Vault error while saving token store (HTTP {resp.status_code})")

    def create(self, client_name: str, permissions: list, allowed_resources: list = None,
               expires_in_days: int = 90, email: str = "", policy_id: str = "") -> dict:
        """Crée un nouveau token et le sauvegarde dans MCP Vault."""
        import secrets
        from datetime import datetime, timezone, timedelta

        # Best-effort race reduction: start from latest Vault state before mutating.
        self.load()

        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

        now = datetime.now(timezone.utc)
        expires_at = None
        if expires_in_days and expires_in_days > 0:
            expires_at = (now + timedelta(days=expires_in_days)).isoformat()

        token_info = {
            "hash": token_hash,
            "client_name": client_name,
            "permissions": permissions,
            "allowed_resources": allowed_resources or [],
            "policy_id": policy_id,
            "email": email,
            "created_at": now.isoformat(),
            "expires_at": expires_at,
            "revoked": False,
        }

        self._tokens[token_hash] = token_info
        self._save()

        return {"raw_token": raw_token, **token_info}

    def update(self, hash_prefix: str, policy_id: str = None,
               permissions: list = None, allowed_resources: list = None) -> dict:
        """Modifie un token existant dans MCP Vault."""
        if len(hash_prefix) < 8:
            return {"status": "error", "message": "Hash prefix trop court (min 8 caractères)"}

        self.load()

        target_hash = None
        for h in self._tokens:
            if h.startswith(hash_prefix):
                target_hash = h
                break

        if not target_hash:
            return {"status": "error", "message": f"Token {hash_prefix[:12]}… non trouvé"}

        token = self._tokens[target_hash]
        if token.get("revoked"):
            return {"status": "error", "message": f"Token {hash_prefix[:12]}… est révoqué"}

        updated_fields = []
        if policy_id is not None:
            token["policy_id"] = policy_id
            updated_fields.append("policy_id")
        if permissions is not None:
            token["permissions"] = permissions
            updated_fields.append("permissions")
        if allowed_resources is not None:
            token["allowed_resources"] = allowed_resources
            updated_fields.append("allowed_resources")

        if not updated_fields:
            return {"status": "error", "message": "Aucun champ à modifier"}

        self._save()

        return {
            "status": "updated",
            "client_name": token.get("client_name", "?"),
            "hash_prefix": target_hash[:12],
            "updated_fields": updated_fields,
            "policy_id": token.get("policy_id", ""),
            "permissions": token.get("permissions", []),
            "allowed_resources": token.get("allowed_resources", []),
        }

    def revoke(self, hash_prefix: str) -> bool:
        """Révoque un token par préfixe de hash dans MCP Vault."""
        if len(hash_prefix) < 8:
            return False

        self.load()

        from datetime import datetime, timezone
        for h, t in self._tokens.items():
            if h.startswith(hash_prefix):
                t["revoked"] = True
                t["revoked_at"] = datetime.now(timezone.utc).isoformat()
                self._save()
                return True
        return False

    def count(self) -> int:
        """Nombre de tokens actifs (non révoqués)."""
        return sum(1 for t in self._tokens.values() if not t.get("revoked", False))


# Backward-compatible alias for existing imports/tests.
TokenStore = S3TokenStore
