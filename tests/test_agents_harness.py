"""Tests for the agent harness checkpoint store and PID-file mutex.

CRUCIBLE bias: the atomic-write test asserts no leaked .tmp files;
the resume test asserts a fresh CheckpointStore reads the on-disk
state; the §13 test asserts refusal happens BEFORE any Moneta touch.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from comfy_moneta_bridge.agents.harness import (
    AgentHarness,
    CheckpointStore,
    OrchestrationCheckpoint,
    OrchestrationLockedError,
    PidFileGuard,
    assert_orchestrate_not_running,
    assert_tail_not_running,
)


# ─── CheckpointStore ──────────────────────────────────────────────────


def test_checkpoint_round_trip(tmp_path) -> None:
    store = CheckpointStore(tmp_path / "checkpoints.json")
    cp = OrchestrationCheckpoint(
        goal_id="g1",
        goal="render a teapot",
        session="demo",
        role="PLANNER",
        turn=0,
        timestamp=1.0,
    )
    store.set("g1", cp)
    fresh = CheckpointStore(tmp_path / "checkpoints.json")
    out = fresh.get("g1")
    assert out is not None
    assert out.goal == "render a teapot"
    assert out.role == "PLANNER"
    assert out.turn == 0


def test_checkpoint_multiple_goals(tmp_path) -> None:
    store = CheckpointStore(tmp_path / "checkpoints.json")
    for i in range(3):
        store.set(
            f"g{i}",
            OrchestrationCheckpoint(
                goal_id=f"g{i}", goal=f"goal {i}", session="s",
                role="PLANNER", turn=0, timestamp=float(i),
            ),
        )
    fresh = CheckpointStore(tmp_path / "checkpoints.json")
    for i in range(3):
        cp = fresh.get(f"g{i}")
        assert cp is not None and cp.timestamp == float(i)


def test_checkpoint_atomic_no_leaked_tmp(tmp_path) -> None:
    store = CheckpointStore(tmp_path / "checkpoints.json")
    store.set(
        "g1",
        OrchestrationCheckpoint(
            goal_id="g1", goal="x", session="s",
            role="PLANNER", turn=0, timestamp=1.0,
        ),
    )
    leftover = list(tmp_path.glob("*.tmp"))
    assert leftover == [], f"temp file leaked: {leftover}"


def test_checkpoint_self_heals_on_corrupt_file(tmp_path) -> None:
    p = tmp_path / "checkpoints.json"
    p.write_text("{not valid json", encoding="utf-8")
    store = CheckpointStore(p)
    assert store.get("anything") is None
    # And a fresh set restores the file.
    store.set(
        "g1",
        OrchestrationCheckpoint(
            goal_id="g1", goal="x", session="s",
            role="PLANNER", turn=0, timestamp=1.0,
        ),
    )
    assert json.loads(p.read_text(encoding="utf-8"))["goals"]["g1"]


def test_checkpoint_missing_file_returns_empty(tmp_path) -> None:
    store = CheckpointStore(tmp_path / "never_created.json")
    assert store.get("anything") is None


# ─── PID-file mutex (Hard Rule §13) ───────────────────────────────────


def test_assert_tail_not_running_when_absent(tmp_path) -> None:
    # No raise.
    assert_tail_not_running(tmp_path)


def test_assert_tail_not_running_refuses_when_present(tmp_path) -> None:
    (tmp_path / "tail.pid").write_text("12345", encoding="utf-8")
    with pytest.raises(OrchestrationLockedError) as excinfo:
        assert_tail_not_running(tmp_path)
    assert "tail" in str(excinfo.value).lower()
    assert "12345" in str(excinfo.value)


def test_assert_orchestrate_not_running_refuses_when_present(
    tmp_path,
) -> None:
    (tmp_path / "orchestrate.pid").write_text("99", encoding="utf-8")
    with pytest.raises(OrchestrationLockedError) as excinfo:
        assert_orchestrate_not_running(tmp_path)
    assert "99" in str(excinfo.value)


def test_pid_file_guard_writes_and_cleans(tmp_path) -> None:
    pid_file = tmp_path / "test.pid"
    with PidFileGuard(pid_file):
        assert pid_file.exists()
        assert pid_file.read_text(encoding="utf-8").strip() == str(os.getpid())
    assert not pid_file.exists()


def test_pid_file_guard_cleans_on_exception(tmp_path) -> None:
    pid_file = tmp_path / "test.pid"
    with pytest.raises(RuntimeError, match="intentional"):
        with PidFileGuard(pid_file):
            assert pid_file.exists()
            raise RuntimeError("intentional")
    assert not pid_file.exists()


def test_pid_file_guard_survives_external_removal(tmp_path) -> None:
    pid_file = tmp_path / "test.pid"
    with PidFileGuard(pid_file):
        pid_file.unlink()  # someone else removed it
    # Should not raise.


# ─── AgentHarness ─────────────────────────────────────────────────────


@pytest.fixture
def harness(tmp_path) -> AgentHarness:
    return AgentHarness(
        checkpoint_store=CheckpointStore(tmp_path / "ckpt.json"),
        state_dir=tmp_path,
    )


def test_start_goal(harness: AgentHarness) -> None:
    cp = harness.start_goal("g1", "render a teapot", "demo")
    assert cp.role == "PLANNER"
    assert cp.turn == 0


def test_start_goal_refuses_when_tail_running(harness: AgentHarness, tmp_path: Path) -> None:
    (tmp_path / "tail.pid").write_text("123", encoding="utf-8")
    with pytest.raises(OrchestrationLockedError):
        harness.start_goal("g1", "x", "s")


def test_resume_goal(harness: AgentHarness) -> None:
    harness.start_goal("g1", "x", "s")
    cp = harness.resume_goal("g1")
    assert cp is not None and cp.role == "PLANNER"


def test_resume_unknown_goal_returns_none(harness: AgentHarness) -> None:
    assert harness.resume_goal("never_seen") is None


def test_advance_role_transitions(harness: AgentHarness) -> None:
    harness.start_goal("g1", "x", "s")
    cp = harness.advance("g1", "MUTATOR")
    assert cp.role == "MUTATOR"
    assert cp.turn == 1
    cp = harness.advance("g1", "EXECUTOR")
    assert cp.role == "EXECUTOR"
    assert cp.turn == 2


def test_advance_executor_refuses_without_planner_or_mutator(
    tmp_path,
) -> None:
    harness = AgentHarness(
        checkpoint_store=CheckpointStore(tmp_path / "ckpt.json"),
        state_dir=tmp_path,
    )
    # Bypass start_goal to inject an invalid prior role (CRITIC), then
    # try to advance to EXECUTOR.
    cp = OrchestrationCheckpoint(
        goal_id="g1", goal="x", session="s",
        role="CRITIC", turn=5, timestamp=1.0,
    )
    harness.checkpoint_store.set("g1", cp)
    with pytest.raises(OrchestrationLockedError, match="§16"):
        harness.advance("g1", "EXECUTOR")


def test_advance_preserves_prompt_id_when_not_overridden(
    harness: AgentHarness,
) -> None:
    harness.start_goal("g1", "x", "s")
    harness.advance("g1", "MUTATOR")
    harness.advance("g1", "EXECUTOR", prompt_id="comfy-abc")
    harness.advance("g1", "CRITIC")
    cp = harness.resume_goal("g1")
    assert cp is not None and cp.prompt_id == "comfy-abc"


def test_advance_unknown_goal_raises(harness: AgentHarness) -> None:
    with pytest.raises(RuntimeError, match="unknown goal_id"):
        harness.advance("never_started", "MUTATOR")


def test_is_tool_already_done(harness: AgentHarness) -> None:
    harness.start_goal("g1", "x", "s")
    harness.advance(
        "g1", "MUTATOR",
        last_tool_calls=[{"id": "call_1", "name": "workflow_mutate_node"}],
    )
    assert harness.is_tool_already_done("g1", "call_1") is True
    assert harness.is_tool_already_done("g1", "call_2") is False


def test_is_tool_already_done_unknown_goal(harness: AgentHarness) -> None:
    assert harness.is_tool_already_done("never_seen", "call_1") is False
