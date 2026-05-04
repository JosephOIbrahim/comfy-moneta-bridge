"""Loader for ``REVIEW_CONSTITUTION.md`` (the bespoke review constitution).

The file lives at the repo root as a sibling of ``AGENT_COMMANDMENTS.md``.
Loaded once per harness run and cached; cached as an Anthropic prompt
breakpoint so the constitution travels with every agent call at zero
incremental cost after the first call.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from .artifacts import repo_root


@lru_cache(maxsize=1)
def load() -> str:
    """Return the raw markdown of the review constitution."""
    path: Path = repo_root() / "REVIEW_CONSTITUTION.md"
    if not path.is_file():
        raise FileNotFoundError(f"REVIEW_CONSTITUTION.md not found at {path}")
    return path.read_text(encoding="utf-8")


def article_count() -> int:
    """Number of §R articles defined in the constitution."""
    text = load()
    return sum(1 for line in text.splitlines() if line.startswith("## §R"))
