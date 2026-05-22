"""End-to-end agent integration test.

Drives a full orchestration against:
  - a mocked Anthropic client (canned tool-use sequence),
  - a mocked ComfyUI HTTP server (httpx_mock),
  - real Moneta (no mock — verifies Hard Rule §12 with the real
    deposit pipeline),
  - real capsule writer (verifies snapshot extraction end-to-end).

This is the only test that exercises every layer simultaneously.
If it passes, the agent layer is wired up correctly.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("anthropic")
pytest.importorskip("mcp")
pytest.importorskip("httpx")

from comfy_moneta_bridge.agents.client import ComfyClient  # noqa: E402
from comfy_moneta_bridge.agents.orchestrator import (  # noqa: E402
    Orchestrator,
    RoleTurnOutput,
    ToolCall,
)
from comfy_moneta_bridge.capsule import write_capsule  # noqa: E402
from comfy_moneta_bridge.recall import recall  # noqa: E402


SAMPLE_WORKFLOW = {
    "3": {
        "class_type": "KSampler",
        "inputs": {
            "seed": 42, "steps": 20, "cfg": 7.0,
            "sampler_name": "euler", "scheduler": "normal",
            "denoise": 1.0,
            "model": ["4", 0], "positive": ["6", 0],
            "negative": ["7", 0], "latent_image": ["5", 0],
        },
    },
    "4": {"class_type": "CheckpointLoaderSimple",
          "inputs": {"ckpt_name": "sdxl.safetensors"}},
    "5": {"class_type": "EmptyLatentImage",
          "inputs": {"width": 1024, "height": 1024, "batch_size": 1}},
    "6": {"class_type": "CLIPTextEncode",
          "inputs": {"clip": ["4", 1], "text": "a teapot"}},
    "7": {"class_type": "CLIPTextEncode",
          "inputs": {"clip": ["4", 1], "text": "blurry"}},
    "8": {"class_type": "VAEDecode",
          "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
    "9": {"class_type": "SaveImage",
          "inputs": {"images": ["8", 0], "filename_prefix": "demo"}},
}


def _object_info() -> dict:
    """Minimal /object_info for the sample workflow."""
    return {
        "KSampler": {"input": {"required": {
            "seed": [], "steps": [], "cfg": [], "sampler_name": [],
            "scheduler": [], "denoise": [], "model": [],
            "positive": [], "negative": [], "latent_image": [],
        }}},
        "CheckpointLoaderSimple": {"input": {"required": {"ckpt_name": []}}},
        "EmptyLatentImage": {"input": {"required": {
            "width": [], "height": [], "batch_size": []
        }}},
        "CLIPTextEncode": {"input": {"required": {"clip": [], "text": []}}},
        "VAEDecode": {"input": {"required": {"samples": [], "vae": []}}},
        "SaveImage": {"input": {"required": {
            "images": [], "filename_prefix": []
        }}},
    }


class _ScriptedDriver:
    def __init__(self, scripted):
        self._iter = iter(scripted)

    async def step(self, role, system_prompt, goal, transcript):
        expected_role, output = next(self._iter)
        assert expected_role == role
        return output


@pytest.mark.asyncio
async def test_full_agent_round_trip(tmp_path, httpx_mock) -> None:
    """PLANNER → MUTATOR → EXECUTOR → CRITIC(ACCEPT) → MEMORIST.

    Asserts:
      1. Hard Rule §14: workflow_validate is called before submit;
         the (fixed-via-mutation) workflow validates clean.
      2. The Anthropic-style tool_use sequence threads through dispatch.
      3. The final outcome and workflow snapshot are deposited into
         Moneta via the real ingest pipeline.
      4. Subsequent write_capsule extracts the snapshot into the
         workflow block, demonstrating the v0.2 unlock end-to-end.
    """
    # --- ComfyUI HTTP mock ---
    # /object_info is cached per-client so only one fetch is needed
    # despite both workflow_validate and workflow_submit calling it.
    httpx_mock.add_response(
        url="http://127.0.0.1:8188/object_info", json=_object_info(),
    )
    httpx_mock.add_response(
        url="http://127.0.0.1:8188/prompt",
        json={"prompt_id": "comfy-prompt-7", "number": 1, "node_errors": {}},
    )

    moneta = tmp_path / "moneta"
    state_dir = tmp_path / "state"
    cozy = tmp_path / "cozy"

    # Mutated workflow: bump seed to 99.
    mutated = json.loads(json.dumps(SAMPLE_WORKFLOW))
    mutated["3"]["inputs"]["seed"] = 99

    scripted = [
        ("PLANNER", RoleTurnOutput(
            text="Load wf, bump seed.",
            tool_calls=[
                ToolCall(
                    id="c1", name="workflow_load",
                    args={"workflow": SAMPLE_WORKFLOW},
                ),
            ],
        )),
        ("MUTATOR", RoleTurnOutput(
            text="Bump seed.",
            tool_calls=[
                ToolCall(
                    id="c2", name="workflow_mutate_node",
                    args={"node_id": "3", "inputs": {"seed": 99}},
                ),
                ToolCall(
                    id="c3", name="workflow_validate", args={},
                ),
            ],
        )),
        ("EXECUTOR", RoleTurnOutput(
            text="Submitting.",
            tool_calls=[
                ToolCall(id="c4", name="workflow_submit", args={}),
            ],
        )),
        ("CRITIC", RoleTurnOutput(
            text="Looks great.", verdict="ACCEPT: clean result",
        )),
        ("MEMORIST", RoleTurnOutput(
            text="Recording.",
            tool_calls=[
                ToolCall(
                    id="c5", name="deposit_outcome",
                    args={
                        "vision_notes": ["teapot rendered cleanly"],
                        "key_params": {"seed": 99},
                        "quality_score": 0.95,
                        "workflow_summary": "seed bumped, teapot ok",
                    },
                ),
                ToolCall(
                    id="c6", name="deposit_outcome",
                    args={
                        "_kind": "workflow_snapshot",
                        "workflow": mutated,
                        "workflow_summary": "final teapot workflow",
                    },
                ),
            ],
        )),
    ]

    async def _client_factory():
        return ComfyClient(base_url="http://127.0.0.1:8188")

    orch = Orchestrator(
        driver=_ScriptedDriver(scripted),
        moneta_storage_path=moneta,
        state_dir=state_dir,
        cozy_root=cozy,
        client_factory=_client_factory,
    )

    result = await orch.run("render a clean teapot", session="e2e")
    assert result.completed is True
    assert "ACCEPT" in (result.critic_verdict or "")
    assert result.prompt_id == "comfy-prompt-7"

    # ── Persistence checks ─────────────────────────────────────
    # JSONL log file written by deposit_outcome.
    jsonl = state_dir / "agent_outcomes" / "e2e_outcomes.jsonl"
    assert jsonl.exists()
    lines = [json.loads(l) for l in jsonl.read_text(
        encoding="utf-8"
    ).splitlines()]
    kinds = [l.get("_kind") for l in lines]
    assert "outcome" in kinds
    assert "workflow_snapshot" in kinds

    # ── Moneta round-trip: recall finds the deposit ─────────────
    matches = recall("teapot", moneta, top_k=10)
    assert any(m.get("session") == "e2e" for m in matches)

    # ── Capsule extraction: hydrate produces a populated workflow ─
    out = write_capsule("e2e", cozy, moneta)
    capsule = json.loads(out.read_text(encoding="utf-8"))
    assert capsule["workflow"]["current_workflow"] is not None
    # The snapshot we deposited has seed=99 — verify it round-tripped.
    sampler = capsule["workflow"]["current_workflow"]["3"]
    assert sampler["inputs"]["seed"] == 99


@pytest.mark.asyncio
async def test_validate_failure_blocks_submit(tmp_path, httpx_mock) -> None:
    """Hard Rule §14: workflow_submit refuses on validation errors
    even if the agent tries to skip validation."""

    # object_info only declares CheckpointLoaderSimple — KSampler is
    # unknown, so the workflow won't validate.
    httpx_mock.add_response(
        url="http://127.0.0.1:8188/object_info",
        json={"CheckpointLoaderSimple": {"input": {"required": {
            "ckpt_name": []
        }}}},
    )

    scripted = [
        ("PLANNER", RoleTurnOutput(
            text="load",
            tool_calls=[
                ToolCall(id="c1", name="workflow_load",
                         args={"workflow": SAMPLE_WORKFLOW}),
            ],
        )),
        ("MUTATOR", RoleTurnOutput(text="no edits")),
        ("EXECUTOR", RoleTurnOutput(
            text="submitting blind",
            tool_calls=[
                ToolCall(id="c2", name="workflow_submit", args={}),
            ],
        )),
        ("CRITIC", RoleTurnOutput(
            text="failed", verdict="ABORT: validation failed",
        )),
        ("MEMORIST", RoleTurnOutput(
            text="record",
            tool_calls=[
                ToolCall(
                    id="c3", name="deposit_outcome",
                    args={
                        "_kind": "blocker",
                        "reason": "validation errors not addressed",
                    },
                ),
            ],
        )),
    ]

    async def _client_factory():
        return ComfyClient(base_url="http://127.0.0.1:8188")

    orch = Orchestrator(
        driver=_ScriptedDriver(scripted),
        moneta_storage_path=tmp_path / "moneta",
        state_dir=tmp_path / "state",
        cozy_root=tmp_path / "cozy",
        client_factory=_client_factory,
    )
    result = await orch.run("bad workflow", session="abort_test")
    assert result.completed is True
    assert result.prompt_id is None  # never submitted
    assert "ABORT" in (result.critic_verdict or "")

    # No /prompt request was ever made — the submit was refused.
    posts = [r for r in httpx_mock.get_requests()
             if r.url.path == "/prompt"]
    assert posts == []
