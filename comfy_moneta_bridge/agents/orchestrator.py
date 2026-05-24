"""Single-goal orchestration driver.

Wraps ``AgentHarness`` + an LLM (the internal Claude loop or an
external MCP client) + ``tools.dispatch``. Drives one goal through
the PLANNER → MUTATOR → EXECUTOR → CRITIC → MEMORIST role machine.

The orchestrator owns the Moneta handle implicitly (each
``deposit_outcome`` call opens an ephemeral handle per Rule §6, and
Rule §13 ensures Tailer isn't racing it for the URI lock). At goal
completion, the MEMORIST role calls ``deposit_outcome`` twice — once
for the run outcome, once for the workflow snapshot — so the existing
``write_capsule`` snapshot-extraction path picks up the workflow on
the next ``bridge hydrate``.

The orchestrator does NOT instantiate an LLM client directly. It
accepts a ``RoleDriver`` protocol — anything that can, given a system
prompt + recent transcript, return the next assistant message and any
tool calls. The internal Claude loop is one implementation; tests
inject a stub.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol

from comfy_moneta_bridge.agents.client import ComfyClient
from comfy_moneta_bridge.agents.constitution import (
    ROLE_ALLOWLIST,
    load_constitution,
)
from comfy_moneta_bridge.agents.harness import (
    AgentHarness,
    CheckpointStore,
    OrchestrationLockedError,
    PidFileGuard,
    assert_tail_not_running,
)
from comfy_moneta_bridge.agents.roles import (
    next_role as next_role_fn,
    system_prompt_for,
)
from comfy_moneta_bridge.agents.tools import (
    AgentContext,
    RefusalError,
    dispatch,
)
from comfy_moneta_bridge.capsule import WORKFLOW_SNAPSHOT_KIND

_logger = logging.getLogger(__name__)


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict


@dataclass
class RoleTurnOutput:
    """One LLM turn: assistant text + zero-or-more tool calls."""

    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    verdict: str | None = None  # CRITIC only


class RoleDriver(Protocol):
    """Anything that can drive one role turn.

    The internal Claude loop is the production implementation; tests
    inject a deterministic stub.
    """

    async def step(
        self,
        role: str,
        system_prompt: str,
        goal: str,
        transcript: list[dict],
    ) -> RoleTurnOutput: ...


@dataclass
class OrchestrationResult:
    goal_id: str
    goal: str
    session: str
    final_role: str
    critic_verdict: str | None
    prompt_id: str | None
    completed: bool
    transcript: list[dict] = field(default_factory=list)


class Orchestrator:
    """One-shot driver for a single goal.

    Lifecycle:
      1. Construction does no work.
      2. ``run(goal, session)`` writes the orchestrate.pid file,
         constructs the AgentContext (with ComfyClient if available),
         and loops through roles until done or max_turns exceeded.
      3. On exit (success or failure), the pid file is removed.

    Crash recovery: if a previous run was killed mid-loop, calling
    ``run(goal, session, goal_id=<prior_id>)`` resumes from the latest
    checkpoint.
    """

    def __init__(
        self,
        driver: RoleDriver,
        moneta_storage_path: Path,
        state_dir: Path,
        cozy_root: Path | None = None,
        max_turns: int = 12,
        constitution_path: Path | None = None,
        client_factory: Callable[[], Awaitable[ComfyClient | None]]
        | None = None,
        interactive: bool = False,
        ack_planner: Callable[[str], Awaitable[bool]] | None = None,
    ) -> None:
        self.driver = driver
        self.moneta_storage_path = Path(moneta_storage_path)
        self.state_dir = Path(state_dir)
        self.cozy_root = Path(cozy_root) if cozy_root else None
        self.max_turns = max_turns
        self.constitution_path = constitution_path
        self.client_factory = client_factory
        self.interactive = interactive
        self.ack_planner = ack_planner

    async def run(
        self,
        goal: str,
        session: str = "default",
        goal_id: str | None = None,
    ) -> OrchestrationResult:
        assert_tail_not_running(self.state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)

        constitution_text = load_constitution(self.constitution_path)
        gid = goal_id or uuid.uuid4().hex

        harness = AgentHarness(
            checkpoint_store=CheckpointStore(
                self.state_dir / "checkpoints.json"
            ),
            state_dir=self.state_dir,
        )

        comfy_client: ComfyClient | None = None
        if self.client_factory is not None:
            comfy_client = await self.client_factory()

        ctx = AgentContext(
            moneta_storage_path=self.moneta_storage_path,
            state_dir=self.state_dir,
            session=session,
            comfy_client=comfy_client,
            cozy_root=self.cozy_root,
            role_allowlist=ROLE_ALLOWLIST,
        )

        transcript: list[dict] = []
        prior = harness.resume_goal(gid)
        if prior is None:
            cp = harness.start_goal(gid, goal, session)
            _logger.info("orchestrator: started goal %s", gid)
        else:
            cp = prior
            _logger.info(
                "orchestrator: resumed goal %s at role=%s turn=%d",
                gid, cp.role, cp.turn,
            )

        with PidFileGuard(self.state_dir / "orchestrate.pid"):
            try:
                if comfy_client is not None:
                    await comfy_client.__aenter__()
                result = await self._loop(
                    harness, ctx, gid, goal, session,
                    constitution_text, transcript, cp.role,
                )
                return result
            finally:
                if comfy_client is not None:
                    try:
                        await comfy_client.__aexit__(None, None, None)
                    except Exception:  # noqa: BLE001
                        _logger.warning(
                            "comfy_client teardown failed",
                            exc_info=True,
                        )

    async def _loop(
        self,
        harness: AgentHarness,
        ctx: AgentContext,
        gid: str,
        goal: str,
        session: str,
        constitution_text: str,
        transcript: list[dict],
        starting_role: str,
    ) -> OrchestrationResult:
        role: str | None = starting_role
        verdict: str | None = None
        prompt_id: str | None = None
        turn_count = 0

        while role is not None:
            if turn_count >= self.max_turns:
                _logger.warning(
                    "orchestrator: max_turns=%d reached for goal %s; "
                    "halting at role %s", self.max_turns, gid, role,
                )
                break
            turn_count += 1

            ctx.role = role
            system_prompt = system_prompt_for(role, constitution_text)
            output = await self.driver.step(
                role, system_prompt, goal, transcript
            )
            transcript.append({"role": role, "output": output.text})

            # Hard Rule §16: interactive ack before EXECUTOR.
            if (
                role == "MUTATOR"
                and self.interactive
                and self.ack_planner is not None
            ):
                ok = await self.ack_planner(output.text)
                if not ok:
                    _logger.info(
                        "orchestrator: human declined ack; halting"
                    )
                    break

            executed_calls: list[dict] = []
            for tc in output.tool_calls:
                # Idempotent submit (AGENTS.md §3 / C2): if a submission
                # is already in flight for this goal — i.e. a prior run
                # recorded a prompt_id mid-turn before crashing — do NOT
                # re-submit. Reuse the recorded prompt_id so resume picks
                # up at the await instead of double-posting to ComfyUI.
                if (
                    tc.name == "workflow_submit"
                    and harness.has_inflight_submission(gid)
                ):
                    existing = harness.resume_goal(gid)
                    prompt_id = existing.prompt_id if existing else prompt_id
                    transcript.append(
                        {
                            "role": role, "tool": tc.name,
                            "result": {"prompt_id": prompt_id, "resumed": True},
                        }
                    )
                    executed_calls.append(
                        {"id": tc.id, "name": tc.name,
                         "result": {"prompt_id": prompt_id, "resumed": True}}
                    )
                    continue
                try:
                    res = await dispatch(tc.name, tc.args, ctx)
                except RefusalError as e:
                    transcript.append(
                        {"role": role, "tool_error": str(e), "tool": tc.name}
                    )
                    continue
                executed_calls.append(
                    {"id": tc.id, "name": tc.name, "result": res}
                )
                transcript.append(
                    {"role": role, "tool": tc.name, "result": res}
                )
                if tc.name == "workflow_submit" and isinstance(res, dict):
                    prompt_id = res.get("prompt_id") or prompt_id
                    # Checkpoint the prompt_id immediately, before any
                    # await begins, so a crash mid-render is resumable.
                    if prompt_id:
                        harness.record_inflight_submission(gid, prompt_id)

            if role == "CRITIC":
                verdict = output.verdict

            workflow_state = (
                ctx.workflow.dump() if ctx.workflow is not None else None
            )
            harness.advance(
                gid, role,
                last_tool_calls=executed_calls,
                prompt_id=prompt_id,
                workflow_state=workflow_state,
                critic_verdict=verdict,
            )

            if role == "CRITIC":
                role = next_role_fn("CRITIC", verdict or "ABORT")
            else:
                role = next_role_fn(role)

        completed = role is None
        # MEMORIST hands a workflow snapshot to the bridge as part of
        # its tool calls; if the loop ended early, the orchestrator
        # makes sure at least one deposit_outcome of _kind=outcome
        # exists so the run is recorded in Moneta.
        if not completed and not any(
            entry.get("tool") == "deposit_outcome"
            for entry in transcript
        ):
            try:
                await dispatch(
                    "deposit_outcome",
                    {
                        "vision_notes": [
                            f"orchestration halted at role={role}"
                        ],
                        "key_params": {"goal": goal[:200]},
                        "workflow_summary": (
                            f"halt: {role} after {turn_count} turns"
                        ),
                    },
                    ctx,
                )
            except RefusalError:
                _logger.exception(
                    "orchestrator: fallback deposit_outcome refused"
                )

        return OrchestrationResult(
            goal_id=gid,
            goal=goal,
            session=session,
            final_role=role or "MEMORIST",
            critic_verdict=verdict,
            prompt_id=prompt_id,
            completed=completed,
            transcript=transcript,
        )
