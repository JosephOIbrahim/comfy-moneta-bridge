"""Single source of truth for the agent tool surface.

Each tool is defined once as a ``ToolSpec`` with a name, description,
input schema (JSONSchema, Anthropic format), and async callable. Two
helpers derive the two consumer surfaces from the same list:

  * ``as_anthropic_tools()`` → list[dict] for ``client.messages.create(tools=[...])``
  * ``register_with_mcp(server)`` → registers each on an ``mcp.server.Server``

Both surfaces dispatch through ``dispatch(name, args, ctx)``, so refusal
hooks and idempotency live in one place. The internal Claude loop and
the MCP server share this dispatcher; the loop does NOT speak MCP-over-
stdio to itself (would add subprocess + JSON-RPC overhead for in-process
calls).

Hard Rule §14: ``workflow_submit`` runs ``workflow_validate`` against
the current ``/object_info`` before posting. Validation errors short-
circuit the submission with a structured tool-error.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from comfy_moneta_bridge.agents.client import ComfyClient
from comfy_moneta_bridge.agents.workflow import Workflow
from comfy_moneta_bridge.capsule import (
    WORKFLOW_SNAPSHOT_KIND,
    write_capsule,
)
from comfy_moneta_bridge.ingest import ingest_outcome
from comfy_moneta_bridge.recall import recall as recall_fn
from comfy_moneta_bridge.vector import current_embedder_version

_logger = logging.getLogger(__name__)


class RefusalError(RuntimeError):
    """Raised when a tool call violates a constitutional refusal case."""


@dataclass
class AgentContext:
    """Per-orchestration shared state passed to every tool call.

    Tools that need the ComfyUI client, the workflow under edit, the
    Moneta storage path, etc. pull them from here rather than from
    module globals — that way the dispatcher can be unit-tested without
    process-wide state.
    """

    moneta_storage_path: Path
    state_dir: Path
    session: str = "default"
    comfy_client: ComfyClient | None = None
    workflow: Workflow | None = None
    cozy_root: Path | None = None
    role: str = "PLANNER"
    role_allowlist: dict[str, set[str]] = field(default_factory=dict)


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict
    func: Callable[[dict, AgentContext], Awaitable[dict]]


# ─── Tool implementations ──────────────────────────────────────────────


async def _tool_workflow_load(args: dict, ctx: AgentContext) -> dict:
    payload = args.get("workflow")
    if payload is None and "path" in args:
        with open(args["path"], "r", encoding="utf-8") as fp:
            payload = json.load(fp)
    if payload is None:
        raise RefusalError(
            "workflow_load requires either 'workflow' (dict) or 'path' (str)"
        )
    wf = Workflow.load(payload)
    ctx.workflow = wf
    return {"node_count": len(wf.nodes), "node_ids": list(wf.nodes.keys())}


async def _tool_workflow_mutate_node(args: dict, ctx: AgentContext) -> dict:
    if ctx.workflow is None:
        raise RefusalError("no workflow loaded — call workflow_load first")
    nid = args["node_id"]
    if "class_type" in args and nid not in ctx.workflow:
        ctx.workflow.add_node(
            nid, args["class_type"],
            inputs=args.get("inputs"),
            _meta=args.get("_meta"),
        )
    inputs = args.get("inputs") or {}
    for input_name, value in inputs.items():
        ctx.workflow.set_input(nid, input_name, value)
    return {"node_id": nid, "inputs": ctx.workflow[nid].get("inputs", {})}


async def _tool_workflow_connect(args: dict, ctx: AgentContext) -> dict:
    if ctx.workflow is None:
        raise RefusalError("no workflow loaded — call workflow_load first")
    ctx.workflow.connect(
        args["src_node"], int(args["src_slot"]),
        args["dst_node"], args["dst_input"],
    )
    return {"ok": True}


async def _tool_workflow_remove_node(args: dict, ctx: AgentContext) -> dict:
    if ctx.workflow is None:
        raise RefusalError("no workflow loaded — call workflow_load first")
    ctx.workflow.remove_node(args["node_id"])
    return {"removed": args["node_id"]}


async def _tool_workflow_validate(args: dict, ctx: AgentContext) -> dict:
    if ctx.workflow is None:
        raise RefusalError("no workflow loaded — call workflow_load first")
    if ctx.comfy_client is None:
        raise RefusalError(
            "workflow_validate requires an active ComfyClient in context"
        )
    object_info = await ctx.comfy_client.get_object_info()
    errors = ctx.workflow.validate(object_info)
    return {"valid": not errors, "errors": errors}


async def _tool_workflow_submit(args: dict, ctx: AgentContext) -> dict:
    if ctx.workflow is None:
        raise RefusalError("no workflow loaded — call workflow_load first")
    if ctx.comfy_client is None:
        raise RefusalError(
            "workflow_submit requires an active ComfyClient in context"
        )
    # Hard Rule §14: validate before submit.
    object_info = await ctx.comfy_client.get_object_info()
    errors = ctx.workflow.validate(object_info)
    if errors:
        raise RefusalError(
            "workflow_submit refused: validation errors must be fixed first: "
            + "; ".join(errors[:5])
        )
    prompt_id = await ctx.comfy_client.post_prompt(ctx.workflow.dump())
    return {"prompt_id": prompt_id}


async def _tool_workflow_interrupt(args: dict, ctx: AgentContext) -> dict:
    if ctx.comfy_client is None:
        raise RefusalError(
            "workflow_interrupt requires an active ComfyClient in context"
        )
    await ctx.comfy_client.interrupt()
    return {"interrupted": True}


async def _tool_recall_memory(args: dict, ctx: AgentContext) -> dict:
    results = recall_fn(
        args["query"], ctx.moneta_storage_path,
        top_k=int(args.get("top_k", 10)),
    )
    return {"matches": results}


def _is_path_inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


async def _tool_deposit_outcome(args: dict, ctx: AgentContext) -> dict:
    """Append an outcome JSONL line for the bridge to pick up.

    Writes to ``{state_dir}/agent_outcomes.jsonl`` so it's bridge-owned
    and Tailer-watchable (if Tailer were running; per Rule §13 it isn't
    during orchestration, so the orchestrator handles ingest directly
    via a separate call path).

    The ``_kind`` arg discriminates "outcome" vs "workflow_snapshot"
    vs "blocker". All three flow through ``ingest_outcome`` because
    they share ``schema_version=1``; the discriminator lives in the
    payload itself.
    """
    kind = args.get("_kind", "outcome")
    if kind not in ("outcome", WORKFLOW_SNAPSHOT_KIND, "blocker"):
        raise RefusalError(
            f"deposit_outcome: invalid _kind={kind!r}; must be "
            "'outcome', 'workflow_snapshot', or 'blocker'"
        )

    out_dir = ctx.state_dir / "agent_outcomes"
    if not _is_path_inside(out_dir, ctx.state_dir):
        raise RefusalError(
            "deposit_outcome: refusing path that escapes state_dir"
        )
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{ctx.session}_outcomes.jsonl"

    payload = {
        "schema_version": 1,
        "session": ctx.session,
        "timestamp": time.time(),
        "_kind": kind,
        "_embedder": current_embedder_version(),
    }
    if kind == WORKFLOW_SNAPSHOT_KIND:
        if "workflow" not in args:
            raise RefusalError(
                "deposit_outcome with _kind=workflow_snapshot requires "
                "'workflow' in args"
            )
        payload["workflow"] = args["workflow"]
        payload["loaded_path"] = args.get("loaded_path")
        payload["workflow_summary"] = args.get(
            "workflow_summary", "agent-deposited workflow snapshot"
        )
    elif kind == "blocker":
        payload["blocker_reason"] = args.get("reason", "")
        payload["attempts"] = args.get("attempts", [])
        payload["workflow_summary"] = (
            f"blocker: {args.get('reason', '')[:120]}"
        )
    else:
        payload["vision_notes"] = args.get("vision_notes", [])
        payload["key_params"] = args.get("key_params", {})
        payload["quality_score"] = args.get("quality_score")
        payload["workflow_hash"] = args.get("workflow_hash", "")
        payload["workflow_summary"] = args.get("workflow_summary", "")
        payload["user_feedback"] = args.get("user_feedback", "")
        payload["model_combo"] = args.get("model_combo", [])

    with open(out_path, "a", encoding="utf-8") as fp:
        fp.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        fp.write("\n")
        fp.flush()
        os.fsync(fp.fileno())

    # Deposit directly through the existing ingest pipeline (Rule §13
    # excludes Tailer during orchestration, so the orchestrator owns
    # the handle).
    ingest_outcome(payload, ctx.moneta_storage_path)
    return {"deposited": True, "kind": kind, "path": str(out_path)}


async def _tool_capsule_write(args: dict, ctx: AgentContext) -> dict:
    if ctx.cozy_root is None:
        raise RefusalError(
            "capsule_write requires cozy_root in context (the Comfy-Cozy "
            "root path to write sessions/ into)"
        )
    session_name = args.get("session", ctx.session)
    out = write_capsule(
        session_name, ctx.cozy_root, ctx.moneta_storage_path
    )
    return {"capsule_path": str(out)}


# ─── ToolSpec registry ────────────────────────────────────────────────


ALL_TOOLS: list[ToolSpec] = [
    ToolSpec(
        name="workflow_load",
        description=(
            "Load a ComfyUI API-format workflow into the agent context. "
            "Provide either 'workflow' (dict) or 'path' (str)."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "workflow": {
                    "type": "object",
                    "description": "ComfyUI API-format workflow JSON.",
                },
                "path": {
                    "type": "string",
                    "description": "Filesystem path to a workflow JSON file.",
                },
            },
        },
        func=_tool_workflow_load,
    ),
    ToolSpec(
        name="workflow_mutate_node",
        description=(
            "Add a node (if class_type is provided and node_id is new) "
            "or update inputs on an existing node."
        ),
        input_schema={
            "type": "object",
            "required": ["node_id"],
            "properties": {
                "node_id": {"type": "string"},
                "class_type": {"type": "string"},
                "inputs": {"type": "object"},
                "_meta": {"type": "object"},
            },
        },
        func=_tool_workflow_mutate_node,
    ),
    ToolSpec(
        name="workflow_connect",
        description=(
            "Wire src_node's output slot into dst_node's named input."
        ),
        input_schema={
            "type": "object",
            "required": ["src_node", "src_slot", "dst_node", "dst_input"],
            "properties": {
                "src_node": {"type": "string"},
                "src_slot": {"type": "integer", "minimum": 0},
                "dst_node": {"type": "string"},
                "dst_input": {"type": "string"},
            },
        },
        func=_tool_workflow_connect,
    ),
    ToolSpec(
        name="workflow_remove_node",
        description=(
            "Remove a node and strip any downstream connections "
            "referencing it."
        ),
        input_schema={
            "type": "object",
            "required": ["node_id"],
            "properties": {"node_id": {"type": "string"}},
        },
        func=_tool_workflow_remove_node,
    ),
    ToolSpec(
        name="workflow_validate",
        description=(
            "Validate the current workflow against ComfyUI's "
            "/object_info schema map. Returns {valid, errors[]}."
        ),
        input_schema={"type": "object", "properties": {}},
        func=_tool_workflow_validate,
    ),
    ToolSpec(
        name="workflow_submit",
        description=(
            "Submit the current workflow to ComfyUI's /prompt endpoint. "
            "Runs workflow_validate first (Hard Rule §14); refuses on "
            "validation errors. Returns the ComfyUI prompt_id."
        ),
        input_schema={"type": "object", "properties": {}},
        func=_tool_workflow_submit,
    ),
    ToolSpec(
        name="workflow_interrupt",
        description="Interrupt the currently-executing ComfyUI prompt.",
        input_schema={"type": "object", "properties": {}},
        func=_tool_workflow_interrupt,
    ),
    ToolSpec(
        name="recall_memory",
        description=(
            "Cross-session semantic recall over the bridge's Moneta "
            "storage. Returns top-k matching outcomes."
        ),
        input_schema={
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {"type": "string"},
                "top_k": {"type": "integer", "minimum": 1, "maximum": 100},
            },
        },
        func=_tool_recall_memory,
    ),
    ToolSpec(
        name="deposit_outcome",
        description=(
            "Deposit an outcome (or workflow_snapshot, or blocker) "
            "through the bridge's ingest pipeline. _kind defaults to "
            "'outcome'."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "_kind": {
                    "type": "string",
                    "enum": ["outcome", WORKFLOW_SNAPSHOT_KIND, "blocker"],
                },
                "workflow": {"type": "object"},
                "loaded_path": {"type": "string"},
                "vision_notes": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "key_params": {"type": "object"},
                "quality_score": {"type": "number"},
                "workflow_hash": {"type": "string"},
                "workflow_summary": {"type": "string"},
                "user_feedback": {"type": "string"},
                "model_combo": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "reason": {"type": "string"},
                "attempts": {"type": "array"},
            },
        },
        func=_tool_deposit_outcome,
    ),
    ToolSpec(
        name="capsule_write",
        description=(
            "Write the Comfy-Cozy schema_v2 capsule for a session "
            "(populated workflow block if snapshots exist)."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "session": {"type": "string"},
            },
        },
        func=_tool_capsule_write,
    ),
]


# ─── Schema/derivation helpers ────────────────────────────────────────


def tool_by_name(name: str) -> ToolSpec:
    for t in ALL_TOOLS:
        if t.name == name:
            return t
    raise KeyError(f"no tool named {name!r}")


def as_anthropic_tools() -> list[dict]:
    """Return the tools in Anthropic ``messages.create(tools=...)`` shape."""
    return [
        {
            "name": t.name,
            "description": t.description,
            "input_schema": t.input_schema,
        }
        for t in ALL_TOOLS
    ]


async def dispatch(
    name: str, args: dict, ctx: AgentContext
) -> dict:
    """Single entry point that both surfaces share.

    Enforces role allowlists before dispatching: if the active role
    (``ctx.role``) is in ``ctx.role_allowlist`` and ``name`` is not in
    that role's allowed set, raises ``RefusalError``.
    """
    tool = tool_by_name(name)
    allowed = ctx.role_allowlist.get(ctx.role)
    if allowed is not None and name not in allowed:
        raise RefusalError(
            f"role {ctx.role!r} is not permitted to call tool {name!r}; "
            f"allowed: {sorted(allowed)}"
        )
    return await tool.func(args, ctx)
