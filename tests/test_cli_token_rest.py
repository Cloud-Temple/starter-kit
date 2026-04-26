# -*- coding: utf-8 -*-
"""
Tests CLI/shell token management — hybrid architecture.

Contract validated for the starter-kit:
- MCP (/mcp) is for business/system tools.
- Administration (tokens, policies, audit) goes through REST /admin/api/*.

These tests ensure the Click CLI and interactive shell do not call a MCP tool named
"token" and instead use MCPClient.call_admin_api().
"""

import asyncio
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "boilerplate" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from cli.commands import cli  # noqa: E402
from cli.client import MCPClient  # noqa: E402
from cli.shell import cmd_token  # noqa: E402


@pytest.fixture
def admin_calls(monkeypatch):
    """Capture calls made through MCPClient.call_admin_api."""
    calls = []

    async def fake_call_admin_api(self, method, path, json_body=None):
        calls.append({
            "method": method,
            "path": path,
            "json_body": json_body,
            "base_url": self.base_url,
            "token": self.token,
        })
        if method == "POST":
            return {
                "status": "created",
                "raw_token": "raw-token-once",
                "client_name": json_body["client_name"],
                "permissions": json_body["permissions"],
                "email": json_body.get("email", ""),
                "expires_at": "2099-01-01T00:00:00+00:00",
            }
        if method == "GET":
            return {"status": "ok", "tokens": []}
        if method == "PUT":
            return {"status": "updated", "updated_fields": list((json_body or {}).keys())}
        if method == "DELETE":
            return {"status": "ok", "message": "Token révoqué"}
        return {"status": "error", "message": "unexpected method"}

    def forbidden_call_tool(self, tool_name, arguments, on_progress=None):
        raise AssertionError(f"CLI/shell must not call MCP tool {tool_name!r} for token admin")

    monkeypatch.setattr(MCPClient, "call_admin_api", fake_call_admin_api)
    monkeypatch.setattr(MCPClient, "call_tool", forbidden_call_tool)
    return calls


def invoke_cli(args):
    runner = CliRunner()
    return runner.invoke(cli, ["--url", "http://localhost:8002", "--token", "admin-token", *args])


class TestClickTokenRest:
    def test_token_create_uses_admin_rest_with_resources(self, admin_calls):
        result = invoke_cli([
            "token", "create", "agent-prod",
            "--permissions", "read,write",
            "--email", "ops@example.test",
            "--expires", "30",
            "--resources", "vault-a,vault-b",
        ])

        assert result.exit_code == 0, result.output
        assert len(admin_calls) == 1
        call = admin_calls[0]
        assert call["method"] == "POST"
        assert call["path"] == "/tokens"
        assert call["token"] == "admin-token"
        assert call["json_body"] == {
            "client_name": "agent-prod",
            "permissions": ["read", "write"],
            "allowed_resources": ["vault-a", "vault-b"],
            "email": "ops@example.test",
            "expires_in_days": 30,
        }

    def test_token_create_accepts_vaults_alias(self, admin_calls):
        result = invoke_cli([
            "token", "create", "agent-vault",
            "--permissions", "read",
            "--vaults", "prod-a,prod-b",
        ])

        assert result.exit_code == 0, result.output
        assert admin_calls[0]["method"] == "POST"
        assert admin_calls[0]["json_body"]["allowed_resources"] == ["prod-a", "prod-b"]

    def test_token_list_uses_admin_rest(self, admin_calls):
        result = invoke_cli(["token", "list"])

        assert result.exit_code == 0, result.output
        assert admin_calls == [{
            "method": "GET",
            "path": "/tokens",
            "json_body": None,
            "base_url": "http://localhost:8002",
            "token": "admin-token",
        }]

    def test_token_update_uses_admin_rest_with_resources_alias(self, admin_calls):
        result = invoke_cli([
            "token", "update", "abcdef123456",
            "--permissions", "read,admin",
            "--vaults", "vault-x",
        ])

        assert result.exit_code == 0, result.output
        call = admin_calls[0]
        assert call["method"] == "PUT"
        assert call["path"] == "/tokens/abcdef123456"
        assert call["json_body"] == {
            "permissions": ["read", "admin"],
            "allowed_resources": ["vault-x"],
        }

    def test_token_revoke_uses_admin_rest(self, admin_calls):
        result = invoke_cli(["token", "revoke", "abcdef123456"])

        assert result.exit_code == 0, result.output
        assert admin_calls[0]["method"] == "DELETE"
        assert admin_calls[0]["path"] == "/tokens/abcdef123456"


class TestShellTokenRest:
    def test_shell_token_create_uses_admin_rest(self, admin_calls):
        client = MCPClient("http://localhost:8002", "admin-token")

        asyncio.run(cmd_token(
            client,
            {},
            "create shell-agent --email shell@example.test --permissions read,write --expires 7 --resources r1,r2",
            json_output=True,
        ))

        call = admin_calls[0]
        assert call["method"] == "POST"
        assert call["path"] == "/tokens"
        assert call["json_body"] == {
            "client_name": "shell-agent",
            "permissions": ["read", "write"],
            "allowed_resources": ["r1", "r2"],
            "email": "shell@example.test",
            "expires_in_days": 7,
        }

    def test_shell_token_update_accepts_vaults_alias(self, admin_calls):
        client = MCPClient("http://localhost:8002", "admin-token")

        asyncio.run(cmd_token(
            client,
            {},
            "update abcdef123456 --permissions read --vaults prod-a",
            json_output=True,
        ))

        call = admin_calls[0]
        assert call["method"] == "PUT"
        assert call["path"] == "/tokens/abcdef123456"
        assert call["json_body"] == {
            "permissions": ["read"],
            "allowed_resources": ["prod-a"],
        }


def test_no_cli_or_shell_token_admin_uses_mcp_tool():
    """Static non-regression: token admin must not use call_tool('token')."""
    for rel in [
        "boilerplate/scripts/cli/commands.py",
        "boilerplate/scripts/cli/shell.py",
    ]:
        content = (ROOT / rel).read_text(encoding="utf-8")
        assert 'call_tool("token"' not in content
        assert "call_tool('token'" not in content
