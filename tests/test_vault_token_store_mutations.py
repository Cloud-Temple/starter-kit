# -*- coding: utf-8 -*-
"""Tests for VaultTokenStore create/update/revoke behavior using mocked HTTP."""

import hashlib
import sys
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


def install_vault_http_mock(monkeypatch, initial_tokens=None, post_status=200):
    state = {"tokens": list(initial_tokens or [])}
    posts = []
    gets = []

    def fake_get(url, headers=None, timeout=None):
        gets.append({"url": url, "headers": headers, "timeout": timeout})
        return FakeResponse(200, {"status": "ok", "data": {"_type": "custom", "tokens": state["tokens"]}})

    def fake_post(url, headers=None, json=None, timeout=None):
        posts.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        if post_status < 300:
            state["tokens"] = list(json["data"]["tokens"])
        return FakeResponse(post_status, {"status": "ok" if post_status < 300 else "error"})

    monkeypatch.setattr("httpx.get", fake_get)
    monkeypatch.setattr("httpx.post", fake_post)
    return state, gets, posts


def test_vault_create_writes_tokens_secret(monkeypatch):
    settings = configure_vault_env(monkeypatch)
    state, gets, posts = install_vault_http_mock(monkeypatch)

    store = VaultTokenStore(settings)
    created = store.create(
        "agent",
        ["read", "write"],
        allowed_resources=["resource-a"],
        expires_in_days=7,
        email="agent@example.test",
        policy_id="readonly",
    )

    assert created["raw_token"]
    assert created["hash"]
    assert created["policy_id"] == "readonly"
    assert len(state["tokens"]) == 1
    assert len(posts) == 1
    post = posts[0]
    assert post["url"] == "https://vault.example.test/admin/api/vaults/my-vault/secrets"
    assert post["headers"]["Authorization"] == "Bearer vault-token"
    assert post["json"]["path"] == "token-store/tokens.json"
    assert post["json"]["type"] == "custom"
    assert post["json"]["data"]["tokens"][0]["client_name"] == "agent"
    assert post["json"]["data"]["tokens"][0]["policy_id"] == "readonly"


def test_vault_update_changes_policy_permissions_and_resources(monkeypatch):
    settings = configure_vault_env(monkeypatch)
    token_hash = hashlib.sha256(b"token").hexdigest()
    install_vault_http_mock(monkeypatch, initial_tokens=[{
        "hash": token_hash,
        "client_name": "agent",
        "permissions": ["read"],
        "allowed_resources": [],
        "policy_id": "old",
        "revoked": False,
    }])

    store = VaultTokenStore(settings)
    result = store.update(
        token_hash[:12],
        policy_id="new",
        permissions=["read", "write"],
        allowed_resources=["resource-b"],
    )

    assert result["status"] == "updated"
    assert set(result["updated_fields"]) == {"policy_id", "permissions", "allowed_resources"}
    assert result["policy_id"] == "new"
    assert result["permissions"] == ["read", "write"]
    assert result["allowed_resources"] == ["resource-b"]


def test_vault_update_rejects_short_prefix(monkeypatch):
    settings = configure_vault_env(monkeypatch)
    install_vault_http_mock(monkeypatch)

    store = VaultTokenStore(settings)
    result = store.update("abc", permissions=["read"])

    assert result["status"] == "error"
    assert "Hash prefix trop court" in result["message"]


def test_vault_revoke_marks_token_revoked(monkeypatch):
    settings = configure_vault_env(monkeypatch)
    token_hash = hashlib.sha256(b"token").hexdigest()
    state, _, _ = install_vault_http_mock(monkeypatch, initial_tokens=[{
        "hash": token_hash,
        "client_name": "agent",
        "permissions": ["read"],
        "allowed_resources": [],
        "revoked": False,
    }])

    store = VaultTokenStore(settings)
    assert store.revoke(token_hash[:12]) is True
    assert state["tokens"][0]["revoked"] is True
    assert state["tokens"][0]["revoked_at"]


def test_vault_save_permission_denied_is_explicit(monkeypatch):
    settings = configure_vault_env(monkeypatch)
    install_vault_http_mock(monkeypatch, post_status=403)

    store = VaultTokenStore(settings)
    with pytest.raises(RuntimeError, match="permission denied"):
        store.create("agent", ["read"])
