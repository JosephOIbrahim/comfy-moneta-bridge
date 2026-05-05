"""Live BGE-small smoke tests — skipped if sentence-transformers absent.

Verifies that ``encode_outcome`` against the real
``BAAI/bge-small-en-v1.5`` checkpoint produces 384-dim L2-normalized
vectors, that identical outcomes encode identically, and that
semantically distinct outcomes encode differently.

First run downloads ~100MB of weights to the HF cache; subsequent
runs are fast. Module-level ``importorskip`` keeps CI green when the
``embeddings`` extra is not installed.
"""

from __future__ import annotations

import math

import pytest

pytest.importorskip("sentence_transformers")

from comfy_moneta_bridge import vector as vector_mod
from comfy_moneta_bridge.vector import DIMENSION, encode_outcome


@pytest.fixture(autouse=True)
def _reset_singleton(monkeypatch):
    """Force a fresh BGE load per test? No — keep the singleton between
    tests in this module so we only pay the load cost once. This fixture
    exists as a hook for any future reset needs."""
    yield


def _l2(v: list[float]) -> float:
    return math.sqrt(sum(x * x for x in v))


def test_real_bge_dimension():
    v = encode_outcome(
        {"session": "default", "workflow_summary": "studio portrait"}
    )
    assert len(v) == DIMENSION


def test_real_bge_unit_norm():
    v = encode_outcome(
        {"session": "default", "workflow_summary": "studio portrait"}
    )
    # BGE returns float32 — slightly looser tolerance than the synthetic path.
    assert abs(_l2(v) - 1.0) < 1e-5


def test_real_bge_deterministic():
    outcome = {
        "session": "alpha",
        "workflow_summary": "stormy seascape at dusk",
        "vision_notes": ["high contrast", "warm rim light"],
    }
    v1 = encode_outcome(outcome)
    v2 = encode_outcome(outcome)
    # BGE is deterministic for identical text input.
    assert v1 == v2


def test_real_bge_distinguishes_distinct_content():
    a = encode_outcome(
        {"session": "alpha", "workflow_summary": "happy bright sunny meadow"}
    )
    b = encode_outcome(
        {"session": "beta", "workflow_summary": "dark stormy haunted forest"}
    )
    # Cosine similarity should be well below 1.0 for clearly distinct text.
    cos = sum(x * y for x, y in zip(a, b))
    assert cos < 0.95, f"distinct outcomes too similar: cos={cos}"


def test_real_bge_session_stub_has_signal():
    """capsule.py's session-only query stub still produces a usable vector."""
    v = encode_outcome({"session": "session_a"})
    assert len(v) == DIMENSION
    assert abs(_l2(v) - 1.0) < 1e-5
