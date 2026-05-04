"""Verifies the constitution loads and contains all 13 articles."""

from __future__ import annotations

from review.constitution import article_count, load


def test_constitution_loads_non_empty():
    text = load()
    assert len(text) > 1000


def test_constitution_has_thirteen_articles():
    assert article_count() == 13


def test_constitution_has_all_R_numbers():
    text = load()
    for i in range(1, 14):
        assert f"§R{i}" in text, f"§R{i} missing from constitution"


def test_constitution_references_AGENT_COMMANDMENTS():
    """The constitution is the sibling of AGENT_COMMANDMENTS.md and
    must defer to it (§R3)."""
    text = load()
    assert "AGENT_COMMANDMENTS" in text


def test_constitution_references_hard_rules():
    """§R11 must reference the project's Hard Rules §1..§12."""
    text = load()
    assert "Hard Rule" in text or "Hard Rules" in text
