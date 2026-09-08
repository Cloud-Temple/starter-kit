# -*- coding: utf-8 -*-
"""
Docker Compose e2e against the CI stack (WAF + MCP + Moto S3).

This test assumes the stack is already running, typically via:

    docker compose -f boilerplate/docker-compose.ci.yml up -d --build
    RUN_COMPOSE_E2E=1 python -m pytest tests/e2e/test_s3_compose.py -q

It validates a concrete end-to-end flow:
- admin REST health through WAF
- create token through /admin/api/tokens (S3TokenStore backed by Moto)
- call MCP system_whoami using the created token
- revoke token through /admin/api/tokens/{hash_prefix}
- verify revoked token is no longer accepted by system_whoami
"""

import json
import os
import time

import httpx
import pytest

pytestmark = pytest.mark.e2e

BASE_URL = os.environ.get("E2E_BASE_URL", "http://localhost:8082").rstrip("/")
BOOTSTRAP = os.environ.get("E2E_ADMIN_BOOTSTRAP_KEY", "test-ci-bootstrap-key")
RUN_E2E = os.environ.get("RUN_COMPOSE_E2E") == "1"
S3_ENDPOINT_URL = os.environ.get("E2E_S3_ENDPOINT_URL", "http://127.0.0.1:5000")


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


def read_persisted_tokens():
    import boto3
    from botocore.config import Config

    client = boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT_URL,
        aws_access_key_id="ci-access-key",
        aws_secret_access_key="ci-secret-key",
        config=Config(region_name="fr1", signature_version="s3"),
    )
    response = client.get_object(Bucket="starterkit-ci", Key="_system/tokens.json")
    raw_document = response["Body"].read().decode("utf-8")
    return raw_document, json.loads(raw_document)["tokens"]


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
async def test_s3_tokenstore_end_to_end():
    wait_for_health()

    # Admin health through WAF.
    r = httpx.get(f"{BASE_URL}/admin/api/health", headers=admin_headers(), timeout=10)
    assert r.status_code == 200, r.text
    health = r.json()
    assert health["status"] == "ok"
    assert health["s3_configured"] is True

    # Coraza protects non-MCP routes and must block a concrete XSS probe.
    blocked = httpx.get(
        f"{BASE_URL}/admin/health",
        params={"probe": "<script>alert(1)</script>"},
        timeout=10,
    )
    assert blocked.status_code == 403, blocked.text

    # Create a real S3-backed token via admin REST.
    r = httpx.post(
        f"{BASE_URL}/admin/api/tokens",
        headers=admin_headers(),
        json={
            "client_name": "ci-e2e-agent",
            "permissions": ["read", "write"],
            "allowed_resources": [],
            "email": "ci@example.test",
            "expires_in_days": 1,
        },
        timeout=10,
    )
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["status"] == "created"
    raw_token = created["raw_token"]
    hash_prefix = created["hash"][:12]

    # Read the object from S3 itself: an in-memory-only success must fail here.
    raw_document, persisted_tokens = read_persisted_tokens()
    persisted_by_hash = {token["hash"]: token for token in persisted_tokens}
    assert created["hash"] in persisted_by_hash
    persisted = persisted_by_hash[created["hash"]]
    assert raw_token not in raw_document
    assert "raw_token" not in persisted
    assert persisted["revoked"] is False

    # Token can authenticate MCP tool system_whoami.
    whoami = await call_mcp_tool("system_whoami", {}, raw_token)
    assert whoami["status"] == "ok"
    assert whoami["client_name"] == "ci-e2e-agent"
    assert whoami["permissions"] == ["read", "write"]

    # Revoke via admin REST.
    r = httpx.delete(f"{BASE_URL}/admin/api/tokens/{hash_prefix}", headers=admin_headers(), timeout=10)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ok"

    _, persisted_tokens = read_persisted_tokens()
    persisted_by_hash = {token["hash"]: token for token in persisted_tokens}
    assert created["hash"] in persisted_by_hash
    persisted = persisted_by_hash[created["hash"]]
    assert persisted["revoked"] is True
    assert persisted["revoked_at"]

    # Revoked token should no longer authenticate.
    whoami = await call_mcp_tool("system_whoami", {}, raw_token)
    assert whoami["status"] == "error"
    assert "authentification" in whoami["message"].lower()
