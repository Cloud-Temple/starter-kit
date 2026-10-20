"""Closed refusal outcomes only; uncertainty never proves absence or confirmation."""
import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def oracle(tmp_path, monkeypatch):
    calls = []
    response = {}
    intent = tmp_path / "intent.json"

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        async def call_tool(self, tool, args):
            assert json.loads(intent.read_text())["operation_id"] == args["operation_id"]
            calls.append((tool, args))
            return response["value"]

    client = ModuleType("scripts.cli.client")
    client.MCPClient = Client
    credentials = ModuleType("src.mcp_tools.storage.files_credentials")
    credentials.private_text = lambda filename: Path(filename).read_text()
    monkeypatch.setitem(sys.modules, client.__name__, client)
    monkeypatch.setitem(sys.modules, credentials.__name__, credentials)
    spec = importlib.util.spec_from_file_location("qualification48", ROOT / "tests/fixtures/standalone_project/qualify.py")
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    token = tmp_path / "token"
    token.write_text("synthetic-unit-token")
    args = SimpleNamespace(url="unused-pure-test", token_file=str(token), intent=str(intent), phase="refuse-write")
    return loaded, args, response, calls


@pytest.mark.parametrize("response", [
    {"status": "error"},  # Native public6083 R21: all five connection faults.
    {"status": "incomplete", "remote_result": "uncertain", "effect_state": "unobserved"},
    {"status": "incomplete", "remote_result": "uncertain"},
])
def test_public_write_nonconfirmation_still_requires_healthy_absence(oracle, response, capsys):
    loaded, args, replies, calls = oracle
    replies["value"] = response
    asyncio.run(loaded.run(args))
    output = json.loads(capsys.readouterr().out)
    assert output["absence_not_yet_proven"] is True
    assert len(calls) == 1 and calls[0][1]["operation"] == "write"
    assert not Path(args.intent).with_suffix(".confirmed").exists()


@pytest.mark.parametrize("response", [
    {"status": "unexpected"}, {},
    {"status": "ok", "remote_result": "confirmed"},
    {"status": "error", "remote_result": "confirmed"},
    {"status": "incomplete", "remote_result": "confirmed", "effect_state": "unobserved"},
    {"status": "incomplete"},
    {"status": "incomplete", "remote_result": "uncertain", "effect_state": "present"},
    {"status": "error", "effect_state": "absent"},
    {"status": ["error"]}, {"status": True},
    {"status": "incomplete", "remote_result": ["uncertain"]},
    {"status": "incomplete", "remote_result": "uncertain", "effect_state": {}},
    None, [], "error",
])
def test_public_write_unknown_confirmed_or_malformed_outcomes_refuse(oracle, response):
    loaded, args, replies, _ = oracle
    replies["value"] = response
    with pytest.raises(RuntimeError):
        asyncio.run(loaded.run(args))
    assert Path(args.intent).is_file()
    assert not Path(args.intent).with_suffix(".confirmed").exists()
