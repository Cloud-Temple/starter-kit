# -*- coding: utf-8 -*-
"""Tests for VaultTokenStore load/read behavior using mocked HTTP."""

import hashlib
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "boilerplate" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from mon_service.config import get_settings  # noqa: E402
from mon_service.auth import token_store  # noqa: E402
from mon_service.auth.token_store import VaultTokenStore  # noqa: E402


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = str(self._payload)

    def json(self):
        return self._payload


@pytest.fixture(autouse=True)
def reset_env(monkeypatch):
    for key in [
        "TOKEN_STORE_BACKEND",
        "MCP_VAULT_URL",
        "MCP_VAULT_TOKEN_FILE",
        "MCP_VAULT_TOKEN",
        "MCP_VAULT_ID",
        "MCP_VAULT_TOKEN_STORE_PATH",
        "MCP_VAULT_TIMEOUT",
    ]:
        monkeypatch.delenv(key, raising=False)
    get_settings.cache_clear()
    token_store._token_store = None
    yield
    get_settings.cache_clear()
    token_store._token_store = None


def configure_vault_env(monkeypatch):
    monkeypatch.setenv("TOKEN_STORE_BACKEND", "vault")
    monkeypatch.setenv("MCP_VAULT_URL", "https://vault.example.test")
    monkeypatch.setenv("MCP_VAULT_TOKEN", "vault-token")
    monkeypatch.setenv("MCP_VAULT_ID", "my-vault")
    monkeypatch.setenv("MCP_VAULT_TOKEN_STORE_PATH", "token-store/tokens.json")
    get_settings.cache_clear()
    return get_settings()


def test_vault_load_reads_tokens_and_ignores_metadata(monkeypatch):
    settings = configure_vault_env(monkeypatch)
    raw = "client-token"
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    expires = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    calls = []

    def fake_get(url, headers=None, timeout=None):
        calls.append({"url": url, "headers": headers, "timeout": timeout})
        return FakeResponse(200, {
            "status": "ok",
            "vault_id": "my-vault",
            "path": "token-store/tokens.json",
            "data": {
                "_type": "custom",
                "_tags": "",
                "_favorite": "false",
                "tokens": [{
                    "hash": token_hash,
                    "client_name": "agent",
                    "permissions": ["read"],
                    "allowed_resources": [],
                    "policy_id": "readonly",
                    "email": "agent@example.test",
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "expires_at": expires,
                    "revoked": False,
                }],
            },
        })

    monkeypatch.setattr("httpx.get", fake_get)
    store = VaultTokenStore(settings)
    store.load()

    assert calls[0]["url"] == "https://vault.example.test/admin/api/vaults/my-vault/secrets/token-store%2Ftokens.json"
    assert calls[0]["headers"] == {"Authorization": "Bearer vault-token"}
    assert calls[0]["timeout"] == 5.0
    assert store.count() == 1
    assert store.get_by_hash(token_hash)["client_name"] == "agent"
    listed = store.list_all()
    assert listed[0]["policy_id"] == "readonly"
    assert listed[0]["hash_prefix"] == token_hash[:12]


def test_vault_load_404_means_empty_store(monkeypatch):
    settings = configure_vault_env(monkeypatch)
    monkeypatch.setattr("httpx.get", lambda *a, **kw: FakeResponse(404, {"status": "error"}))

    store = VaultTokenStore(settings)
    store.load()

    assert store.count() == 0
    assert store.list_all() == []


def test_vault_load_403_fails_closed(monkeypatch):
    settings = configure_vault_env(monkeypatch)
    monkeypatch.setattr("httpx.get", lambda *a, **kw: FakeResponse(403, {"status": "error"}))

    store = VaultTokenStore(settings)
    with pytest.raises(RuntimeError, match="permission denied"):
        store.load()
    assert store.count() == 0


def test_vault_load_rejects_invalid_tokens_payload(monkeypatch):
    settings = configure_vault_env(monkeypatch)
    monkeypatch.setattr("httpx.get", lambda *a, **kw: FakeResponse(200, {"data": {"tokens": "invalid"}}))

    store = VaultTokenStore(settings)
    with pytest.raises(RuntimeError, match="data.tokens must be a list"):
        store.load()


def test_init_token_store_vault_loads_store(monkeypatch):
    configure_vault_env(monkeypatch)
    monkeypatch.setattr("httpx.get", lambda *a, **kw: FakeResponse(404, {"status": "error"}))

    token_store.init_token_store()

    assert isinstance(token_store.get_token_store(), VaultTokenStore)
    assert token_store.get_token_store().count() == 0
