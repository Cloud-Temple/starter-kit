# -*- coding: utf-8 -*-
"""Configuration du service MCP via pydantic-settings."""

from functools import lru_cache
from pydantic import model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Configuration chargée depuis les variables d'env / .env."""

    # --- Serveur MCP ---
    mcp_server_name: str = "mon-mcp-service"
    mcp_server_host: str = "0.0.0.0"
    mcp_server_port: int = 8002
    mcp_server_debug: bool = False

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

    # --- Vos services métier (exemples) ---
    # database_url: str = "postgresql://user:pass@db:5432/mydb"
    # redis_url: str = "redis://redis:6379/0"
    # external_api_key: str = ""
    # external_api_url: str = "https://api.example.com"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    @model_validator(mode="after")
    def _validate_settings(self) -> "Settings":
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

        return self


@lru_cache()
def get_settings() -> Settings:
    """Singleton Settings (cached)."""
    return Settings()
