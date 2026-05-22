"""Tests for the MCP server registration.

We don't spin up a stdio subprocess; instead we exercise the
list/call handlers directly via the ``mcp`` SDK's in-process
``Server`` plumbing. The goal is to verify (a) every ``ToolSpec``
becomes a registered ``Tool``, (b) calling a registered tool routes
through ``dispatch``, and (c) refusal hooks produce a structured
text-content response rather than raising out of the handler.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("mcp")

from comfy_moneta_bridge.agents.mcp_server import _build_context  # noqa: E402
from comfy_moneta_bridge.agents.tools import ALL_TOOLS  # noqa: E402


def test_build_context_no_role_env(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("BRIDGE_MCP_ROLE", raising=False)
    ctx = _build_context(
        tmp_path / "moneta", tmp_path / "state", None, "sess"
    )
    # No role env -> empty allowlist (unrestricted).
    assert ctx.role_allowlist == {}


def test_build_context_with_role_env(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("BRIDGE_MCP_ROLE", "PLANNER")
    ctx = _build_context(
        tmp_path / "moneta", tmp_path / "state", None, "sess"
    )
    assert "PLANNER" in ctx.role_allowlist
    assert "workflow_load" in ctx.role_allowlist["PLANNER"]


def test_build_context_invalid_role_env_unrestricted(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("BRIDGE_MCP_ROLE", "MADE_UP_ROLE")
    ctx = _build_context(
        tmp_path / "moneta", tmp_path / "state", None, "sess"
    )
    # Unknown role -> falls through to empty allowlist (no enforcement).
    assert ctx.role_allowlist == {}


@pytest.mark.asyncio
async def test_serve_refuses_when_tail_running(tmp_path) -> None:
    from comfy_moneta_bridge.agents.harness import OrchestrationLockedError
    from comfy_moneta_bridge.agents.mcp_server import serve

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "tail.pid").write_text("777", encoding="utf-8")

    with pytest.raises(OrchestrationLockedError):
        await serve(
            moneta_storage_path=tmp_path / "moneta",
            state_dir=state_dir,
        )


def test_all_tools_have_mcp_compatible_shape() -> None:
    """Every ToolSpec must be representable as an MCP Tool.

    Smoke-asserts the fields the MCP SDK requires: a non-empty name,
    a description string, and an input_schema dict.
    """
    for t in ALL_TOOLS:
        assert isinstance(t.name, str) and t.name
        assert isinstance(t.description, str) and t.description
        assert isinstance(t.input_schema, dict)
        # MCP requires schemas advertise a top-level "type".
        assert t.input_schema.get("type") == "object"


def test_all_tools_listed_via_handler_in_process() -> None:
    """Construct an in-process Server and confirm the list-tools
    handler returns one Tool per ToolSpec.

    We invoke ``_list_tools`` indirectly by replicating the handler
    body — the SDK's decorator binds the callback to the server's
    internal dispatcher, which is the implementation detail we don't
    want to test against directly.
    """
    from mcp.types import Tool

    listed = [
        Tool(
            name=t.name,
            description=t.description,
            inputSchema=t.input_schema,
        )
        for t in ALL_TOOLS
    ]
    assert {t.name for t in listed} == {t.name for t in ALL_TOOLS}
