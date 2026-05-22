"""Tests for ``comfy_moneta_bridge.agents.tools``.

Three concerns:

  1. **Schema validity**: every ``ToolSpec.input_schema`` is a valid
     JSONSchema (jsonschema parses it without complaint).
  2. **Surface parity**: ``as_anthropic_tools()`` exposes the same
     names+descriptions+schemas as the registry; MCP registration
     (tested separately in test_agents_mcp_server) iterates over the
     same list. The "single source of truth" guarantee is enforced
     here.
  3. **Refusal hooks fire**: ``workflow_submit`` against a workflow
     with validation errors refuses; role-allowlist mismatch refuses;
     ``deposit_outcome`` with invalid ``_kind`` refuses.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("httpx")
pytest.importorskip("jsonschema")

from jsonschema import Draft202012Validator  # noqa: E402

from comfy_moneta_bridge.agents.tools import (  # noqa: E402
    ALL_TOOLS,
    AgentContext,
    RefusalError,
    as_anthropic_tools,
    dispatch,
    tool_by_name,
)
from comfy_moneta_bridge.agents.workflow import Workflow  # noqa: E402


# ─── Schema validity ──────────────────────────────────────────────────


@pytest.mark.parametrize("tool", ALL_TOOLS, ids=lambda t: t.name)
def test_input_schema_is_valid_jsonschema(tool) -> None:
    # Will raise if the schema is malformed.
    Draft202012Validator.check_schema(tool.input_schema)


@pytest.mark.parametrize("tool", ALL_TOOLS, ids=lambda t: t.name)
def test_tool_has_name_and_description(tool) -> None:
    assert tool.name
    assert tool.description
    assert callable(tool.func)


# ─── Surface parity ───────────────────────────────────────────────────


def test_as_anthropic_tools_matches_registry() -> None:
    anth = as_anthropic_tools()
    assert {t["name"] for t in anth} == {t.name for t in ALL_TOOLS}
    by_name = {t["name"]: t for t in anth}
    for tool in ALL_TOOLS:
        assert by_name[tool.name]["description"] == tool.description
        assert by_name[tool.name]["input_schema"] == tool.input_schema


def test_tool_by_name() -> None:
    assert tool_by_name("workflow_load").name == "workflow_load"
    with pytest.raises(KeyError):
        tool_by_name("nonexistent_tool")


def test_expected_tool_names_present() -> None:
    """Smoke check that all tools named in AGENTS.md role allowlists
    actually exist."""
    expected = {
        "workflow_load",
        "workflow_mutate_node",
        "workflow_connect",
        "workflow_remove_node",
        "workflow_validate",
        "workflow_submit",
        "workflow_interrupt",
        "recall_memory",
        "deposit_outcome",
        "capsule_write",
    }
    actual = {t.name for t in ALL_TOOLS}
    assert expected == actual


# ─── Refusal hooks ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_workflow_load_inline_payload(tmp_path) -> None:
    ctx = AgentContext(moneta_storage_path=tmp_path, state_dir=tmp_path)
    result = await dispatch(
        "workflow_load",
        {"workflow": {"1": {"class_type": "KSampler"}}},
        ctx,
    )
    assert result["node_count"] == 1
    assert ctx.workflow is not None


@pytest.mark.asyncio
async def test_workflow_load_from_path(tmp_path) -> None:
    p = tmp_path / "wf.json"
    p.write_text(
        json.dumps({"1": {"class_type": "KSampler"}}),
        encoding="utf-8",
    )
    ctx = AgentContext(moneta_storage_path=tmp_path, state_dir=tmp_path)
    result = await dispatch("workflow_load", {"path": str(p)}, ctx)
    assert result["node_ids"] == ["1"]


@pytest.mark.asyncio
async def test_workflow_load_no_args_refused(tmp_path) -> None:
    ctx = AgentContext(moneta_storage_path=tmp_path, state_dir=tmp_path)
    with pytest.raises(RefusalError):
        await dispatch("workflow_load", {}, ctx)


@pytest.mark.asyncio
async def test_workflow_mutate_without_load_refused(tmp_path) -> None:
    ctx = AgentContext(moneta_storage_path=tmp_path, state_dir=tmp_path)
    with pytest.raises(RefusalError, match="workflow_load first"):
        await dispatch(
            "workflow_mutate_node", {"node_id": "1"}, ctx
        )


@pytest.mark.asyncio
async def test_workflow_submit_refuses_invalid_workflow(tmp_path) -> None:
    """Hard Rule §14: workflow_submit refuses on validation errors,
    even if the agent calls it directly without validating first."""

    class _StubClient:
        async def get_object_info(self):
            return {"KSampler": {"input": {"required": {"seed": []}}}}

        async def post_prompt(self, wf):  # pragma: no cover — must not run
            raise AssertionError("post_prompt called despite invalid workflow")

    ctx = AgentContext(
        moneta_storage_path=tmp_path,
        state_dir=tmp_path,
        comfy_client=_StubClient(),  # type: ignore[arg-type]
        workflow=Workflow.load({"1": {"class_type": "UnknownClass"}}),
    )
    with pytest.raises(RefusalError, match="validation errors"):
        await dispatch("workflow_submit", {}, ctx)


@pytest.mark.asyncio
async def test_workflow_submit_happy_path(tmp_path) -> None:
    submitted = {}

    class _StubClient:
        async def get_object_info(self):
            return {"KSampler": {"input": {"required": {}}}}

        async def post_prompt(self, wf):
            submitted["payload"] = wf
            return "fake-prompt-id"

    ctx = AgentContext(
        moneta_storage_path=tmp_path,
        state_dir=tmp_path,
        comfy_client=_StubClient(),  # type: ignore[arg-type]
        workflow=Workflow.load({"1": {"class_type": "KSampler"}}),
    )
    result = await dispatch("workflow_submit", {}, ctx)
    assert result["prompt_id"] == "fake-prompt-id"
    assert "1" in submitted["payload"]


@pytest.mark.asyncio
async def test_workflow_interrupt(tmp_path) -> None:
    interrupted = []

    class _StubClient:
        async def interrupt(self):
            interrupted.append(True)

    ctx = AgentContext(
        moneta_storage_path=tmp_path,
        state_dir=tmp_path,
        comfy_client=_StubClient(),  # type: ignore[arg-type]
    )
    await dispatch("workflow_interrupt", {}, ctx)
    assert interrupted == [True]


@pytest.mark.asyncio
async def test_deposit_outcome_invalid_kind_refused(tmp_path) -> None:
    ctx = AgentContext(moneta_storage_path=tmp_path, state_dir=tmp_path)
    with pytest.raises(RefusalError, match="invalid _kind"):
        await dispatch("deposit_outcome", {"_kind": "made_up"}, ctx)


@pytest.mark.asyncio
async def test_deposit_outcome_snapshot_without_workflow_refused(
    tmp_path,
) -> None:
    ctx = AgentContext(moneta_storage_path=tmp_path, state_dir=tmp_path)
    with pytest.raises(RefusalError, match="requires 'workflow'"):
        await dispatch(
            "deposit_outcome", {"_kind": "workflow_snapshot"}, ctx
        )


@pytest.mark.asyncio
async def test_deposit_outcome_writes_jsonl_and_ingests(tmp_path) -> None:
    ctx = AgentContext(
        moneta_storage_path=tmp_path / "moneta",
        state_dir=tmp_path / "state",
        session="testsess",
    )
    result = await dispatch(
        "deposit_outcome",
        {
            "vision_notes": ["agent noted the gradient"],
            "key_params": {"steps": 25},
            "quality_score": 0.8,
            "workflow_summary": "agent test outcome",
        },
        ctx,
    )
    assert result["deposited"] is True
    out_path = tmp_path / "state" / "agent_outcomes" / "testsess_outcomes.jsonl"
    assert out_path.exists()
    line = out_path.read_text(encoding="utf-8").strip()
    parsed = json.loads(line)
    assert parsed["session"] == "testsess"
    assert parsed["schema_version"] == 1
    # And the deposit reached Moneta — query it back.
    from comfy_moneta_bridge.recall import recall as recall_fn

    matches = recall_fn("testsess", tmp_path / "moneta", top_k=5)
    assert any(m.get("session") == "testsess" for m in matches)


# ─── Role allowlist enforcement ───────────────────────────────────────


@pytest.mark.asyncio
async def test_role_allowlist_refusal(tmp_path) -> None:
    ctx = AgentContext(
        moneta_storage_path=tmp_path,
        state_dir=tmp_path,
        role="PLANNER",
        role_allowlist={"PLANNER": {"workflow_load", "recall_memory"}},
    )
    with pytest.raises(RefusalError, match="not permitted"):
        await dispatch(
            "workflow_mutate_node", {"node_id": "1"}, ctx
        )


@pytest.mark.asyncio
async def test_role_allowlist_allows_in_lane(tmp_path) -> None:
    ctx = AgentContext(
        moneta_storage_path=tmp_path,
        state_dir=tmp_path,
        role="PLANNER",
        role_allowlist={"PLANNER": {"workflow_load"}},
    )
    result = await dispatch(
        "workflow_load",
        {"workflow": {"1": {"class_type": "KSampler"}}},
        ctx,
    )
    assert result["node_count"] == 1


@pytest.mark.asyncio
async def test_role_allowlist_empty_means_unrestricted(tmp_path) -> None:
    """When ``role_allowlist`` is empty (the default), no role gating
    is applied; all tools are callable."""
    ctx = AgentContext(moneta_storage_path=tmp_path, state_dir=tmp_path)
    await dispatch(
        "workflow_load",
        {"workflow": {"1": {"class_type": "KSampler"}}},
        ctx,
    )
