# -*- coding: utf-8 -*-
"""Minimal fake MCP Vault HTTP service for Docker Compose e2e tests.

Supported live-like endpoints:

- GET  /admin/api/vaults/{vault_id}/secrets/{encoded_path}
- POST /admin/api/vaults/{vault_id}/secrets

The service stores secrets in memory and intentionally implements only the
surface needed by VaultTokenStore e2e validation.
"""

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

VAULT_TOKEN = os.environ.get("FAKE_VAULT_TOKEN", "vault-token")
HOST = os.environ.get("FAKE_VAULT_HOST", "0.0.0.0")
PORT = int(os.environ.get("FAKE_VAULT_PORT", "8080"))

# In-memory storage: (vault_id, path) -> data dict
SECRETS = {}


def _json_bytes(payload: dict) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


class FakeMCPVaultHandler(BaseHTTPRequestHandler):
    server_version = "FakeMCPVault/0.1"

    def log_message(self, fmt, *args):  # pragma: no cover - diagnostics only
        print(f"fake-mcp-vault - {self.address_string()} - {fmt % args}")

    def _send_json(self, status: int, payload: dict) -> None:
        body = _json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        return self.headers.get("Authorization") == f"Bearer {VAULT_TOKEN}"

    def _require_auth(self) -> bool:
        if self._authorized():
            return True
        self._send_json(403, {"status": "error", "message": "permission denied"})
        return False

    def do_GET(self):  # noqa: N802 - stdlib callback name
        if self.path in {"/health", "/healthz"}:
            self._send_json(200, {"status": "ok", "service": "fake-mcp-vault"})
            return

        if not self._require_auth():
            return

        prefix = "/admin/api/vaults/"
        marker = "/secrets/"
        if not self.path.startswith(prefix) or marker not in self.path:
            self._send_json(404, {"status": "error", "message": "not found"})
            return

        rest = self.path[len(prefix):]
        vault_id, encoded_path = rest.split(marker, 1)
        path = unquote(encoded_path)
        key = (vault_id, path)
        if key not in SECRETS:
            self._send_json(404, {"status": "error", "message": "secret not found"})
            return

        data = dict(SECRETS[key])
        data.setdefault("_type", "custom")
        data.setdefault("_tags", "")
        data.setdefault("_favorite", "false")

        self._send_json(200, {
            "status": "ok",
            "vault_id": vault_id,
            "path": path,
            "data": data,
            "version": 1,
            "created_time": "2026-01-01T00:00:00Z",
        })

    def do_POST(self):  # noqa: N802 - stdlib callback name
        if not self._require_auth():
            return

        prefix = "/admin/api/vaults/"
        suffix = "/secrets"
        if not self.path.startswith(prefix) or not self.path.endswith(suffix):
            self._send_json(404, {"status": "error", "message": "not found"})
            return

        vault_id = self.path[len(prefix):-len(suffix)]
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8") if length else "{}"
            payload = json.loads(body)
        except Exception:
            self._send_json(400, {"status": "error", "message": "invalid json"})
            return

        path = payload.get("path")
        data = payload.get("data")
        if not isinstance(path, str) or not path:
            self._send_json(400, {"status": "error", "message": "path is required"})
            return
        if not isinstance(data, dict):
            self._send_json(400, {"status": "error", "message": "data must be an object"})
            return

        stored = dict(data)
        stored.setdefault("_type", payload.get("type", "custom"))
        stored.setdefault("_tags", "")
        stored.setdefault("_favorite", "false")
        SECRETS[(vault_id, path)] = stored

        self._send_json(200, {
            "status": "ok",
            "vault_id": vault_id,
            "path": path,
            "data": stored,
            "version": 1,
        })


def main():
    httpd = ThreadingHTTPServer((HOST, PORT), FakeMCPVaultHandler)
    print(f"fake-mcp-vault listening on {HOST}:{PORT}", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
