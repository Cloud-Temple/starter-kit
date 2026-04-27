# -*- coding: utf-8 -*-
"""
Manual live MCP Vault validation for VaultTokenStore.

This test is intentionally excluded from default CI. It validates the real
MCP Vault HTTP API with a dedicated vault/path and a limited RW application
token. Do not use a production token-store path.
"""

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "boilerplate" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from mon_service.auth.token_store import VaultTokenStore  # noqa: E402

pytestmark = pytest.mark.live_vault

RUN_LIVE_VAULT = os.environ.get("RUN_LIVE_VAULT") == "1"
REQUIRED_ENV = [
    "MCP_VAULT_TOKEN",
    "MCP_VAULT_ID",
    "MCP_VAULT_TOKEN_STORE_PATH",
]
DEFAULT_VAULT_URL = "https://vault.mcp.cloud-temple.app"
PROTECTED_TOKEN_STORE_PATHS = {
    "token-store/tokens.json",
    "token-store/policies.json",
}


@dataclass
class VaultSettings:
    mcp_vault_url: str
    mcp_vault_token: str
    mcp_vault_token_file: str
    mcp_vault_id: str
    mcp_vault_token_store_path: str
    mcp_vault_timeout: float = 5.0
    token_store_cache_ttl: int = 300


def _vault_secret_url(settings: VaultSettings) -> str:
    base = settings.mcp_vault_url.rstrip("/")
    path = quote(settings.mcp_vault_token_store_path, safe="")
    return f"{base}/admin/api/vaults/{settings.mcp_vault_id}/secrets/{path}"


def _vault_headers(settings: VaultSettings) -> dict:
    return {"Authorization": f"Bearer {settings.mcp_vault_token}"}


def _read_secret_data(settings: VaultSettings):
    """Return (exists, data) for the live Vault secret without logging secrets."""
    resp = httpx.get(
        _vault_secret_url(settings),
        headers=_vault_headers(settings),
        timeout=settings.mcp_vault_timeout,
    )
    if resp.status_code == 404:
        return False, None
    if resp.status_code in (401, 403):
        raise RuntimeError(f"MCP Vault permission denied while reading live test secret (HTTP {resp.status_code})")
    if resp.status_code >= 300:
        raise RuntimeError(f"MCP Vault error while reading live test secret (HTTP {resp.status_code})")
    payload = resp.json()
    data = payload.get("data", {}) if isinstance(payload, dict) else {}
    return True, data if isinstance(data, dict) else {}


def _write_secret_data(settings: VaultSettings, data: dict) -> None:
    url = f"{settings.mcp_vault_url.rstrip('/')}/admin/api/vaults/{settings.mcp_vault_id}/secrets"
    body = {
        "path": settings.mcp_vault_token_store_path,
        "type": "custom",
        "data": data,
    }
    resp = httpx.post(
        url,
        headers={**_vault_headers(settings), "Content-Type": "application/json"},
        json=body,
        timeout=settings.mcp_vault_timeout,
    )
    if resp.status_code in (401, 403):
        raise RuntimeError(f"MCP Vault permission denied while writing live test secret (HTTP {resp.status_code})")
    if resp.status_code >= 300:
        raise RuntimeError(f"MCP Vault error while writing live test secret (HTTP {resp.status_code})")


@pytest.fixture(scope="module")
def live_vault_settings():
    missing = [name for name in REQUIRED_ENV if not os.environ.get(name)]
    if not RUN_LIVE_VAULT:
        pytest.skip("Set RUN_LIVE_VAULT=1 to run live MCP Vault tests")
    if missing:
        pytest.skip(f"Missing MCP Vault env vars: {', '.join(missing)}")

    path = os.environ["MCP_VAULT_TOKEN_STORE_PATH"].strip()
    if path in PROTECTED_TOKEN_STORE_PATHS and os.environ.get("RUN_LIVE_VAULT_ALLOW_PROTECTED_PATH") != "1":
        pytest.skip(
            "Live Vault validation requires a dedicated test path. "
            "Set MCP_VAULT_TOKEN_STORE_PATH to e.g. token-store/live-vault-validation.json."
        )

    return VaultSettings(
        mcp_vault_url=os.environ.get("MCP_VAULT_URL", DEFAULT_VAULT_URL),
        mcp_vault_token=os.environ["MCP_VAULT_TOKEN"],
        mcp_vault_token_file="",
        mcp_vault_id=os.environ["MCP_VAULT_ID"],
        mcp_vault_token_store_path=path,
        mcp_vault_timeout=float(os.environ.get("MCP_VAULT_TIMEOUT", "5")),
    )


@pytest.fixture
def vault_store_with_restore(live_vault_settings):
    """Start from an empty dedicated secret and restore original data afterward."""
    original_exists, original_data = _read_secret_data(live_vault_settings)
    store = VaultTokenStore(live_vault_settings)

    try:
        _write_secret_data(live_vault_settings, {"tokens": []})
        store.load()
        yield store
    finally:
        if original_exists:
            _write_secret_data(live_vault_settings, original_data or {})
        else:
            # MCP Vault API currently exposes create/update for secrets in this
            # starter-kit flow. If the dedicated validation secret did not exist
            # before the test, leave it empty rather than leaking test tokens.
            _write_secret_data(live_vault_settings, {"tokens": []})


def test_live_vault_tokenstore_create_list_update_revoke(vault_store_with_restore, live_vault_settings):
    store = vault_store_with_restore

    assert store.count() == 0

    created = store.create(
        client_name="ci-live-vault-agent",
        permissions=["read", "write"],
        allowed_resources=["live-vault-test-resource"],
        expires_in_days=1,
        email="ci-live-vault@example.test",
        policy_id="live-vault-validation",
    )

    assert created["raw_token"]
    assert created["hash"]
    assert created["client_name"] == "ci-live-vault-agent"
    assert created["permissions"] == ["read", "write"]
    assert created["policy_id"] == "live-vault-validation"

    exists, persisted = _read_secret_data(live_vault_settings)
    assert exists is True
    assert "tokens" in persisted
    assert len(persisted["tokens"]) == 1
    assert persisted["tokens"][0]["client_name"] == "ci-live-vault-agent"
    assert "raw_token" not in persisted["tokens"][0]

    assert store.get_by_hash(created["hash"])["client_name"] == "ci-live-vault-agent"

    listed = store.list_all()
    assert len(listed) == 1
    hash_prefix = listed[0]["hash_prefix"]

    updated = store.update(
        hash_prefix=hash_prefix,
        policy_id="live-vault-validation-updated",
        permissions=["read"],
        allowed_resources=["live-vault-updated-resource"],
    )
    assert updated["status"] == "updated"
    assert set(updated["updated_fields"]) == {"policy_id", "permissions", "allowed_resources"}

    store.load()
    listed = store.list_all()
    assert listed[0]["permissions"] == ["read"]
    assert listed[0]["policy_id"] == "live-vault-validation-updated"
    assert listed[0]["allowed_resources"] == ["live-vault-updated-resource"]

    assert store.revoke(hash_prefix) is True
    store.load()
    listed = store.list_all()
    assert listed[0]["revoked"] is True
    assert store.count() == 0
