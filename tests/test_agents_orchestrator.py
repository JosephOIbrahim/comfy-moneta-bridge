"""End-to-end-ish test for the orchestrator with a scripted driver.

The ``StubDriver`` plays back a fixed sequence of role outputs so the
test can verify role progression, tool dispatch, checkpoint
advancement, and final-outcome emission without depending on the
Anthropic SDK or a live ComfyUI.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

import pytest

# Importing the orchestrator transitively imports agents.client, which
# imports httpx and websockets at module load. Under a lean install
# (no [agents] extras) collection would error rather than skip. These
# importorskips make the lean-install pytest pass cleanly.
pytest.importorskip("httpx")
pytest.importorskip("anthropic")

from comfy_moneta_bridge.agents.orchestrator import (  # noqa: E402
    Orchestrator,
    RoleTurnOutput,
    ToolCall,
)


class StubDriver:
    """Replays a fixed sequence of (role, RoleTurnOutput) tuples."""

    def __init__(self, scripted: list[tuple[str, RoleTurnOutput]]) -> None:
        self._scripted: Iterator[tuple[str, RoleTurnOutput]] = iter(scripted)

    async def step(self, role, system_prompt, goal, transcript):
        expected_role, output = next(self._scripted)
        assert expected_role == role, (
            f"StubDriver out-of-sync: expected {expected_role}, got {role}"
        )
        return output


@pytest.mark.asyncio
async def test_orchestrator_full_run_with_snapshot(tmp_path) -> None:
    """Full happy-path: PLANNER → MUTATOR → EXECUTOR → CRITIC(ACCEPT)
    → MEMORIST emits both a normal outcome and a workflow snapshot."""

    moneta = tmp_path / "moneta"
    state_dir = tmp_path / "state"

    sample_workflow = {"1": {"class_type": "KSampler", "inputs": {"seed": 42}}}

    class _FakeComfy:
        def __init__(self):
            self._entered = False

        async def __aenter__(self):
            self._entered = True
            return self

        async def __aexit__(self, *a):
            return False

        async def get_object_info(self):
            return {"KSampler": {"input": {"required": {"seed": []}}}}

        async def post_prompt(self, wf):
            return "fake-prompt-id-99"

    async def _client_factory():
        return _FakeComfy()

    scripted = [
        (
            "PLANNER",
            RoleTurnOutput(
                text="Plan: load workflow, mutate seed, submit.",
                tool_calls=[
                    ToolCall(
                        id="c1", name="workflow_load",
                        args={"workflow": sample_workflow},
                    ),
                ],
            ),
        ),
        (
            "MUTATOR",
            RoleTurnOutput(
                text="Bumping seed to 7.",
                tool_calls=[
                    ToolCall(
                        id="c2", name="workflow_mutate_node",
                        args={"node_id": "1", "inputs": {"seed": 7}},
                    ),
                ],
            ),
        ),
        (
            "EXECUTOR",
            RoleTurnOutput(
                text="Submitting.",
                tool_calls=[
                    ToolCall(id="c3", name="workflow_submit", args={}),
                ],
            ),
        ),
        (
            "CRITIC",
            RoleTurnOutput(text="Looks good.", verdict="ACCEPT"),
        ),
        (
            "MEMORIST",
            RoleTurnOutput(
                text="Recording.",
                tool_calls=[
                    ToolCall(
                        id="c4", name="deposit_outcome",
                        args={
                            "vision_notes": ["seed bump worked"],
                            "key_params": {"seed": 7},
                            "quality_score": 0.9,
                            "workflow_summary": "seed bump",
                        },
                    ),
                    ToolCall(
                        id="c5", name="deposit_outcome",
                        args={
                            "_kind": "workflow_snapshot",
                            "workflow": {
                                "1": {
                                    "class_type": "KSampler",
                                    "inputs": {"seed": 7},
                                }
                            },
                            "workflow_summary": "post-bump snapshot",
                        },
                    ),
                ],
            ),
        ),
    ]

    orch = Orchestrator(
        driver=StubDriver(scripted),
        moneta_storage_path=moneta,
        state_dir=state_dir,
        client_factory=_client_factory,
    )

    result = await orch.run("bump seed to 7", session="snaptest")
    assert result.completed is True
    assert result.critic_verdict == "ACCEPT"
    assert result.prompt_id == "fake-prompt-id-99"
    assert result.final_role == "MEMORIST"

    # Checkpoint file should exist with at least 5 turns recorded.
    checkpoint_file = state_dir / "checkpoints.json"
    assert checkpoint_file.exists()
    blob = json.loads(checkpoint_file.read_text(encoding="utf-8"))
    cp = blob["goals"][result.goal_id]
    assert cp["role"] == "MEMORIST"
    assert cp["turn"] >= 5

    # PID file cleaned up.
    assert not (state_dir / "orchestrate.pid").exists()

    # Hydrating a capsule for the session should now find the snapshot
    # and produce a populated workflow block.
    from comfy_moneta_bridge.capsule import write_capsule

    cozy_root = tmp_path / "cozy"
    out = write_capsule("snaptest", cozy_root, moneta)
    capsule = json.loads(out.read_text(encoding="utf-8"))
    assert capsule["workflow"]["current_workflow"] == {
        "1": {"class_type": "KSampler", "inputs": {"seed": 7}}
    }


@pytest.mark.asyncio
async def test_orchestrator_refuses_when_tail_running(tmp_path) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "tail.pid").write_text("12345", encoding="utf-8")

    from comfy_moneta_bridge.agents.harness import OrchestrationLockedError

    orch = Orchestrator(
        driver=StubDriver([]),
        moneta_storage_path=tmp_path / "moneta",
        state_dir=state_dir,
    )
    with pytest.raises(OrchestrationLockedError):
        await orch.run("anything")


@pytest.mark.asyncio
async def test_orchestrator_critic_reject_loops_back(tmp_path) -> None:
    """CRITIC verdict REJECT routes back to MUTATOR for another pass."""

    scripted = [
        ("PLANNER", RoleTurnOutput(text="plan")),
        ("MUTATOR", RoleTurnOutput(text="edit v1")),
        ("EXECUTOR", RoleTurnOutput(text="cannot submit; no workflow loaded")),
        ("CRITIC", RoleTurnOutput(text="not good", verdict="REJECT: try again")),
        ("MUTATOR", RoleTurnOutput(text="edit v2")),
        ("EXECUTOR", RoleTurnOutput(text="still nothing")),
        ("CRITIC", RoleTurnOutput(text="abort", verdict="ABORT: gave up")),
        ("MEMORIST", RoleTurnOutput(
            text="recording blocker",
            tool_calls=[
                ToolCall(
                    id="c1", name="deposit_outcome",
                    args={
                        "_kind": "blocker",
                        "reason": "agent could not converge",
                    },
                ),
            ],
        )),
    ]

    orch = Orchestrator(
        driver=StubDriver(scripted),
        moneta_storage_path=tmp_path / "moneta",
        state_dir=tmp_path / "state",
    )
    result = await orch.run("impossible task", session="loop_test")
    assert result.completed is True
    assert result.critic_verdict and "ABORT" in result.critic_verdict


@pytest.mark.asyncio
async def test_orchestrator_max_turns_halts(tmp_path) -> None:
    """If the scripted driver exceeds max_turns, orchestrator halts."""

    scripted = [
        ("PLANNER", RoleTurnOutput(text="plan")),
        ("MUTATOR", RoleTurnOutput(text="m")),
    ]

    orch = Orchestrator(
        driver=StubDriver(scripted),
        moneta_storage_path=tmp_path / "moneta",
        state_dir=tmp_path / "state",
        max_turns=2,
    )
    result = await orch.run("x", session="halt_test")
    assert result.completed is False


@pytest.mark.asyncio
async def test_orchestrator_executor_awaits_result(tmp_path) -> None:
    """EXECUTOR submits then awaits; the await result lands in the
    transcript and the checkpoint records the prompt_id."""
    moneta = tmp_path / "moneta"
    state_dir = tmp_path / "state"

    class _FakeComfy:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get_object_info(self):
            return {"KSampler": {"input": {"required": {}}}}

        async def post_prompt(self, wf):
            return "pid-await"

        async def await_result(self, prompt_id, timeout_s=300.0):
            return {"status": "success", "prompt_id": prompt_id,
                    "history": {prompt_id: {"status": {"status_str": "success"}}}}

    async def _client_factory():
        return _FakeComfy()

    scripted = [
        ("PLANNER", RoleTurnOutput(
            text="plan",
            tool_calls=[ToolCall(id="c1", name="workflow_load",
                                 args={"workflow": {"1": {"class_type": "KSampler"}}})],
        )),
        ("MUTATOR", RoleTurnOutput(text="no edit needed")),
        ("EXECUTOR", RoleTurnOutput(
            text="submit + await",
            tool_calls=[
                ToolCall(id="c2", name="workflow_submit", args={}),
                ToolCall(id="c3", name="workflow_await_result",
                         args={"prompt_id": "pid-await"}),
            ],
        )),
        ("CRITIC", RoleTurnOutput(text="render ok", verdict="ACCEPT")),
        ("MEMORIST", RoleTurnOutput(
            text="record",
            tool_calls=[ToolCall(id="c4", name="deposit_outcome",
                                 args={"workflow_summary": "awaited"})],
        )),
    ]

    orch = Orchestrator(
        driver=StubDriver(scripted),
        moneta_storage_path=moneta,
        state_dir=state_dir,
        client_factory=_client_factory,
    )
    result = await orch.run("await test", session="awaitsess")
    assert result.completed is True
    assert result.prompt_id == "pid-await"
    # The await result reached the transcript for CRITIC to read.
    await_entries = [
        e for e in result.transcript
        if e.get("tool") == "workflow_await_result"
    ]
    assert await_entries
    assert await_entries[0]["result"]["status"] == "success"


@pytest.mark.asyncio
async def test_orchestrator_resume_skips_resubmit(tmp_path) -> None:
    """Crash-resume: a checkpoint with an in-flight prompt_id must not
    trigger a second post_prompt when EXECUTOR re-runs."""
    from comfy_moneta_bridge.agents.harness import (
        CheckpointStore,
        OrchestrationCheckpoint,
    )

    moneta = tmp_path / "moneta"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)

    # Simulate a crash after submit: the EXECUTOR turn dispatched
    # workflow_submit and record_inflight_submission stamped the
    # prompt_id onto the still-current MUTATOR checkpoint, then the
    # process died before the end-of-turn advance.
    store = CheckpointStore(state_dir / "checkpoints.json")
    store.set("g-resume", OrchestrationCheckpoint(
        goal_id="g-resume", goal="bump seed", session="resume",
        role="MUTATOR", turn=1, prompt_id="inflight-p1", timestamp=1.0,
    ))

    posted: list = []

    class _FakeComfy:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get_object_info(self):
            return {"KSampler": {"input": {"required": {}}}}

        async def post_prompt(self, wf):  # must NOT be called on resume
            posted.append(wf)
            return "SHOULD-NOT-HAPPEN"

        async def await_result(self, prompt_id, timeout_s=300.0):
            return {"status": "success", "prompt_id": prompt_id, "history": {}}

    async def _client_factory():
        return _FakeComfy()

    scripted = [
        # Resume starts at MUTATOR (per the seeded checkpoint).
        ("MUTATOR", RoleTurnOutput(
            text="re-mutate",
            tool_calls=[ToolCall(id="r1", name="workflow_load",
                                 args={"workflow": {"1": {"class_type": "KSampler"}}})],
        )),
        ("EXECUTOR", RoleTurnOutput(
            text="re-submit (should be short-circuited) + await",
            tool_calls=[
                ToolCall(id="r2", name="workflow_submit", args={}),
                ToolCall(id="r3", name="workflow_await_result",
                         args={"prompt_id": "inflight-p1"}),
            ],
        )),
        ("CRITIC", RoleTurnOutput(text="ok", verdict="ACCEPT")),
        ("MEMORIST", RoleTurnOutput(
            text="record",
            tool_calls=[ToolCall(id="r4", name="deposit_outcome",
                                 args={"workflow_summary": "resumed"})],
        )),
    ]

    orch = Orchestrator(
        driver=StubDriver(scripted),
        moneta_storage_path=moneta,
        state_dir=state_dir,
        client_factory=_client_factory,
    )
    result = await orch.run("bump seed", session="resume", goal_id="g-resume")
    assert result.completed is True
    # The core guarantee: no second submission to ComfyUI.
    assert posted == []
    # The reused prompt_id flowed through.
    resumed = [e for e in result.transcript
               if e.get("tool") == "workflow_submit"]
    assert resumed and resumed[0]["result"].get("resumed") is True
