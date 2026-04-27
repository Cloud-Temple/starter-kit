# -*- coding: utf-8 -*-
"""HTTP integration tests for VaultTokenStore against a local fake MCP Vault.

This is more concrete than monkeypatching httpx.get/post: VaultTokenStore performs
real HTTP requests to a local HTTP server that emulates the MCP Vault admin API
shape used by the starter-kit.
"""

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "boilerplate" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from mon_service.config import get_settings  # noqa: E402
from mon_service.auth import token_store  # noqa: E402
from mon_service.auth.token_store import VaultTokenStore  # noqa: E402


class FakeVaultState:
    def __init__(self):
        self.secrets = {}
        self.posts = []
        self.gets = []


class FakeVaultHandler(BaseHTTPRequestHandler):
    server_version = "FakeMCPVault/0.1"

    def log_message(self, fmt, *args):  # pragma: no cover - keep tests quiet
        return

    @property
    def state(self):
        return self.server.state

    def _json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self):
        return self.headers.get("Authorization") == "Bearer vault-token"

    def do_GET(self):
        self.state.gets.append(self.path)
        if not self._authorized():
            return self._json(403, {"status": "error", "message": "forbidden"})

        prefix = "/admin/api/vaults/my-vault/secrets/"
        if not self.path.startswith(prefix):
            return self._json(404, {"status": "error", "message": "not found"})

        encoded_path = self.path[len(prefix):]
        secret_path = unquote(encoded_path)
        if secret_path not in self.state.secrets:
            return self._json(404, {"status": "error", "message": "secret not found"})

        return self._json(200, {
            "status": "ok",
            "vault_id": "my-vault",
            "path": secret_path,
            "data": {
                "_type": "custom",
                "_tags": "",
                "_favorite": "false",
                **self.state.secrets[secret_path],
            },
            "version": 1,
            "created_time": "2026-01-01T00:00:00Z",
        })

    def do_POST(self):
        if not self._authorized():
            return self._json(403, {"status": "error", "message": "forbidden"})
        if self.path != "/admin/api/vaults/my-vault/secrets":
            return self._json(404, {"status": "error", "message": "not found"})

        size = int(self.headers.get("content-length", "0"))
        payload = json.loads(self.rfile.read(size).decode("utf-8"))
        self.state.posts.append(payload)
        self.state.secrets[payload["path"]] = payload.get("data", {})
        return self._json(200, {"status": "ok", "path": payload["path"]})


@pytest.fixture
def fake_vault_server():
    state = FakeVaultState()
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeVaultHandler)
    server.state = state
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}", state
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


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


def configure_vault(monkeypatch, url):
    monkeypatch.setenv("TOKEN_STORE_BACKEND", "vault")
    monkeypatch.setenv("MCP_VAULT_URL", url)
    monkeypatch.setenv("MCP_VAULT_TOKEN", "vault-token")
    monkeypatch.setenv("MCP_VAULT_ID", "my-vault")
    monkeypatch.setenv("MCP_VAULT_TOKEN_STORE_PATH", "token-store/tokens.json")
    get_settings.cache_clear()
    return get_settings()


def test_vault_token_store_roundtrip_against_local_http_server(monkeypatch, fake_vault_server):
    vault_url, state = fake_vault_server
    settings = configure_vault(monkeypatch, vault_url)
    store = VaultTokenStore(settings)

    # Missing Vault secret behaves as empty store.
    store.load()
    assert store.count() == 0

    created = store.create(
        client_name="http-agent",
        permissions=["read", "write"],
        allowed_resources=["resource-a"],
        expires_in_days=1,
        email="http-agent@example.test",
        policy_id="policy-a",
    )
    assert created["raw_token"]
    assert created["hash"]
    assert state.posts[-1]["path"] == "token-store/tokens.json"
    assert state.secrets["token-store/tokens.json"]["tokens"][0]["client_name"] == "http-agent"
    assert state.secrets["token-store/tokens.json"]["tokens"][0]["policy_id"] == "policy-a"

    listed = store.list_all()
    hash_prefix = listed[0]["hash_prefix"]

    updated = store.update(
        hash_prefix,
        policy_id="policy-b",
        permissions=["read"],
        allowed_resources=["resource-b"],
    )
    assert updated["status"] == "updated"
    persisted = state.secrets["token-store/tokens.json"]["tokens"][0]
    assert persisted["policy_id"] == "policy-b"
    assert persisted["permissions"] == ["read"]
    assert persisted["allowed_resources"] == ["resource-b"]

    assert store.revoke(hash_prefix) is True
    persisted = state.secrets["token-store/tokens.json"]["tokens"][0]
    assert persisted["revoked"] is True
    assert persisted["revoked_at"]


def test_init_token_store_uses_vault_backend_against_local_http_server(monkeypatch, fake_vault_server):
    vault_url, _state = fake_vault_server
    configure_vault(monkeypatch, vault_url)

    token_store.init_token_store()

    assert isinstance(token_store.get_token_store(), VaultTokenStore)
    assert token_store.get_token_store().count() == 0
