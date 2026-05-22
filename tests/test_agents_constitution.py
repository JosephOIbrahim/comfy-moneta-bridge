"""Tests for ``constitution.py`` and ``roles.py``."""

from __future__ import annotations

import pytest

from comfy_moneta_bridge.agents.constitution import (
    ROLE_ALLOWLIST,
    default_constitution_path,
    load_constitution,
)
from comfy_moneta_bridge.agents.roles import (
    ROLE_ORDER,
    ROLES,
    next_role,
    system_prompt_for,
)


def test_default_constitution_path_resolves_to_agents_md() -> None:
    p = default_constitution_path()
    assert p.name == "AGENTS.md"


def test_load_constitution_from_default(tmp_path) -> None:
    # The repo's AGENTS.md should exist.
    text = load_constitution()
    assert "Runtime Constitution" in text
    assert "Role allowlists" in text


def test_load_constitution_missing_raises(tmp_path) -> None:
    with pytest.raises(FileNotFoundError, match="not found"):
        load_constitution(tmp_path / "absent.md")


def test_load_constitution_custom_path(tmp_path) -> None:
    p = tmp_path / "my_agents.md"
    p.write_text("# custom constitution\n", encoding="utf-8")
    assert load_constitution(p) == "# custom constitution\n"


def test_role_allowlist_has_all_roles() -> None:
    assert set(ROLE_ALLOWLIST) == set(ROLES)


def test_role_allowlist_planner_is_read_only() -> None:
    planner = ROLE_ALLOWLIST["PLANNER"]
    forbidden = {
        "workflow_mutate_node", "workflow_connect", "workflow_remove_node",
        "workflow_submit", "workflow_interrupt", "deposit_outcome",
        "capsule_write",
    }
    assert not (planner & forbidden)


def test_role_allowlist_executor_cannot_mutate() -> None:
    executor = ROLE_ALLOWLIST["EXECUTOR"]
    forbidden = {
        "workflow_mutate_node", "workflow_connect", "workflow_remove_node",
    }
    assert not (executor & forbidden)


def test_role_allowlist_mutator_cannot_submit() -> None:
    mutator = ROLE_ALLOWLIST["MUTATOR"]
    forbidden = {"workflow_submit", "workflow_interrupt"}
    assert not (mutator & forbidden)


def test_role_allowlist_memorist_cannot_touch_workflow() -> None:
    memorist = ROLE_ALLOWLIST["MEMORIST"]
    forbidden = {
        "workflow_mutate_node", "workflow_connect", "workflow_remove_node",
        "workflow_submit", "workflow_interrupt",
    }
    assert not (memorist & forbidden)


# ─── Role transitions ─────────────────────────────────────────────────


def test_role_order() -> None:
    assert ROLE_ORDER == (
        "PLANNER", "MUTATOR", "EXECUTOR", "CRITIC", "MEMORIST",
    )


def test_next_role_happy_path() -> None:
    assert next_role("PLANNER") == "MUTATOR"
    assert next_role("MUTATOR") == "EXECUTOR"
    assert next_role("EXECUTOR") == "CRITIC"
    assert next_role("CRITIC", "ACCEPT") == "MEMORIST"
    assert next_role("MEMORIST") is None


def test_next_role_critic_reject_loops_to_mutator() -> None:
    assert next_role("CRITIC", "REJECT: too blurry") == "MUTATOR"


def test_next_role_critic_abort_to_memorist() -> None:
    assert next_role("CRITIC", "ABORT: hardware error") == "MEMORIST"


def test_next_role_critic_requires_verdict() -> None:
    with pytest.raises(ValueError, match="requires a verdict"):
        next_role("CRITIC", None)


def test_next_role_critic_unknown_verdict_raises() -> None:
    with pytest.raises(ValueError, match="unrecognized"):
        next_role("CRITIC", "MAYBE")


def test_next_role_unknown_role_raises() -> None:
    with pytest.raises(KeyError):
        next_role("HACKER")


# ─── System prompt composition ────────────────────────────────────────


def test_system_prompt_composes_constitution_and_charter() -> None:
    prompt = system_prompt_for("PLANNER", "MY_CONSTITUTION_TEXT")
    assert "MY_CONSTITUTION_TEXT" in prompt
    assert "Active Role: PLANNER" in prompt
    assert "PLANNER" in prompt


def test_system_prompt_unknown_role_raises() -> None:
    with pytest.raises(KeyError):
        system_prompt_for("HACKER", "x")


@pytest.mark.parametrize("role", list(ROLES.keys()))
def test_every_role_has_a_charter(role) -> None:
    assert ROLES[role].charter
    assert ROLES[role].name == role
