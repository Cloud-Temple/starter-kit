# -*- coding: utf-8 -*-
"""Regression tests for starter-kit issue #16 admin console fixes."""

import asyncio
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "boilerplate" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from mon_service.admin.middleware import AdminMiddleware  # noqa: E402
from mon_service.auth import middleware as auth_middleware  # noqa: E402
from mon_service.auth.middleware import LoggingMiddleware  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


async def _call_asgi(app, path, method="GET"):
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "path": path, "method": method, "headers": []}
    await app(scope, receive, send)
    return sent


def _response_start(sent):
    return next(message for message in sent if message["type"] == "http.response.start")


def _headers(sent):
    return dict(_response_start(sent)["headers"])


def test_logging_middleware_records_iso8601_utc_timestamp():
    auth_middleware._activity_log.clear()

    async def downstream(_scope, _receive, send):
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    app = LoggingMiddleware(downstream)
    _run(_call_asgi(app, "/admin/api/logs"))

    logs = auth_middleware.get_activity_log()
    assert len(logs) == 1
    timestamp = logs[0]["timestamp"]
    assert isinstance(timestamp, str)
    parsed = datetime.fromisoformat(timestamp)
    assert parsed.tzinfo is not None
    assert parsed.utcoffset().total_seconds() == 0


def test_admin_html_is_served_with_strict_script_csp():
    async def downstream(_scope, _receive, send):
        await send({"type": "http.response.start", "status": 404, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    app = AdminMiddleware(downstream)
    sent = _run(_call_asgi(app, "/admin"))
    headers = _headers(sent)

    assert _response_start(sent)["status"] == 200
    csp = headers[b"content-security-policy"].decode()
    assert "script-src 'self';" in csp
    assert "script-src 'self' 'unsafe-inline'" not in csp
    assert "object-src 'none'" in csp
    assert "base-uri 'self'" in csp


def test_admin_static_assets_do_not_reintroduce_inline_script_sinks():
    static_dir = SRC / "mon_service" / "static"
    admin_html = (static_dir / "admin.html").read_text(encoding="utf-8")
    js_sources = [
        path.read_text(encoding="utf-8")
        for path in sorted((static_dir / "js").glob("*.js"))
    ]

    assert not re.search(r"\son[a-zA-Z]+\s*=", admin_html)
    forbidden_js_sinks = (".innerHTML", ".outerHTML", ".insertAdjacentHTML", "document.write(")
    assert all(
        sink not in source
        for source in js_sources
        for sink in forbidden_js_sinks
    )


def test_waf_csp_removes_unsafe_inline_from_script_src():
    caddyfile = (ROOT / "boilerplate" / "waf" / "Caddyfile").read_text(encoding="utf-8")
    match = re.search(r'Content-Security-Policy\s+"([^"]+)"', caddyfile)
    assert match, "missing Content-Security-Policy in WAF Caddyfile"

    directives = {}
    for raw_directive in match.group(1).split(";"):
        directive = raw_directive.strip()
        if directive:
            directives[directive.split(None, 1)[0]] = directive
    script_src = directives.get("script-src")
    assert script_src == "script-src 'self'"
