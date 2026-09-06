# -*- coding: utf-8 -*-
"""
Docker Compose e2e against the Vault CI stack (WAF + MCP + fake MCP Vault).

This test assumes the stack is already running, typically via:

    docker compose -f boilerplate/docker-compose.vault-ci.yml up -d --build
    RUN_COMPOSE_E2E=1 python -m pytest tests/e2e/test_vault_compose.py -q

It validates a concrete end-to-end flow:
- admin REST health through WAF reports TOKEN_STORE_BACKEND=vault
- create token through /admin/api/tokens (VaultTokenStore backed by fake Vault)
- token is persisted in the fake Vault with a live-like response shape
- call MCP system_whoami using the created token
- revoke token through /admin/api/tokens/{hash_prefix}
- verify revoked token is no longer accepted by system_whoami
"""

import json
import os
import time
from urllib.parse import quote

import httpx
import pytest

pytestmark = pytest.mark.e2e

BASE_URL = os.environ.get("E2E_BASE_URL", "http://localhost:8083").rstrip("/")
FAKE_VAULT_URL = os.environ.get("E2E_FAKE_VAULT_URL", "http://localhost:18080").rstrip("/")
BOOTSTRAP = os.environ.get("E2E_ADMIN_BOOTSTRAP_KEY", "test-ci-bootstrap-key")
VAULT_TOKEN = os.environ.get("E2E_FAKE_VAULT_TOKEN", "vault-token")
VAULT_ID = os.environ.get("E2E_MCP_VAULT_ID", "test-vault")
VAULT_PATH = os.environ.get("E2E_MCP_VAULT_TOKEN_STORE_PATH", "token-store/tokens.json")
RUN_E2E = os.environ.get("RUN_COMPOSE_E2E") == "1"


def wait_for_health(timeout=90):
    deadline = time.time() + timeout
    last_error = None
    while time.time() < deadline:
        try:
            r = httpx.get(f"{BASE_URL}/health", timeout=5)
            if r.status_code == 200:
                return
            last_error = f"HTTP {r.status_code}: {r.text[:120]}"
        except Exception as exc:  # pragma: no cover - diagnostic only
            last_error = str(exc)
        time.sleep(2)
    raise AssertionError(f"Service not healthy at {BASE_URL}: {last_error}")


def admin_headers(token=BOOTSTRAP):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def vault_headers():
    return {"Authorization": f"Bearer {VAULT_TOKEN}"}


def read_fake_vault_secret():
    encoded_path = quote(VAULT_PATH, safe="")
    r = httpx.get(
        f"{FAKE_VAULT_URL}/admin/api/vaults/{VAULT_ID}/secrets/{encoded_path}",
        headers=vault_headers(),
        timeout=10,
    )
    assert r.status_code == 200, r.text
    return r.json()


async def call_mcp_tool(tool_name, arguments, token):
    import httpx2
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async with httpx2.AsyncClient(
        headers={"Authorization": f"Bearer {token}"},
        timeout=httpx2.Timeout(30, read=30),
    ) as http_client:
        async with streamable_http_client(f"{BASE_URL}/mcp", http_client=http_client) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments)
                text = ""
                if result.content:
                    text = getattr(result.content[0], "text", "") or ""
                return json.loads(text)


@pytest.mark.skipif(not RUN_E2E, reason="Set RUN_COMPOSE_E2E=1 to run docker-compose e2e")
@pytest.mark.asyncio
async def test_vault_tokenstore_end_to_end():
    wait_for_health()

    # Admin health through WAF reports VaultTokenStore status without secrets.
    r = httpx.get(f"{BASE_URL}/admin/api/health", headers=admin_headers(), timeout=10)
    assert r.status_code == 200, r.text
    health = r.json()
    assert health["status"] == "ok"
    assert health["s3_configured"] is False
    assert health["token_store"]["backend"] == "vault"
    assert health["token_store"]["configured"] is True
    assert health["token_store"]["vault_id"] == VAULT_ID
    assert health["token_store"]["path"] == VAULT_PATH
    assert "token" not in health["token_store"]

    # Create a real Vault-backed token via admin REST.
    r = httpx.post(
        f"{BASE_URL}/admin/api/tokens",
        headers=admin_headers(),
        json={
            "client_name": "ci-vault-e2e-agent",
            "permissions": ["read", "write"],
            "allowed_resources": [],
            "email": "ci-vault@example.test",
            "policy_id": "vault-e2e-policy",
            "expires_in_days": 1,
        },
        timeout=10,
    )
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["status"] == "created"
    raw_token = created["raw_token"]
    hash_prefix = created["hash"][:12]

    # Token is persisted in the fake Vault with live-like metadata and no raw token.
    persisted = read_fake_vault_secret()
    assert persisted["status"] == "ok"
    assert persisted["vault_id"] == VAULT_ID
    assert persisted["path"] == VAULT_PATH
    assert persisted["data"]["_type"] == "custom"
    assert persisted["data"]["_tags"] == ""
    assert persisted["data"]["_favorite"] == "false"
    assert len(persisted["data"]["tokens"]) == 1
    persisted_token = persisted["data"]["tokens"][0]
    assert persisted_token["client_name"] == "ci-vault-e2e-agent"
    assert persisted_token["permissions"] == ["read", "write"]
    assert persisted_token["policy_id"] == "vault-e2e-policy"
    assert "raw_token" not in persisted_token

    # Token can authenticate MCP tool system_whoami.
    whoami = await call_mcp_tool("system_whoami", {}, raw_token)
    assert whoami["status"] == "ok"
    assert whoami["client_name"] == "ci-vault-e2e-agent"
    assert whoami["permissions"] == ["read", "write"]

    # Revoke via admin REST.
    r = httpx.delete(f"{BASE_URL}/admin/api/tokens/{hash_prefix}", headers=admin_headers(), timeout=10)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ok"

    # Revoked token should no longer authenticate.
    whoami = await call_mcp_tool("system_whoami", {}, raw_token)
    assert whoami["status"] == "error"
    assert "authentification" in whoami["message"].lower()

    persisted = read_fake_vault_secret()
    assert persisted["data"]["tokens"][0]["revoked"] is True
