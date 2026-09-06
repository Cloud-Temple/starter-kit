# -*- coding: utf-8 -*-
"""Configuration du service MCP via pydantic-settings."""

from functools import lru_cache
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Configuration chargée depuis les variables d'env / .env."""

    # --- Serveur MCP ---
    mcp_server_name: str = "mon-mcp-service"
    mcp_server_host: str = "0.0.0.0"
    mcp_server_port: int = 8002
    mcp_server_debug: bool = False
    # Le déploiement fournit les valeurs publiques exactes au format JSON,
    # par exemple MCP_ALLOWED_HOSTS='["mcp.example.fr"]'. Elles n'ont pas de
    # défaut : un service publiable sans politique Host/Origin échoue au boot.
    mcp_allowed_hosts: list[str] = Field(default_factory=list)
    mcp_allowed_origins: list[str] = Field(default_factory=list)
    mcp_max_request_body_size: int = 4 * 1024 * 1024

    # --- Branding admin UI ---
    # Valeurs: ct (Cloud Temple), dgy (Dragonfly), isec (Intrinsec)
    mcp_brand: str = "ct"

    # --- Auth ---
    admin_bootstrap_key: str = "change_me_in_production"

    # --- Token Store backend ---
    # Valeurs: s3 (défaut), vault (à venir)
    token_store_backend: str = "s3"
    token_store_cache_ttl: int = 300
    token_store_fail_mode: str = "fail_close"

    # --- MCP Vault Token Store (si TOKEN_STORE_BACKEND=vault) ---
    mcp_vault_url: str = "https://vault.mcp.cloud-temple.app"
    mcp_vault_token_file: str = ""
    mcp_vault_token: str = ""
    mcp_vault_id: str = ""
    mcp_vault_token_store_path: str = "token-store/tokens.json"
    mcp_vault_timeout: float = 5.0

    # --- S3 Token Store (optionnel — si vide, tokens en mémoire uniquement) ---
    s3_endpoint_url: str = ""
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    s3_bucket_name: str = ""
    s3_region_name: str = "fr1"
    # Cloud Temple / Dell ECS expects SigV2 for object data operations.
    # Use "s3v4" for providers that require AWS Signature V4.
    s3_signature_version: str = "s3"
    s3_addressing_style: str = "path"

    # --- Proxy HTTP sortant (optionnel) ---
    # Injecté MANUELLEMENT dans httpx/boto3 — ne pose pas HTTP_PROXY dans l'env
    # pour éviter d'affecter les libs tierces qui liraient automatiquement cette var.
    proxy_url: str = ""

    # --- JWT / OIDC (optionnel) ---
    # Activé seulement si les 3 variables sont toutes non-vides.
    # Compatible Keycloak, Entra ID (Azure AD), Okta, etc.
    # Le fichier JWKS doit être présent localement (validation offline — pas de fetch réseau).
    jwt_issuer: str = ""    # ex: https://sso.example.com/realms/myrealm
    jwt_audience: str = ""  # ex: mon-mcp-service
    jwks_file: str = ""     # ex: /app/certs/jwks.json  (chemin absolu dans le container)

    # --- Mission JWT (mcp-mission mission_token, ES256 via JWKS dynamique) ---
    # Cf. starter-kit#14 + mcp-mission ARCHITECTURE §17.10/§17.12.
    # AuthMissionJWTMiddleware valide un mission_token signé ES256 émis par
    # mcp-mission. Activé seulement si STARTER_KIT_AUTH_MODE != "bearer".
    #
    # Modes :
    #   bearer      → middleware inactif (auth Bearer legacy uniquement) — DÉFAUT
    #   jwt         → SEUL le mission_token JWT est accepté (fail-close, P1/P2 vault/teleport)
    #   dual-stack  → JWT accepté ; à défaut, fallback Bearer legacy (P3-P7)
    starter_kit_auth_mode: str = "bearer"

    # Identifiant d'instance unique de CE MCP (ex: "vault-prod-eu-tenant-acme").
    # Le middleware refuse tout token dont `aud` ne le contient pas (T03/T11).
    mcp_instance_id: str = ""

    # Genre (kind) de ce MCP dans le claim `component_id` du token
    # (ex: "vault", "teleport", "live_memory"). Le middleware vérifie
    # `component_id[<kind>] == MCP_INSTANCE_ID` (détecte les misconfigurations).
    mcp_component_kind: str = ""

    # URL du JWKS public de mcp-mission (ex:
    # "https://mcp-mission.internal/.well-known/jwks.json").
    mcp_mission_jwks_url: str = ""

    # TTL du cache JWKS en secondes (défaut 5 min, cf. §17.10).
    jwks_cache_ttl_seconds: int = 300

    # Skew toléré sur `iat` (anti-rejeu d'horloge avancée), en secondes.
    mission_jwt_iat_leeway_seconds: int = 60

    # --- Vos services métier (exemples) ---
    # database_url: str = "postgresql://user:pass@db:5432/mydb"
    # redis_url: str = "redis://redis:6379/0"
    # external_api_key: str = ""
    # external_api_url: str = "https://api.example.com"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    @model_validator(mode="after")
    def _validate_settings(self) -> "Settings":
        # --- Sécurité du transport MCP v2 ---
        if not self.mcp_allowed_hosts:
            raise ValueError("MCP_ALLOWED_HOSTS ne peut pas être vide (protection DNS rebinding).")
        if not self.mcp_allowed_origins:
            raise ValueError("MCP_ALLOWED_ORIGINS ne peut pas être vide (protection Origin).")
        if self.mcp_max_request_body_size <= 0:
            raise ValueError("MCP_MAX_REQUEST_BODY_SIZE doit être strictement positif.")

        # --- Validation PROXY_URL ---
        if self.proxy_url and not (
            self.proxy_url.startswith("http://")
            or self.proxy_url.startswith("https://")
        ):
            raise ValueError(
                f"PROXY_URL doit commencer par http:// ou https:// "
                f"(valeur actuelle : {self.proxy_url!r})"
            )

        # --- Validation JWT : trio all-or-nothing ---
        # Si l'une des 3 vars JWT est définie, les 2 autres sont obligatoires.
        jwt_fields = {
            "JWT_ISSUER": self.jwt_issuer,
            "JWT_AUDIENCE": self.jwt_audience,
            "JWKS_FILE": self.jwks_file,
        }
        set_fields = {k for k, v in jwt_fields.items() if v}
        if set_fields and len(set_fields) != len(jwt_fields):
            missing = sorted(set(jwt_fields) - set_fields)
            raise ValueError(
                f"Configuration JWT incomplète — variables manquantes : {missing}. "
                f"JWT_ISSUER, JWT_AUDIENCE et JWKS_FILE doivent être toutes définies ou toutes vides."
            )

        # --- Validation Mission JWT (mission_token mcp-mission) ---
        # Composant de sécurité : on REFUSE de démarrer avec une config
        # incohérente plutôt que de fail-open silencieusement.
        valid_modes = {"bearer", "jwt", "dual-stack"}
        if self.starter_kit_auth_mode not in valid_modes:
            raise ValueError(
                f"STARTER_KIT_AUTH_MODE invalide : {self.starter_kit_auth_mode!r}. "
                f"Valeurs autorisées : {sorted(valid_modes)}."
            )

        if self.starter_kit_auth_mode != "bearer":
            # Les variables mission_token deviennent obligatoires : sans elles,
            # le middleware ne pourrait pas valider `aud`/`component_id` ni
            # récupérer le JWKS — il fail-open de facto, ce qui est interdit.
            mission_required = {
                "MCP_INSTANCE_ID": self.mcp_instance_id,
                "MCP_COMPONENT_KIND": self.mcp_component_kind,
                "MCP_MISSION_JWKS_URL": self.mcp_mission_jwks_url,
            }
            missing = sorted(k for k, v in mission_required.items() if not v)
            if missing:
                raise ValueError(
                    f"STARTER_KIT_AUTH_MODE={self.starter_kit_auth_mode!r} mais variables "
                    f"mission_token manquantes : {missing}. "
                    f"MCP_INSTANCE_ID, MCP_COMPONENT_KIND et MCP_MISSION_JWKS_URL sont "
                    f"obligatoires en mode 'jwt' ou 'dual-stack' (fail-close)."
                )
            # Schéma du JWKS : refus de tout ce qui n'est pas HTTP(S) (anti file://,
            # data://, etc.). `http://` n'est toléré QUE pour un hôte interne
            # (loopback / nom Docker court) — sinon MITM/cache-poisoning du JWKS
            # = acceptation de tokens forgés (T09). HTTPS exigé hors réseau interne.
            from urllib.parse import urlparse

            parsed = urlparse(self.mcp_mission_jwks_url)
            if parsed.scheme not in ("http", "https"):
                raise ValueError(
                    f"MCP_MISSION_JWKS_URL doit utiliser https:// (ou http:// pour un hôte "
                    f"interne) — schéma reçu : {parsed.scheme!r}."
                )
            if parsed.scheme == "http":
                host = (parsed.hostname or "").lower()
                # Loopback explicite, OU nom de service Docker court (un seul
                # label DNS : ni point — FQDN/IPv4 — ni deux-points — IPv6).
                # Cette dernière condition exclut les IPv6 routables (ex.
                # [2001:db8::1]) qui n'ont pas de point mais des deux-points.
                is_loopback = host in ("localhost", "127.0.0.1", "::1")
                is_short_service_name = bool(host) and "." not in host and ":" not in host
                if not (is_loopback or is_short_service_name):
                    raise ValueError(
                        "MCP_MISSION_JWKS_URL en http:// n'est autorisé que pour un hôte "
                        f"interne (loopback ou nom de service court) — hôte reçu : {host!r}. "
                        "Utilisez https:// pour un hôte routable (anti MITM du JWKS, T09)."
                    )
            if self.jwks_cache_ttl_seconds <= 0:
                raise ValueError(
                    f"JWKS_CACHE_TTL_SECONDS doit être > 0 (valeur : {self.jwks_cache_ttl_seconds})."
                )
            if self.mission_jwt_iat_leeway_seconds < 0:
                raise ValueError(
                    "MISSION_JWT_IAT_LEEWAY_SECONDS doit être >= 0 "
                    f"(valeur : {self.mission_jwt_iat_leeway_seconds})."
                )

        return self


@lru_cache()
def get_settings() -> Settings:
    """Singleton Settings (cached)."""
    return Settings()
