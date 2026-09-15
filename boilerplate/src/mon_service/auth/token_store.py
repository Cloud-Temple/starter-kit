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
import copy
import functools
import threading
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


_DEFAULT_CACHE_TTL = 300  # secondes


def _configured_cache_ttl(settings) -> int:
    """TTL du cache de tokens, en respectant un `0` explicite.

    Un `0` configuré signifie « ne jamais servir depuis le cache ». Le repli
    `int(...) or 300` le transformait silencieusement en 300 secondes, et
    rendait à l'exploitant la fenêtre de révocation de cinq minutes qu'il
    venait précisément de refuser.
    """
    raw = getattr(settings, "token_store_cache_ttl", None)
    if raw is None or raw == "":
        return _DEFAULT_CACHE_TTL
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return _DEFAULT_CACHE_TTL


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
        "cache_ttl": _configured_cache_ttl(settings),
    }

    if store is not None:
        cache_time = getattr(store, "_cache_time", 0) or 0
        # Le message d'erreur reste dans les logs : il n'a pas à transiter par
        # une réponse HTTP, même d'administration.
        status["reachable"] = getattr(store, "_last_error", None) is None
        status["cache_age_seconds"] = int(time.time() - cache_time) if cache_time else None
        status["never_loaded"] = not bool(cache_time)

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


def _load_at_startup(store, label: str) -> None:
    """Charge le magasin au démarrage sans empêcher le service de démarrer.

    Refuser de démarrer sur une panne du magasin coûterait aussi `/health`, la
    console admin et la clé bootstrap, c'est-à-dire les moyens de diagnostiquer
    et de corriger. Le service démarre dégradé : l'authentification par token
    répond 503 tant que le magasin est injoignable.
    """
    try:
        store.load()
    except TokenStoreUnavailable as e:
        print(f"⚠️  Token Store {label} injoignable au démarrage : {e}", file=sys.stderr)
        print("   → démarrage dégradé, authentification par token refusée (HTTP 503)",
              file=sys.stderr)
        return
    print(f"🔑 Token Store {label} initialisé ({store.count()} tokens)", file=sys.stderr)


def init_token_store():
    """Initialise le Token Store au démarrage selon TOKEN_STORE_BACKEND."""
    global _token_store
    settings = get_settings()
    backend = getattr(settings, "token_store_backend", "s3").strip().lower()

    if backend == "s3":
        if settings.s3_endpoint_url and settings.s3_bucket_name:
            _token_store = S3TokenStore(settings)
            _load_at_startup(_token_store, "S3")
        else:
            _token_store = None
            print("🔑 Token Store S3 non configuré (bootstrap key uniquement)", file=sys.stderr)
        return

    if backend == "vault":
        validate_vault_settings(settings)
        _token_store = VaultTokenStore(settings)
        _load_at_startup(_token_store, "Vault")
        return

    raise ValueError(f"Unsupported TOKEN_STORE_BACKEND: {backend}")


# =============================================================================
# S3TokenStore — Stockage S3 + cache mémoire TTL
# =============================================================================

def _is_missing_object(error: Exception) -> bool:
    """Vrai seulement si le magasin n'existe pas encore.

    Chercher « 404 » dans le texte de l'erreur transformait une panne de proxy
    en magasin vide, donc en « aucun token connu », sans rien signaler.
    """
    code = ""
    status = None
    response = getattr(error, "response", None)
    if isinstance(response, dict):
        code = str(response.get("Error", {}).get("Code", "") or "")
        meta = response.get("ResponseMetadata", {})
        if isinstance(meta, dict):
            status = meta.get("HTTPStatusCode")
    del status  # le seul signal fiable est le code d'erreur, pas le statut HTTP
    return code == "NoSuchKey" or type(error).__name__ == "NoSuchKey"


def _serialise(method):
    """Sérialise une mutation du magasin.

    `create`, `revoke` et `update` sont des lire-modifier-écrire. Deux appels
    concurrents pouvaient s'entrelacer, et le second écraser la mutation du
    premier. Ce verrou ne couvre que ce process : sans écriture conditionnelle
    côté magasin, deux instances peuvent toujours s'écraser mutuellement.
    """
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapper


def _persist_or_restore(store, token_hash: str, previous_entry,
                        keep_on_failure: bool = False) -> None:
    """Écrit l'état courant, ou garde en mémoire l'état le plus restrictif.

    Une écriture qui échoue est ambiguë : le magasin a pu appliquer la requête
    et perdre sa réponse en route. On ne peut donc pas savoir laquelle des deux
    versions fait foi, et la seule règle sûre est de conserver localement celle
    qui refuse le plus :

    - une création ou une élévation refusée est retirée (`keep_on_failure`
      faux) : cette instance n'accorde pas un accès que le magasin n'a pas
      enregistré ;
    - une révocation est conservée (`keep_on_failure` vrai) : l'annuler
      rendrait valide, ici, un token que le magasin a peut-être déjà marqué
      mort.

    La restauration reste limitée à l'entrée concernée, pour ne pas emporter
    une mutation portant sur un autre token. Dans les deux cas le cache est
    marqué à revalider, pour que la lecture suivante aille chercher la vérité
    plutôt que de servir un état incertain.
    """
    try:
        store._save()
    except TokenStoreUnavailable:
        if not keep_on_failure:
            if previous_entry is None:
                store._tokens.pop(token_hash, None)
            else:
                store._tokens[token_hash] = previous_entry
        store._needs_reload = True
        raise


class TokenStoreUnavailable(RuntimeError):
    """Le magasin de tokens est injoignable, ou refuse d'écrire.

    Levée plutôt qu'avalée : un magasin muet laisserait croire qu'un token
    révoqué n'existe pas, ou qu'un token créé a été persisté.
    """


class S3TokenStore:
    """
    Gestion des tokens d'accès MCP.

    - Stockage sur S3 : _system/tokens.json
    - Cache mémoire avec TTL de 5 minutes
    - CRUD : create, list, info, revoke
    """

    DEFAULT_CACHE_TTL = _DEFAULT_CACHE_TTL
    S3_KEY = "_system/tokens.json"
    BACKOFF_MIN = 1.0   # secondes
    BACKOFF_MAX = 60.0  # secondes

    def __init__(self, settings):
        self.settings = settings
        self._tokens: dict = {}  # hash → token_info
        self._cache_time: float = 0
        self._s3_client = None
        # Panne en cours : on ne retente pas S3 à chaque requête.
        self._backoff: float = 0.0
        self._backoff_until: float = 0.0
        self._last_error: Optional[str] = None
        # Une écriture au sort incertain rend le cache douteux : la lecture
        # suivante doit revalider, sans attendre l'expiration du TTL.
        self._needs_reload: bool = False
        # Une mutation est un lire-modifier-écrire : la sérialiser évite qu'une
        # lecture concurrente remplace `_tokens` entre le rechargement et
        # l'écriture. Ne dit rien des autres instances.
        self._lock = threading.RLock()

    @property
    def CACHE_TTL(self) -> int:
        """TTL du cache, lu dans la configuration et non figé à 300s."""
        return _configured_cache_ttl(self.settings)

    @property
    def fail_mode(self) -> str:
        """`fail_close` (défaut) ou `fail_open`. Lu à chaud, jamais deviné."""
        mode = getattr(self.settings, "token_store_fail_mode", "fail_close")
        return "fail_open" if str(mode).lower() == "fail_open" else "fail_close"

    @property
    def stale_grace(self) -> int:
        """Durée pendant laquelle un cache périmé reste servi malgré la panne."""
        try:
            return max(0, int(getattr(self.settings, "token_store_stale_grace", 300)))
        except (TypeError, ValueError):
            return 300

    def _note_failure(self, error: Exception) -> None:
        self._last_error = str(error)
        self._backoff = min(max(self._backoff * 2, self.BACKOFF_MIN), self.BACKOFF_MAX)
        self._backoff_until = time.time() + self._backoff
        print(f"⚠️  Token Store S3 : {error} (nouvel essai dans {self._backoff:.0f}s)",
              file=sys.stderr)

    def _clear_failure(self) -> None:
        self._backoff = 0.0
        self._backoff_until = 0.0
        self._last_error = None

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
        """Charge les tokens depuis S3.

        Un objet absent est un magasin vide, pas une panne. Toute autre erreur
        est levée : le cache et son horodatage restent en l'état, sans quoi une
        panne se présenterait comme un magasin vide.
        """
        try:
            s3 = self._get_s3()
            resp = s3.get_object(Bucket=self.settings.s3_bucket_name, Key=self.S3_KEY)
            data = json.loads(resp["Body"].read().decode())
            with self._lock:
                self._tokens = {t["hash"]: t for t in data.get("tokens", [])}
                self._cache_time = time.time()
                self._needs_reload = False
            self._clear_failure()
        except Exception as e:
            if _is_missing_object(e):
                with self._lock:
                    self._tokens = {}
                    self._cache_time = time.time()
                    self._needs_reload = False
                self._clear_failure()
                return
            self._note_failure(e)
            raise TokenStoreUnavailable(f"chargement du magasin de tokens impossible : {e}") from e

    def _save(self):
        """Sauvegarde les tokens sur S3.

        Un échec est levé : sans cela, un token créé serait rendu à l'appelant
        alors qu'il n'existe nulle part, et une révocation serait annoncée sans
        avoir été écrite.
        """
        try:
            s3 = self._get_s3()
            with self._lock:
                instantane = list(self._tokens.values())
            data = json.dumps({"tokens": instantane}, indent=2, default=str)
            s3.put_object(
                Bucket=self.settings.s3_bucket_name,
                Key=self.S3_KEY,
                Body=data.encode(),
                ContentType="application/json",
            )
            self._clear_failure()
        except Exception as e:
            self._note_failure(e)
            raise TokenStoreUnavailable(f"écriture du magasin de tokens impossible : {e}") from e

    def _maybe_refresh(self):
        """Rafraîchit le cache si le TTL est dépassé.

        Pendant une panne, le cache périmé reste servi le temps de la fenêtre
        `TOKEN_STORE_STALE_GRACE`, puis l'accès est refusé. Le mode
        `TOKEN_STORE_FAIL_MODE=fail_open` lève cette limite, au prix explicite
        de révocations ignorées tant que la panne dure.
        """
        age = time.time() - self._cache_time
        if age <= self.CACHE_TTL and not self._needs_reload:
            return
        if time.time() < self._backoff_until:
            return self._guard_stale(age)
        try:
            self.load()
        except TokenStoreUnavailable:
            self._guard_stale(time.time() - self._cache_time)

    def _guard_stale(self, age: float) -> None:
        if self.fail_mode == "fail_open":
            return
        if age <= self.CACHE_TTL + self.stale_grace:
            return
        raise TokenStoreUnavailable(
            f"magasin de tokens injoignable depuis {age:.0f}s, "
            f"au-delà de la fenêtre de {self.stale_grace}s : accès refusé "
            f"({self._last_error or 'cause inconnue'})"
        )

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

    @_serialise
    def create(self, client_name: str, permissions: list, allowed_resources: list = None,
               expires_in_days: int = 90, email: str = "", policy_id: str = "") -> dict:
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
            "policy_id": policy_id,
            "email": email,
            "created_at": now.isoformat(),
            "expires_at": expires_at,
            "revoked": False,
        }

        # Recharger avant de muter : écrire depuis un cache périmé écraserait
        # les tokens créés entre-temps par une autre instance.
        self.load()
        self._tokens[token_hash] = token_info
        # Un token que le magasin n'a pas accepté ne doit pas survivre ici :
        # il serait valide sur cette instance et inconnu de toutes les autres.
        _persist_or_restore(self, token_hash, None)

        return {"raw_token": raw_token, **token_info}

    def list_all(self) -> list:
        """Liste tous les tokens (sans les hash complets)."""
        self._maybe_refresh()
        return [
            {
                "client_name": t["client_name"],
                "permissions": t["permissions"],
                "policy_id": t.get("policy_id", ""),
                "email": t.get("email", ""),
                "hash_prefix": t["hash"][:12],
                "expires_at": t.get("expires_at"),
                "revoked": t.get("revoked", False),
            }
            for t in self._tokens.values()
        ]

    @_serialise
    def revoke(self, hash_prefix: str) -> bool:
        """Révoque un token par préfixe de hash (≥8 caractères requis)."""
        # ⚠️ Min 8 chars pour éviter de révoquer le mauvais token
        # avec un préfixe trop court (collision de hash).
        if len(hash_prefix) < 8:
            return False
        self.load()
        for h, t in list(self._tokens.items()):
            if h.startswith(hash_prefix):
                previous_entry = copy.deepcopy(t)
                t["revoked"] = True
                t["revoked_at"] = __import__("datetime").datetime.now(
                    __import__("datetime").timezone.utc
                ).isoformat()
                # Une écriture ratée est ambiguë : garder le refus localement.
                _persist_or_restore(self, h, previous_entry, keep_on_failure=True)
                return True
        return False

    @_serialise
    def update(self, hash_prefix: str, permissions: list = None,
               allowed_resources: list = None, policy_id: str = None) -> dict:
        """
        Modifie les métadonnées policy_id, permissions et/ou ressources autorisées d'un token.

        Seuls les champs fournis (non None) sont modifiés.
        ⚠️ hash_prefix doit faire ≥ 8 caractères (anti-collision).
        """
        if len(hash_prefix) < 8:
            return {"status": "error", "message": "Hash prefix trop court (min 8 caractères)"}

        self.load()
        for h, t in list(self._tokens.items()):
            if h.startswith(hash_prefix):
                previous_entry = copy.deepcopy(t)
                updated_fields = []
                if policy_id is not None:
                    t["policy_id"] = policy_id
                    updated_fields.append("policy_id")
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
                _persist_or_restore(self, h, previous_entry)

                return {
                    "status": "updated",
                    "client_name": t.get("client_name", "?"),
                    "hash_prefix": h[:12],
                    "updated_fields": updated_fields,
                    "policy_id": t.get("policy_id", ""),
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
        self._last_error = None
        self._needs_reload: bool = False
        self._lock = threading.RLock()
        self._vault_token = get_vault_application_token(settings)

    @property
    def CACHE_TTL(self) -> int:
        return _configured_cache_ttl(self.settings)

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._vault_token}"}

    def _secret_url(self) -> str:
        base = self.settings.mcp_vault_url.rstrip("/")
        path = quote(self.settings.mcp_vault_token_store_path, safe="")
        return f"{base}/admin/api/vaults/{self.settings.mcp_vault_id}/secrets/{path}"

    def load(self):
        """Charge les tokens depuis MCP Vault, en retenant la cause d'un échec.

        Le statut d'administration doit pouvoir dire que le magasin n'est pas
        joignable ; sans cette trace il annoncerait le contraire.
        """
        try:
            self._load()
        except TokenStoreUnavailable as exc:
            self._last_error = str(exc)
            raise
        self._last_error = None
        self._needs_reload = False

    def _load(self):
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
            raise TokenStoreUnavailable("MCP Vault unavailable: timeout while loading token store") from exc
        except httpx.HTTPError as exc:
            self._tokens = {}
            self._cache_time = time.time()
            raise TokenStoreUnavailable(f"MCP Vault unavailable while loading token store: {exc}") from exc

        if resp.status_code == 404:
            self._tokens = {}
            self._cache_time = time.time()
            return

        if resp.status_code in (401, 403):
            self._tokens = {}
            self._cache_time = time.time()
            raise TokenStoreUnavailable(f"MCP Vault permission denied while loading token store (HTTP {resp.status_code})")

        if resp.status_code >= 500:
            self._tokens = {}
            self._cache_time = time.time()
            raise TokenStoreUnavailable(f"MCP Vault unavailable while loading token store (HTTP {resp.status_code})")

        if resp.status_code >= 300:
            self._tokens = {}
            self._cache_time = time.time()
            raise TokenStoreUnavailable(f"MCP Vault error while loading token store (HTTP {resp.status_code})")

        try:
            payload = resp.json()
        except ValueError as exc:
            self._tokens = {}
            self._cache_time = time.time()
            raise TokenStoreUnavailable(
                f"MCP Vault token store payload is not valid JSON: {exc}"
            ) from exc
        data = payload.get("data", {}) if isinstance(payload, dict) else {}
        tokens = data.get("tokens", []) if isinstance(data, dict) else []
        if tokens is None:
            tokens = []
        if not isinstance(tokens, list):
            raise TokenStoreUnavailable("MCP Vault token store payload is invalid: data.tokens must be a list")

        self._tokens = {t["hash"]: t for t in tokens if isinstance(t, dict) and "hash" in t}
        self._cache_time = time.time()

    def _maybe_refresh(self):
        """Rafraîchit le cache si le TTL est dépassé, ou s'il est douteux."""
        if time.time() - self._cache_time > self.CACHE_TTL or self._needs_reload:
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
        """Écrit dans MCP Vault, en retenant la cause d'un échec."""
        try:
            self.__save()
        except TokenStoreUnavailable as exc:
            self._last_error = str(exc)
            raise
        self._last_error = None

    def __save(self):
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
            raise TokenStoreUnavailable("MCP Vault unavailable: timeout while saving token store") from exc
        except httpx.HTTPError as exc:
            raise TokenStoreUnavailable(f"MCP Vault unavailable while saving token store: {exc}") from exc

        if resp.status_code in (401, 403):
            raise TokenStoreUnavailable(f"MCP Vault permission denied while saving token store (HTTP {resp.status_code})")
        if resp.status_code >= 500:
            raise TokenStoreUnavailable(f"MCP Vault unavailable while saving token store (HTTP {resp.status_code})")
        if resp.status_code >= 300:
            raise TokenStoreUnavailable(f"MCP Vault error while saving token store (HTTP {resp.status_code})")

    @_serialise
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
        _persist_or_restore(self, token_hash, None)

        return {"raw_token": raw_token, **token_info}

    @_serialise
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
        previous_entry = copy.deepcopy(token)
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

        _persist_or_restore(self, target_hash, previous_entry)

        return {
            "status": "updated",
            "client_name": token.get("client_name", "?"),
            "hash_prefix": target_hash[:12],
            "updated_fields": updated_fields,
            "policy_id": token.get("policy_id", ""),
            "permissions": token.get("permissions", []),
            "allowed_resources": token.get("allowed_resources", []),
        }

    @_serialise
    def revoke(self, hash_prefix: str) -> bool:
        """Révoque un token par préfixe de hash dans MCP Vault."""
        if len(hash_prefix) < 8:
            return False

        self.load()

        from datetime import datetime, timezone
        for h, t in list(self._tokens.items()):
            if h.startswith(hash_prefix):
                previous_entry = copy.deepcopy(t)
                t["revoked"] = True
                t["revoked_at"] = datetime.now(timezone.utc).isoformat()
                # Une écriture ratée est ambiguë : garder le refus localement.
                _persist_or_restore(self, h, previous_entry, keep_on_failure=True)
                return True
        return False

    def count(self) -> int:
        """Nombre de tokens actifs (non révoqués)."""
        return sum(1 for t in self._tokens.values() if not t.get("revoked", False))


# Backward-compatible alias for existing imports/tests.
TokenStore = S3TokenStore
