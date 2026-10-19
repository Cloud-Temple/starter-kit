#!/usr/bin/env python3
"""Public Tools calls from the autonomous copy, using its unmodified official client.

seed creates an explicit UUID and records it BEFORE dispatch; read never replays it.
Proof files are private and kept on any failure/unknown. No infrastructure startup.
"""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
import uuid

sys.path.insert(0, str(Path.cwd()))
from scripts.cli.client import MCPClient
from src.mcp_tools.storage.files_credentials import private_text


def exclusive(path, payload):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(payload, f)


async def run(args):
    client = MCPClient(args.url, private_text(args.token_file), timeout=30)
    intent = Path(args.intent)
    if args.phase in ("seed", "refuse-write"):
        payload = dict(path="qualification48/" + str(uuid.uuid4()),
                       operation_id=str(uuid.uuid4()), content="standalone-confirmed-48")
        exclusive(intent, payload)
        result = await client.call_tool("files", dict(operation="write", **payload))
        if args.phase == "refuse-write":
            if result.get("status") != "error" or result.get("remote_result") == "confirmed":
                raise RuntimeError("Write unexpectedly accepted")
            print('{"status":"public_write_refused","absence_not_yet_proven":true}')
            return
        if result.get("status") != "ok" or result.get("remote_result") != "confirmed":
            raise RuntimeError("Write not confirmed; preserve intent, never rerun seed")
        exclusive(intent.with_suffix(".confirmed"), {"operation_id": payload["operation_id"]})
    else:
        payload = json.loads(private_text(str(intent)))
        if args.phase != "absent" and not intent.with_suffix(".confirmed").is_file():
            raise RuntimeError("No confirmed write to qualify")
    result = await client.call_tool("files", {"operation": "read", "path": payload["path"]})
    if args.phase == "absent":
        if result.get("effect_state") != "absent":
            raise RuntimeError("Refused mutation absence not confirmed")
        print('{"status":"refused_mutation_absence_observed"}')
    elif args.phase == "deny":
        if result.get("status") != "error" or "content" in result:
            raise RuntimeError("Expected explicit refusal without content")
        print('{"status":"public_refusal_observed"}')
    else:
        if result.get("status") != "ok" or result.get("content") != payload["content"]:
            raise RuntimeError("Persistent content not observed")
        expected = hashlib.sha256(payload["content"].encode()).hexdigest()
        if result.get("sha256") != expected:
            raise RuntimeError("Content integrity mismatch")
        print(json.dumps({"status": "public_content_observed", "operation_id": payload["operation_id"]}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("seed", "read", "deny", "refuse-write", "absent"))
    parser.add_argument("--url", required=True)
    parser.add_argument("--token-file", required=True)
    parser.add_argument("--intent", required=True)
    asyncio.run(run(parser.parse_args()))
