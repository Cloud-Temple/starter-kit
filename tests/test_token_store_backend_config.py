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


def test_vault_backend_is_explicitly_not_implemented_yet(monkeypatch):
    monkeypatch.setenv("TOKEN_STORE_BACKEND", "vault")
    get_settings.cache_clear()

    with pytest.raises(NotImplementedError, match="VaultTokenStore comes next"):
        token_store.init_token_store()


def test_unsupported_backend_fails_explicitly(monkeypatch):
    monkeypatch.setenv("TOKEN_STORE_BACKEND", "bad")
    get_settings.cache_clear()

    with pytest.raises(ValueError, match="Unsupported TOKEN_STORE_BACKEND"):
        token_store.init_token_store()
