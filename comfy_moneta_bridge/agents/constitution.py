"""Runtime constitution loader for the agents subpackage.

Loads ``AGENTS.md`` from the repo root (or an explicit path) at the
start of each orchestration. Per the v3.2 design choice, the
constitution is re-read per orchestration — not module-level cached —
so an operator can edit ``AGENTS.md`` and have the change take effect
on the next ``bridge orchestrate`` invocation without restarting any
service.

The loaded text is injected into every role's system prompt by
``roles.py``. This module also exports the small role allowlist map
that ``tools.dispatch`` consults; the allowlist is duplicated here for
runtime enforcement so a stale ``AGENTS.md`` cannot weaken the gate.
"""

from __future__ import annotations

from pathlib import Path

# The single source of truth for role-tool allowlists at runtime. This
# mirrors §1 of AGENTS.md but lives in code so a malformed or absent
# AGENTS.md cannot weaken the gate. If §1 of AGENTS.md is edited, this
# map must be updated in lockstep (a CRUCIBLE test catches drift).
ROLE_ALLOWLIST: dict[str, set[str]] = {
    "PLANNER": {"workflow_load", "recall_memory"},
    "MUTATOR": {
        "workflow_load",
        "workflow_mutate_node",
        "workflow_connect",
        "workflow_remove_node",
        "workflow_validate",
    },
    "EXECUTOR": {
        "workflow_validate",
        "workflow_submit",
        "workflow_interrupt",
    },
    "CRITIC": {"recall_memory"},
    "MEMORIST": {"deposit_outcome", "capsule_write", "recall_memory"},
}


def default_constitution_path() -> Path:
    """The repo-root ``AGENTS.md`` shipped with the bridge."""
    return Path(__file__).resolve().parent.parent.parent / "AGENTS.md"


def load_constitution(path: Path | None = None) -> str:
    """Read AGENTS.md as text. Raises if the file is missing.

    Called once per orchestration, never module-cached. Operators can
    edit AGENTS.md between orchestrations and the change takes effect
    on the next ``bridge orchestrate`` invocation.
    """
    resolved = path or default_constitution_path()
    if not resolved.exists():
        raise FileNotFoundError(
            f"AGENTS.md not found at {resolved}. The runtime constitution "
            "is required (BRIDGE_BUILD_MISSION_v3_2.md scope addition E)."
        )
    return resolved.read_text(encoding="utf-8")
