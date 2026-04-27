# -*- coding: utf-8 -*-
"""Configuration du service MCP via pydantic-settings."""

from functools import lru_cache
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

    # --- Vos services métier (exemples) ---
    # database_url: str = "postgresql://user:pass@db:5432/mydb"
    # redis_url: str = "redis://redis:6379/0"
    # external_api_key: str = ""
    # external_api_url: str = "https://api.example.com"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


@lru_cache()
def get_settings() -> Settings:
    """Singleton Settings (cached)."""
    return Settings()
