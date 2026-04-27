# -*- coding: utf-8 -*-
"""Tests for TokenStore backend factory configuration."""

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "boilerplate" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from mon_service.config import get_settings  # noqa: E402
from mon_service.auth import token_store  # noqa: E402


@pytest.fixture(autouse=True)
def reset_settings_and_store(monkeypatch):
    for key in [
        "TOKEN_STORE_BACKEND",
        "S3_ENDPOINT_URL",
        "S3_BUCKET_NAME",
        "MCP_VAULT_TOKEN_FILE",
        "MCP_VAULT_TOKEN",
        "MCP_VAULT_ID",
    ]:
        monkeypatch.delenv(key, raising=False)
    get_settings.cache_clear()
    token_store._token_store = None
    yield
    get_settings.cache_clear()
    token_store._token_store = None


def test_default_token_store_backend_is_s3():
    settings = get_settings()
    assert settings.token_store_backend == "s3"
    assert settings.token_store_cache_ttl == 300
    assert settings.token_store_fail_mode == "fail_close"


def test_s3_backend_without_s3_config_keeps_bootstrap_only_mode():
    token_store.init_token_store()
    assert token_store.get_token_store() is None


def test_vault_backend_is_explicitly_validated_before_implementation(monkeypatch):
    monkeypatch.setenv("TOKEN_STORE_BACKEND", "vault")
    get_settings.cache_clear()

    with pytest.raises(ValueError, match="MCP_VAULT_ID is required"):
        token_store.init_token_store()


def test_unsupported_backend_fails_explicitly(monkeypatch):
    monkeypatch.setenv("TOKEN_STORE_BACKEND", "bad")
    get_settings.cache_clear()

    with pytest.raises(ValueError, match="Unsupported TOKEN_STORE_BACKEND"):
        token_store.init_token_store()


def test_vault_token_file_has_priority_over_env_token(monkeypatch, tmp_path):
    token_file = tmp_path / "vault-token"
    token_file.write_text("token-from-file")
    monkeypatch.setenv("MCP_VAULT_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("MCP_VAULT_TOKEN", "token-from-env")
    get_settings.cache_clear()

    settings = get_settings()
    assert token_store.get_vault_application_token(settings) == "token-from-file"


def test_vault_token_falls_back_to_env_token(monkeypatch):
    monkeypatch.setenv("MCP_VAULT_TOKEN", "token-from-env")
    get_settings.cache_clear()

    settings = get_settings()
    assert token_store.get_vault_application_token(settings) == "token-from-env"


def test_vault_backend_requires_vault_id(monkeypatch):
    monkeypatch.setenv("TOKEN_STORE_BACKEND", "vault")
    monkeypatch.setenv("MCP_VAULT_TOKEN", "token-from-env")
    get_settings.cache_clear()

    with pytest.raises(ValueError, match="MCP_VAULT_ID is required"):
        token_store.init_token_store()


def test_vault_backend_requires_application_token(monkeypatch):
    monkeypatch.setenv("TOKEN_STORE_BACKEND", "vault")
    monkeypatch.setenv("MCP_VAULT_ID", "my-vault")
    get_settings.cache_clear()

    with pytest.raises(ValueError, match="MCP_VAULT_TOKEN_FILE or MCP_VAULT_TOKEN"):
        token_store.init_token_store()


def test_vault_backend_valid_config_is_accepted(monkeypatch):
    monkeypatch.setenv("TOKEN_STORE_BACKEND", "vault")
    monkeypatch.setenv("MCP_VAULT_ID", "my-vault")
    monkeypatch.setenv("MCP_VAULT_TOKEN", "token-from-env")
    get_settings.cache_clear()

    settings = get_settings()
    assert token_store.validate_vault_settings(settings) == "token-from-env"
