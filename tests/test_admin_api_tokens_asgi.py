# -*- coding: utf-8 -*-
"""
Integration ASGI tests for the starter-kit admin token API.

These tests call the real ASGI admin API handler (handle_admin_api) with fake ASGI
scope/receive/send objects. They are more concrete than CLI unit tests while still
remaining fast and secretless:

- real admin route dispatch
- real JSON request/response handling
- real bootstrap admin auth path
- real token endpoint contracts
- in-memory fake TokenStore instead of S3
"""

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "boilerplate" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# Must be set before get_settings() is evaluated in tests.
os.environ["ADMIN_BOOTSTRAP_KEY"] = "test-bootstrap-key"
os.environ.setdefault("MCP_SERVER_NAME", "starter-kit-test")

from mon_service.admin import api as admin_api  # noqa: E402
from mon_service.config import get_settings  # noqa: E402


class FakeTokenStore:
    def __init__(self):
        self.tokens = {}

    def list_all(self):
        return list(self.tokens.values())

    def get_by_hash(self, token_hash):
        # Test fake: no non-bootstrap token is valid unless explicitly added.
        return None

    def create(self, client_name, permissions, allowed_resources=None, expires_in_days=90, email=""):
        token = {
            "raw_token": "raw-token-once",
            "hash": "abcdef1234567890",
            "hash_prefix": "abcdef123456",
            "client_name": client_name,
            "permissions": permissions,
            "allowed_resources": allowed_resources or [],
            "email": email,
            "expires_at": "2099-01-01T00:00:00+00:00" if expires_in_days else None,
            "revoked": False,
        }
        self.tokens[token["hash_prefix"]] = {
            "client_name": client_name,
            "permissions": permissions,
            "allowed_resources": allowed_resources or [],
            "email": email,
            "hash_prefix": token["hash_prefix"],
            "expires_at": token["expires_at"],
            "revoked": False,
        }
        return token

    def update(self, hash_prefix, permissions=None, allowed_resources=None):
        token = self.tokens.get(hash_prefix[:12])
        if not token:
            return {"status": "error", "message": "Token non trouvé"}
        updated = []
        if permissions is not None:
            token["permissions"] = permissions
            updated.append("permissions")
        if allowed_resources is not None:
            token["allowed_resources"] = allowed_resources
            updated.append("allowed_resources")
        if not updated:
            return {"status": "error", "message": "Aucun champ à modifier"}
        return {"status": "updated", "hash_prefix": token["hash_prefix"], "updated_fields": updated}

    def revoke(self, hash_prefix):
        token = self.tokens.get(hash_prefix[:12])
        if not token:
            return False
        token["revoked"] = True
        token["revoked_at"] = "2099-01-02T00:00:00+00:00"
        return True


@dataclass
class FakeTool:
    name: str


class FakeToolManager:
    def list_tools(self):
        return [FakeTool("system_health"), FakeTool("system_about")]


class FakeMcp:
    _tool_manager = FakeToolManager()


@pytest.fixture(autouse=True)
def clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def fake_store(monkeypatch):
    store = FakeTokenStore()
    monkeypatch.setattr(admin_api, "get_token_store", lambda: store)
    return store


async def call_admin(method, path, body=None, token="test-bootstrap-key", mcp=None):
    payload = json.dumps(body or {}).encode() if body is not None else b""
    sent = []
    received = False

    async def receive():
        nonlocal received
        if received:
            return {"type": "http.request", "body": b"", "more_body": False}
        received = True
        return {"type": "http.request", "body": payload, "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "headers": [(b"authorization", f"Bearer {token}".encode())],
    }

    await admin_api.handle_admin_api(scope, receive, send, mcp or FakeMcp())

    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    raw_body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return status, json.loads(raw_body.decode())


@pytest.mark.asyncio
async def test_admin_health_asgi(fake_store):
    status, data = await call_admin("GET", "/admin/api/health")

    assert status == 200
    assert data["status"] == "ok"
    assert data["service_name"] == "starter-kit-test"
    assert data["tools_count"] == 2
    assert data["tools"] == ["system_health", "system_about"]
    assert data["token_store"]["backend"] == "s3"
    assert data["token_store"]["tokens_count"] == 0
    assert "S3_SECRET_ACCESS_KEY" not in json.dumps(data)


@pytest.mark.asyncio
async def test_admin_token_crud_asgi(fake_store):
    status, created = await call_admin("POST", "/admin/api/tokens", {
        "client_name": "agent-asgi",
        "permissions": ["read", "write"],
        "allowed_resources": ["resource-a"],
        "email": "agent@example.test",
        "expires_in_days": 30,
    })

    assert status == 201
    assert created["status"] == "created"
    assert created["raw_token"] == "raw-token-once"
    assert created["permissions"] == ["read", "write"]

    status, listed = await call_admin("GET", "/admin/api/tokens")
    assert status == 200
    assert listed["status"] == "ok"
    assert listed["tokens"][0]["client_name"] == "agent-asgi"

    status, updated = await call_admin("PUT", "/admin/api/tokens/abcdef123456", {
        "permissions": ["read"],
        "allowed_resources": ["resource-b"],
    })
    assert status == 200
    assert updated["status"] == "updated"
    assert set(updated["updated_fields"]) == {"permissions", "allowed_resources"}

    status, revoked = await call_admin("DELETE", "/admin/api/tokens/abcdef123456")
    assert status == 200
    assert revoked["status"] == "ok"

    status, listed = await call_admin("GET", "/admin/api/tokens")
    assert listed["tokens"][0]["revoked"] is True


@pytest.mark.asyncio
async def test_admin_token_create_rejects_invalid_permissions(fake_store):
    status, data = await call_admin("POST", "/admin/api/tokens", {
        "client_name": "bad-agent",
        "permissions": ["root"],
    })

    assert status == 400
    assert data["status"] == "error"
    assert "Permissions invalides" in data["message"]


@pytest.mark.asyncio
async def test_admin_requires_valid_bearer(fake_store):
    status, data = await call_admin("GET", "/admin/api/tokens", token="wrong-token")

    assert status == 401
    assert data["status"] == "error"
