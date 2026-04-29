"""Tests for comfy_moneta_bridge.vector — deterministic synthetic embedder.

Per mission v3.1 Phase 1 test table. CRUCIBLE bias: tests assert exact
equality where possible, hard numeric tolerances otherwise, and explicitly
exercise unicode + empty-string inputs that would silently corrupt under
naive encoding.
"""

from __future__ import annotations

import math

from comfy_moneta_bridge.vector import DIMENSION, synthesize_vector


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb)


def test_same_session_produces_identical_vector() -> None:
    a = synthesize_vector("default")
    b = synthesize_vector("default")
    # Exact equality — same RNG seed must reproduce the exact float sequence.
    assert a == b


def test_different_sessions_produce_different_vectors() -> None:
    a = synthesize_vector("default")
    b = synthesize_vector("experimental")
    assert a != b
    # Cosine similarity well under 0.5 — orthogonal-ish gauss draws should
    # average near 0 in expectation; concrete value depends on the seed
    # but for sha256-derived seeds is empirically << 0.5.
    assert abs(_cosine(a, b)) < 0.5


def test_output_is_unit_length() -> None:
    v = synthesize_vector("default")
    norm_sq = sum(x * x for x in v)
    assert abs(norm_sq - 1.0) < 1e-9


def test_dimensionality_matches_constant() -> None:
    v = synthesize_vector("default")
    assert len(v) == DIMENSION
    assert DIMENSION == 384


def test_unicode_session_name() -> None:
    v = synthesize_vector("séssîön_测试")
    assert len(v) == DIMENSION
    assert abs(sum(x * x for x in v) - 1.0) < 1e-9
    # Same unicode string → same vector (utf-8 encoding is deterministic).
    again = synthesize_vector("séssîön_测试")
    assert v == again


def test_empty_string_handled() -> None:
    v = synthesize_vector("")
    assert len(v) == DIMENSION
    assert abs(sum(x * x for x in v) - 1.0) < 1e-9
    again = synthesize_vector("")
    assert v == again
