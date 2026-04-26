# -*- coding: utf-8 -*-
"""Tests for multi-company branding profiles and public admin brand endpoint."""

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "boilerplate" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from mon_service.branding import BRANDS, get_brand_profile  # noqa: E402
from mon_service.admin import api as admin_api  # noqa: E402
from mon_service.config import get_settings  # noqa: E402


def test_brand_profiles_exist_with_expected_colors_and_logos():
    assert set(BRANDS) == {"ct", "dgy", "isec"}
    assert BRANDS["ct"]["company_name"] == "Cloud Temple"
    assert BRANDS["ct"]["colors"]["accent"] == "#41a890"
    assert BRANDS["dgy"]["company_name"] == "Dragonfly"
    assert BRANDS["dgy"]["colors"]["accent"] == "#ff5a00"
    assert BRANDS["isec"]["company_name"] == "Intrinsec"
    assert BRANDS["isec"]["colors"]["accent"] == "#c91517"

    for code in BRANDS:
        logo = ROOT / "boilerplate" / "src" / "mon_service" / "static" / "img" / f"logo-{code}.svg"
        assert logo.exists(), f"missing logo for {code}: {logo}"


def test_unknown_brand_falls_back_to_cloud_temple():
    profile = get_brand_profile("unknown")
    assert profile["code"] == "ct"
    assert profile["company_name"] == "Cloud Temple"


@pytest.fixture(autouse=True)
def clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def call_brand():
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": "GET", "path": "/admin/api/brand", "headers": []}
    await admin_api.handle_admin_api(scope, receive, send, mcp=None)
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return status, json.loads(body.decode())


@pytest.mark.asyncio
async def test_brand_endpoint_is_public_and_uses_mcp_brand(monkeypatch):
    monkeypatch.setenv("MCP_BRAND", "dgy")
    get_settings.cache_clear()

    status, data = await call_brand()

    assert status == 200
    assert data["status"] == "ok"
    assert data["code"] == "dgy"
    assert data["company_name"] == "Dragonfly"
    assert data["logo"] == "/admin/static/img/logo-dgy.svg"
