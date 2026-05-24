"""Stdio MCP server exposing the bridge's tool surface.

Wraps ``tools.ALL_TOOLS`` with the official ``mcp`` Python SDK so any
MCP-aware client (Claude Code, Claude Desktop, or a custom agent) can
drive workflow manipulation against this bridge instance.

The server holds **one** ``AgentContext`` for the lifetime of the
stdio session. State across tool calls (loaded workflow, ComfyUI
client, etc.) is preserved between calls. Role allowlists are NOT
applied by default — the calling agent owns its own role discipline.
Set ``BRIDGE_MCP_ROLE=PLANNER|MUTATOR|...`` to lock the server into
one role's allowlist for the session.

Hard Rule §13: this server holds the orchestrate.pid file for its
lifetime (same as ``bridge orchestrate``). Tail refuses to start
while it's running.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path

from comfy_moneta_bridge.agents.client import ComfyClient
from comfy_moneta_bridge.agents.constitution import ROLE_ALLOWLIST
from comfy_moneta_bridge.agents.harness import (
    PidFileGuard,
    assert_tail_not_running,
)
from comfy_moneta_bridge.agents.tools import (
    ALL_TOOLS,
    AgentContext,
    RefusalError,
    dispatch,
)

_logger = logging.getLogger(__name__)


def _build_context(
    moneta_storage_path: Path,
    state_dir: Path,
    cozy_root: Path | None,
    session: str,
) -> AgentContext:
    role = os.environ.get("BRIDGE_MCP_ROLE")
    allowlist = (
        {role: ROLE_ALLOWLIST[role]} if role in ROLE_ALLOWLIST else {}
    )
    return AgentContext(
        moneta_storage_path=moneta_storage_path,
        state_dir=state_dir,
        session=session,
        comfy_client=None,  # opened lazily by client_open tool if needed
        cozy_root=cozy_root,
        role=role or "PLANNER",
        role_allowlist=allowlist,
    )


async def serve(
    moneta_storage_path: Path,
    state_dir: Path,
    cozy_root: Path | None = None,
    session: str = "mcp",
    tools_only: bool = False,
) -> None:
    """Run the MCP stdio server.

    Imports the ``mcp`` SDK lazily so a missing extras install fails
    with a clearer error than ``ImportError`` at module import time.
    """
    try:
        from mcp.server import Server
        from mcp.server.stdio import stdio_server
        from mcp.types import (
            TextContent, Tool,
        )
    except ImportError as e:
        raise RuntimeError(
            "bridge mcp requires the 'mcp' extra. Install with: "
            "pip install 'comfy-moneta-bridge[agents]'"
        ) from e

    assert_tail_not_running(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)

    server: Server = Server("comfy-moneta-bridge")
    ctx = _build_context(
        moneta_storage_path, state_dir, cozy_root, session
    )

    @server.list_tools()
    async def _list_tools() -> list[Tool]:
        return [
            Tool(
                name=t.name,
                description=t.description,
                inputSchema=t.input_schema,
            )
            for t in ALL_TOOLS
        ]

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict) -> list[TextContent]:
        if tools_only:
            return [
                TextContent(
                    type="text",
                    text=json.dumps(
                        {"refused": "tools-only dry-run mode"},
                        ensure_ascii=False,
                    ),
                )
            ]
        try:
            result = await dispatch(name, arguments or {}, ctx)
        except RefusalError as e:
            return [
                TextContent(
                    type="text",
                    text=json.dumps(
                        {"refused": str(e)}, ensure_ascii=False
                    ),
                )
            ]
        return [
            TextContent(
                type="text",
                text=json.dumps(result, ensure_ascii=False),
            )
        ]

    pid_file = state_dir / "orchestrate.pid"
    with PidFileGuard(pid_file):
        async with stdio_server() as (read, write):
            await server.run(
                read, write, server.create_initialization_options()
            )


def run(
    moneta_storage_path: Path,
    state_dir: Path,
    cozy_root: Path | None = None,
    session: str = "mcp",
    tools_only: bool = False,
) -> None:
    """Synchronous entry point used by the CLI."""
    asyncio.run(
        serve(
            moneta_storage_path, state_dir, cozy_root, session, tools_only
        )
    )
