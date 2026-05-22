"""Role system prompts and turn-taking for the agent harness.

The five-role split mirrors AGENTS.md §1:

  PLANNER   — read-only; produces a step plan
  MUTATOR   — edits the workflow; no submit
  EXECUTOR  — submits; no edit
  CRITIC    — read-only; decides whether the result is acceptable
  MEMORIST  — writes outcomes; no workflow touch

Turn-taking is deterministic: PLANNER → MUTATOR → EXECUTOR → CRITIC →
(MEMORIST if accepted else back to MUTATOR with critic feedback).
"""

from __future__ import annotations

from dataclasses import dataclass

ROLE_ORDER = ("PLANNER", "MUTATOR", "EXECUTOR", "CRITIC", "MEMORIST")


@dataclass(frozen=True)
class RoleSpec:
    name: str
    charter: str  # one-paragraph system-prompt fragment


ROLES: dict[str, RoleSpec] = {
    "PLANNER": RoleSpec(
        name="PLANNER",
        charter=(
            "You are the PLANNER. Your job is to read the goal, query "
            "memory for relevant past outcomes via recall_memory, "
            "inspect the current workflow via workflow_load, and "
            "produce a numbered list of concrete mutations the MUTATOR "
            "should perform. You do NOT mutate the workflow. You do "
            "NOT submit. Your output is a plan, nothing else."
        ),
    ),
    "MUTATOR": RoleSpec(
        name="MUTATOR",
        charter=(
            "You are the MUTATOR. You apply the PLANNER's plan by "
            "calling workflow_mutate_node, workflow_connect, "
            "workflow_remove_node as needed. After each significant "
            "change, call workflow_validate to confirm the workflow "
            "is still well-formed. You do NOT submit; the EXECUTOR "
            "does that. Stay strictly inside the plan; do not add "
            "unrelated mutations."
        ),
    ),
    "EXECUTOR": RoleSpec(
        name="EXECUTOR",
        charter=(
            "You are the EXECUTOR. Call workflow_validate one final "
            "time; if it passes, call workflow_submit and report the "
            "returned prompt_id. If validation fails, do NOT submit; "
            "report the errors so the next round can mutate to fix "
            "them. You do NOT edit the workflow yourself."
        ),
    ),
    "CRITIC": RoleSpec(
        name="CRITIC",
        charter=(
            "You are the CRITIC. The submission has completed (or "
            "failed). Decide whether the result satisfies the goal. "
            "You may call recall_memory to compare against past "
            "outcomes. Output exactly one of: ACCEPT, REJECT (with a "
            "short reason), or ABORT (with a short reason)."
        ),
    ),
    "MEMORIST": RoleSpec(
        name="MEMORIST",
        charter=(
            "You are the MEMORIST. Persist what happened. Call "
            "deposit_outcome with the run's vision_notes, key_params, "
            "and quality_score. If a workflow was submitted, call "
            "deposit_outcome again with _kind=workflow_snapshot and "
            "the final workflow JSON. Optionally call capsule_write "
            "to hydrate a Comfy-Cozy session file. Do NOT touch the "
            "workflow."
        ),
    ),
}


def system_prompt_for(role: str, constitution_text: str) -> str:
    """Compose the system prompt for ``role``: constitution + charter."""
    if role not in ROLES:
        raise KeyError(f"unknown role {role!r}; valid: {sorted(ROLES)}")
    return (
        f"# Runtime Constitution\n\n{constitution_text}\n\n"
        f"# Active Role: {role}\n\n{ROLES[role].charter}\n"
    )


def next_role(current: str, critic_verdict: str | None = None) -> str | None:
    """Deterministic turn-taking.

    PLANNER → MUTATOR → EXECUTOR → CRITIC.
    On CRITIC verdict ACCEPT → MEMORIST → done (returns None).
    On CRITIC verdict REJECT → back to MUTATOR (next iteration).
    On CRITIC verdict ABORT → straight to MEMORIST (record blocker).
    """
    if current == "PLANNER":
        return "MUTATOR"
    if current == "MUTATOR":
        return "EXECUTOR"
    if current == "EXECUTOR":
        return "CRITIC"
    if current == "CRITIC":
        if critic_verdict is None:
            raise ValueError("CRITIC handoff requires a verdict")
        v = critic_verdict.strip().upper()
        if v.startswith("ACCEPT"):
            return "MEMORIST"
        if v.startswith("REJECT"):
            return "MUTATOR"
        if v.startswith("ABORT"):
            return "MEMORIST"
        raise ValueError(
            f"unrecognized critic verdict {critic_verdict!r}; "
            "expected ACCEPT / REJECT / ABORT"
        )
    if current == "MEMORIST":
        return None
    raise KeyError(f"unknown role {current!r}")
