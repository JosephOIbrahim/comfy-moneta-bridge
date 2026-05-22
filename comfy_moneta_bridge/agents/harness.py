"""Long-running async harness for the agent orchestration loop.

Mirrors ``tail.Tailer``'s shape: small async object with one public
``run()`` coroutine. Adds two crash-safety primitives:

  * ``CheckpointStore`` — atomic temp+fsync+os.replace, same pattern
    as ``state.CursorStore``. Persists one ``OrchestrationCheckpoint``
    per goal-id; resume reads the latest and skips already-completed
    tool calls within the current step.
  * PID-file mutex — Hard Rule §13. The harness writes
    ``{state_dir}/orchestrate.pid`` on start and removes it on exit.
    It refuses to start if ``{state_dir}/tail.pid`` exists (Tailer
    process holds the Moneta URI lock). Tailer's PID file write/cleanup
    lives in ``cli.py`` symmetrically.

Hard Rule §16: every role transition writes a checkpoint. The EXECUTOR
gate (also enforced in ``orchestrator.py``) refuses to run without a
fresh PLANNER checkpoint.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

_logger = logging.getLogger(__name__)

TAIL_PID_FILE = "tail.pid"
ORCHESTRATE_PID_FILE = "orchestrate.pid"


class OrchestrationLockedError(RuntimeError):
    """Hard Rule §13: tail and orchestrate are mutually exclusive."""


@dataclass
class OrchestrationCheckpoint:
    """One persisted step in an orchestration."""

    goal_id: str
    goal: str
    session: str
    role: str
    turn: int
    last_tool_calls: list[dict] = field(default_factory=list)
    prompt_id: str | None = None
    workflow_state: dict | None = None
    critic_verdict: str | None = None
    timestamp: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "OrchestrationCheckpoint":
        return cls(
            goal_id=d["goal_id"],
            goal=d["goal"],
            session=d["session"],
            role=d["role"],
            turn=int(d["turn"]),
            last_tool_calls=list(d.get("last_tool_calls", [])),
            prompt_id=d.get("prompt_id"),
            workflow_state=d.get("workflow_state"),
            critic_verdict=d.get("critic_verdict"),
            timestamp=float(d.get("timestamp", 0.0)),
        )


class CheckpointStore:
    """Persistent map of goal-id → latest OrchestrationCheckpoint.

    Atomic writes via temp file + fsync + os.replace — same pattern as
    ``state.CursorStore``. A malformed or missing file resolves to an
    empty store; first successful set() re-establishes the on-disk
    state.
    """

    def __init__(self, checkpoint_path: Path) -> None:
        self.checkpoint_path = Path(checkpoint_path)
        self._cache: dict[str, OrchestrationCheckpoint] = self._load()

    def get(self, goal_id: str) -> OrchestrationCheckpoint | None:
        return self._cache.get(goal_id)

    def set(
        self, goal_id: str, cp: OrchestrationCheckpoint
    ) -> None:
        new_cache = dict(self._cache)
        new_cache[goal_id] = cp

        self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.checkpoint_path.with_suffix(
            self.checkpoint_path.suffix + ".tmp"
        )
        serialized = {
            "goals": {gid: c.to_dict() for gid, c in new_cache.items()}
        }
        with open(tmp_path, "w", encoding="utf-8") as fp:
            json.dump(serialized, fp, ensure_ascii=False, sort_keys=True)
            fp.flush()
            os.fsync(fp.fileno())
        os.replace(tmp_path, self.checkpoint_path)

        self._cache = new_cache

    def _load(self) -> dict[str, OrchestrationCheckpoint]:
        if not self.checkpoint_path.exists():
            return {}
        try:
            with open(self.checkpoint_path, "r", encoding="utf-8") as fp:
                blob = json.load(fp)
            goals = blob.get("goals", {})
            return {
                gid: OrchestrationCheckpoint.from_dict(d)
                for gid, d in goals.items()
            }
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
            _logger.warning(
                "checkpoint file %s is malformed (%s); starting empty",
                self.checkpoint_path, type(e).__name__,
            )
            return {}


# ─── PID-file mutex (Hard Rule §13) ───────────────────────────────────


def assert_tail_not_running(state_dir: Path) -> None:
    """Raise if a ``tail.pid`` file is present in ``state_dir``."""
    tail_pid = Path(state_dir) / TAIL_PID_FILE
    if tail_pid.exists():
        try:
            pid = tail_pid.read_text(encoding="utf-8").strip()
        except OSError:
            pid = "?"
        raise OrchestrationLockedError(
            f"refusing to start: 'bridge tail' is running "
            f"(pid={pid}, file={tail_pid}). Stop it first — they share "
            "the Moneta URI lock (Hard Rule §13 in "
            "BRIDGE_BUILD_MISSION_v3_2.md)."
        )


def assert_orchestrate_not_running(state_dir: Path) -> None:
    """Symmetric refusal for ``bridge tail`` startup."""
    orch_pid = Path(state_dir) / ORCHESTRATE_PID_FILE
    if orch_pid.exists():
        try:
            pid = orch_pid.read_text(encoding="utf-8").strip()
        except OSError:
            pid = "?"
        raise OrchestrationLockedError(
            f"refusing to start: 'bridge orchestrate' or 'bridge mcp' "
            f"is running (pid={pid}, file={orch_pid}). Stop it first — "
            "they share the Moneta URI lock (Hard Rule §13 in "
            "BRIDGE_BUILD_MISSION_v3_2.md)."
        )


class PidFileGuard:
    """Context manager that writes and cleans up a pid file.

    Use as ``with PidFileGuard(state_dir / "orchestrate.pid"):`` to
    ensure the file is removed on normal exit, exception, or signal.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def __enter__(self) -> "PidFileGuard":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(str(os.getpid()), encoding="utf-8")
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        except OSError as e:
            _logger.warning(
                "failed to remove pid file %s: %s", self.path, e
            )
        return False


# ─── Harness ─────────────────────────────────────────────────────────


@dataclass
class AgentHarness:
    """Drives one goal through PLANNER → MUTATOR → EXECUTOR → CRITIC →
    MEMORIST role transitions, writing a checkpoint at every transition.

    The harness does NOT call the LLM directly — that's the
    orchestrator's job. The harness's responsibility is the
    role-machine state, the checkpoint discipline, and the §13/§16
    refusals.
    """

    checkpoint_store: CheckpointStore
    state_dir: Path

    def start_goal(
        self, goal_id: str, goal: str, session: str
    ) -> OrchestrationCheckpoint:
        """Begin a fresh goal. Refuses if tail is running.

        Returns the initial PLANNER checkpoint.
        """
        assert_tail_not_running(self.state_dir)
        cp = OrchestrationCheckpoint(
            goal_id=goal_id,
            goal=goal,
            session=session,
            role="PLANNER",
            turn=0,
            timestamp=time.time(),
        )
        self.checkpoint_store.set(goal_id, cp)
        return cp

    def resume_goal(
        self, goal_id: str
    ) -> OrchestrationCheckpoint | None:
        """Return the latest checkpoint for ``goal_id`` (or None)."""
        return self.checkpoint_store.get(goal_id)

    def advance(
        self,
        goal_id: str,
        new_role: str,
        *,
        last_tool_calls: list[dict] | None = None,
        prompt_id: str | None = None,
        workflow_state: dict | None = None,
        critic_verdict: str | None = None,
    ) -> OrchestrationCheckpoint:
        """Write a new checkpoint reflecting a role transition.

        Enforces Hard Rule §16: advancing INTO EXECUTOR requires the
        previous checkpoint to be PLANNER (or MUTATOR, which itself
        follows PLANNER) and to be in the same orchestration.
        """
        prior = self.checkpoint_store.get(goal_id)
        if prior is None:
            raise RuntimeError(
                f"cannot advance unknown goal_id {goal_id!r}; "
                "call start_goal first"
            )
        if new_role == "EXECUTOR":
            if prior.role not in ("PLANNER", "MUTATOR"):
                raise OrchestrationLockedError(
                    "Hard Rule §16: EXECUTOR refuses without a fresh "
                    f"PLANNER (or MUTATOR) checkpoint; saw role="
                    f"{prior.role!r}"
                )
        cp = OrchestrationCheckpoint(
            goal_id=goal_id,
            goal=prior.goal,
            session=prior.session,
            role=new_role,
            turn=prior.turn + 1,
            last_tool_calls=last_tool_calls or [],
            prompt_id=prompt_id or prior.prompt_id,
            workflow_state=workflow_state or prior.workflow_state,
            critic_verdict=critic_verdict,
            timestamp=time.time(),
        )
        self.checkpoint_store.set(goal_id, cp)
        return cp

    def is_tool_already_done(
        self, goal_id: str, tool_call_id: str
    ) -> bool:
        """Idempotent-replay check: True if the most recent checkpoint
        already records this tool_call_id as completed."""
        cp = self.checkpoint_store.get(goal_id)
        if cp is None:
            return False
        return any(
            c.get("id") == tool_call_id
            for c in cp.last_tool_calls
        )
