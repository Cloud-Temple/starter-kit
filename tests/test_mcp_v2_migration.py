# -*- coding: utf-8 -*-
"""Régressions ciblées de la migration du SDK MCP v2 (issue #19)."""

import asyncio
import json
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "boilerplate" / "src"
SCRIPTS = ROOT / "boilerplate" / "scripts"
for path in (SRC, SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from mon_service.config import Settings  # noqa: E402
from mon_service.server import create_app  # noqa: E402
from cli.client import MCPClient  # noqa: E402


def test_mcp_v2_dependencies_are_exact_and_hash_locked():
    requirements = (ROOT / "boilerplate" / "requirements.txt").read_text(encoding="utf-8")
    lock = (ROOT / "boilerplate" / "requirements.lock").read_text(encoding="utf-8")
    assert "mcp[cli]==2.1.1" in requirements
    assert "mcp-types==2.1.1" in requirements
    assert "mcp[cli]==2.1.1" in lock
    assert "mcp-types==2.1.1" in lock
    assert "--hash=sha256:" in lock


async def _asgi_call(app, *, path, method="POST", headers=(), body=b""):
    """Exécute une requête ASGI sans réseau et renvoie statut/corps."""
    sent = []
    received = False

    async def receive():
        nonlocal received
        if received:
            return {"type": "http.disconnect"}
        received = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        sent.append(message)

    await app(
        {"type": "http", "path": path, "method": method, "headers": list(headers)},
        receive,
        send,
    )
    status = next(message["status"] for message in sent if message["type"] == "http.response.start")
    response_body = b"".join(message.get("body", b"") for message in sent if message["type"] == "http.response.body")
    return status, response_body


@pytest.mark.asyncio
async def test_v2_transport_rejects_unapproved_host_and_origin_but_keeps_health_public():
    """MCPServer v2 reçoit la politique Host/Origin sans intercepter /health."""
    app = create_app()

    # Le gestionnaire Streamable HTTP v2 initialise son groupe de tâches dans
    # le lifespan Starlette ; le test exerce donc le vrai chemin ASGI.
    mcp_app = app
    while not hasattr(mcp_app, "router"):
        mcp_app = mcp_app.app
    async with mcp_app.router.lifespan_context(mcp_app):
        status, body = await _asgi_call(app, path="/health", method="GET")
        assert status == 200
        assert json.loads(body)["status"] == "healthy"

        headers = [(b"content-type", b"application/json"), (b"host", b"attacker.test")]
        status, _ = await _asgi_call(app, path="/mcp", headers=headers, body=b"{}")
        assert status == 421

        headers = [
            (b"content-type", b"application/json"),
            (b"host", b"localhost:8002"),
            (b"origin", b"https://attacker.test"),
        ]
        status, _ = await _asgi_call(app, path="/mcp", headers=headers, body=b"{}")
        assert status == 403


def test_v2_transport_settings_fail_closed_and_keep_a_bounded_body_limit(monkeypatch):
    monkeypatch.delenv("MCP_ALLOWED_HOSTS", raising=False)
    monkeypatch.delenv("MCP_ALLOWED_ORIGINS", raising=False)
    with pytest.raises(ValueError, match="MCP_ALLOWED_HOSTS"):
        Settings()
    with pytest.raises(ValueError, match="MCP_ALLOWED_ORIGINS"):
        Settings(mcp_allowed_hosts=["localhost:8002"], mcp_allowed_origins=[])
    with pytest.raises(ValueError, match="MCP_MAX_REQUEST_BODY_SIZE"):
        Settings(
            mcp_allowed_hosts=["localhost:8002"],
            mcp_allowed_origins=["http://localhost:8002"],
            mcp_max_request_body_size=0,
        )

    settings = Settings(
        mcp_allowed_hosts=["localhost:8002"],
        mcp_allowed_origins=["http://localhost:8002"],
    )
    assert settings.mcp_max_request_body_size == 4 * 1024 * 1024
    assert settings.mcp_allowed_hosts == ["localhost:8002"]


@pytest.mark.asyncio
async def test_cli_refuses_a_configured_but_missing_internal_ca_bundle(monkeypatch):
    monkeypatch.setenv("MCP_CLIENT_CA_BUNDLE", "/missing/internal-ca.pem")
    result = await MCPClient("https://mcp.example.test").call_tool("system_health", {})
    assert result["status"] == "error"
    assert "MCP_CLIENT_CA_BUNDLE" in result["message"]


@pytest.mark.asyncio
async def test_streamable_http_eof_resolves_the_pending_request_without_hanging():
    """Une réponse SSE vide doit devenir CONNECTION_CLOSED, pas une attente infinie."""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    from mcp.shared.exceptions import MCPError
    from mcp.types import CONNECTION_CLOSED

    async def eof_server(reader, writer):
        # Le client envoie un POST HTTP/1.1 : lire headers + corps puis fermer
        # proprement un SSE vide, comme un proxy qui coupe le flux.
        headers = await reader.readuntil(b"\r\n\r\n")
        content_length = 0
        for line in headers.decode("latin-1").split("\r\n"):
            if line.lower().startswith("content-length:"):
                content_length = int(line.split(":", 1)[1].strip())
        if content_length:
            await reader.readexactly(content_length)
        writer.write(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: text/event-stream\r\n"
            b"Content-Length: 0\r\n"
            b"Connection: close\r\n\r\n"
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(eof_server, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    started = time.monotonic()
    try:
        async with streamable_http_client(f"http://127.0.0.1:{port}/mcp") as (read, write):
            async with ClientSession(read, write) as session:
                with pytest.raises(MCPError) as exc_info:
                    await asyncio.wait_for(session.initialize(), timeout=1)
        assert exc_info.value.error.code == CONNECTION_CLOSED
        assert time.monotonic() - started < 1
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_cli_uses_a_fresh_v2_session_per_call_and_fails_closed(monkeypatch):
    """Pas de pool implicite ; résultat v2 non-outil refusé sans second tour."""
    import httpx2
    import mcp
    from mcp.client import streamable_http
    from mcp.types import CallToolResult, ProgressNotification, ProgressNotificationParams, Result, TextContent

    events = []
    progress = []
    results = [
        CallToolResult(content=[TextContent(text='{"status":"ok","call":1}')]),
        Result(),
        CallToolResult(content=[TextContent(text='{"status":"ok","call":3}')]),
    ]

    class FakeHTTPClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            events.append(("http", kwargs))

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            events.append(("http-close", None))

    class FakeSession:
        def __init__(self, read, write):
            events.append(("session", None))

            async def received_notification(notification):
                return None

            self._received_notification = received_notification

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            events.append(("session-close", None))

        async def initialize(self):
            events.append(("initialize", None))

        async def call_tool(self, *args, **kwargs):
            events.append(("call", args, kwargs))
            await self._received_notification(
                ProgressNotification(
                    params=ProgressNotificationParams(
                        progress_token="test", progress=1, message="in progress"
                    )
                )
            )
            return results.pop(0)

    @asynccontextmanager
    async def fake_transport(url, *, http_client=None, terminate_on_close=True):
        events.append(("transport", url, http_client, terminate_on_close))
        yield object(), object()

    monkeypatch.setattr(httpx2, "AsyncClient", FakeHTTPClient)
    monkeypatch.setattr(mcp, "ClientSession", FakeSession)
    monkeypatch.setattr(streamable_http, "streamable_http_client", fake_transport)

    client = MCPClient("https://mcp.example.test", token="secret", timeout=42)
    async def on_progress(message):
        progress.append(message)

    assert await client.call_tool("system_health", {}, on_progress=on_progress) == {"status": "ok", "call": 1}
    assert await client.call_tool("system_health", {}) == {
        "status": "error",
        "message": "Réponse MCP non supportée ; appel refusé par sécurité.",
    }
    assert await client.call_tool("system_health", {}) == {"status": "ok", "call": 3}

    calls = [event for event in events if event[0] == "call"]
    assert len(calls) == 3
    assert all(event[2]["allow_input_required"] is False for event in calls)
    assert all(event[2]["allow_claimed"] is False for event in calls)
    assert len([event for event in events if event[0] == "http"]) == 3
    assert all(event[1]["headers"] == {"Authorization": "Bearer secret"} for event in events if event[0] == "http")
    assert all(event[1]["timeout"].connect == 30 for event in events if event[0] == "http")
    assert all(event[1]["timeout"].read == 42 for event in events if event[0] == "http")
    assert progress == ["in progress"]


def test_caddy_keeps_the_intentional_mcp_bypass_and_loads_coraza():
    """Le bypass /mcp est borné ; les autres routes gardent Coraza/CRS actif."""
    caddyfile = (ROOT / "boilerplate" / "waf" / "Caddyfile").read_text(encoding="utf-8")
    dockerfile = (ROOT / "boilerplate" / "waf" / "Dockerfile").read_text(encoding="utf-8")
    assert "github.com/corazawaf/coraza-caddy/v2" in dockerfile
    assert "handle /mcp*" in caddyfile
    assert "flush_interval -1" in caddyfile
    assert "coraza_waf {" in caddyfile
    assert "load_owasp_crs" in caddyfile
    assert "SecRuleEngine On" in caddyfile
    assert "events 300" in caddyfile
