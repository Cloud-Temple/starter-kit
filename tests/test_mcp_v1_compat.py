# -*- coding: utf-8 -*-
"""Compatibilité wire réelle : client MCP 1.28.1 contre serveur SDK v2."""

import os
import subprocess
import textwrap

import pytest


MCP_V1_PYTHON = os.environ.get("MCP_V1_PYTHON")
MCP_V1_COMPAT_URL = os.environ.get("MCP_V1_COMPAT_URL")


@pytest.mark.skipif(
    not (MCP_V1_PYTHON and MCP_V1_COMPAT_URL),
    reason="Set MCP_V1_PYTHON and MCP_V1_COMPAT_URL after provisioning mcp==1.28.1.",
)
def test_real_mcp_v1_28_client_calls_the_v2_server():
    """Le test isole volontairement le client v1 dans son propre environnement."""
    program = textwrap.dedent(
        """
        import asyncio
        import json
        import os
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        async def main():
            async with streamablehttp_client(os.environ["MCP_V1_COMPAT_URL"], timeout=10, sse_read_timeout=10) as streams:
                read, write, _ = streams
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool("system_health", {})
                    assert not result.isError
                    assert json.loads(result.content[0].text)["status"] == "ok"

        asyncio.run(main())
        """
    )
    completed = subprocess.run(
        [MCP_V1_PYTHON, "-c", program],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, "MCP_V1_COMPAT_URL": MCP_V1_COMPAT_URL},
    )
    assert completed.returncode == 0, completed.stderr
