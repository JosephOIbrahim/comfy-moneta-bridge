"""Typed dataclasses for the review harness state.

The findings store is the source of truth across iterations. Every
expert's output is a ``Finding``; iteration boundaries append to
``history[]`` rather than mutating prior records. The orchestrator's
durability cursor is ``RunState``.

All dataclasses are ``frozen=True`` for the same reason ``state.py``
uses ``WatchState`` frozen: a crash mid-mutation must not corrupt the
in-memory view of on-disk state.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Literal

Severity = Literal["critical", "high", "medium", "low", "nit"]
Confidence = Literal["proven", "suspected", "speculative"]
FindingStatus = Literal[
    "open",
    "confirmed",
    "refuted",
    "refined",
    "dropped",
    "v1-candidate",
    "final",
]
IterationKind = Literal[
    "discovery",
    "cross-examination",
    "deep-dive",
    "adversarial",
    "rank-and-report",
]

ITERATION_ORDER: tuple[IterationKind, ...] = (
    "discovery",
    "cross-examination",
    "deep-dive",
    "adversarial",
    "rank-and-report",
)

EXPERT_ROLES: tuple[str, ...] = (
    "architect",
    "reliability",
    "security",
    "performance",
    "testing",
    "frozen-boundaries",
    "docs-vs-code",
)


@dataclass(frozen=True)
class HistoryEntry:
    iteration: int
    kind: IterationKind
    actor: str
    action: str
    note: str


@dataclass(frozen=True)
class Finding:
    id: str
    iter_introduced: int
    expert: str
    severity: Severity
    confidence: Confidence
    file: str
    line_start: int
    line_end: int
    title: str
    claim: str
    evidence_quote: str
    remediation: str
    constitution_articles: tuple[str, ...]
    status: FindingStatus
    history: tuple[HistoryEntry, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class TraceEntry:
    iteration: int
    kind: IterationKind
    actor: str
    request_id: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    est_cost_usd: float
    duration_seconds: float


@dataclass(frozen=True)
class RunState:
    run_id: str
    model: str
    iterations_planned: int
    iteration_completed: int
    last_actor: str
    last_action: str


def make_finding_id(file: str, line_start: int, claim: str) -> str:
    """Stable hash of ``(file, line_start, claim)``.

    Synthesizer dedupe key per the plan: two findings collapse if the
    file matches, line_start is within +/- 5, and the claim hash matches.
    The ID itself is exact; the windowed dedupe is computed by the
    synthesizer using ``id_window`` below.
    """
    payload = f"{file}::{line_start}::{claim.strip()}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def id_window(file: str, line_start: int, claim: str, window: int = 5) -> str:
    """Window-bucketed key for cross-expert dedupe."""
    bucket = (line_start // window) * window
    payload = f"{file}::{bucket}::{claim.strip()}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]
