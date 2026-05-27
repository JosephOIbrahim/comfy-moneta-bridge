"""Tests for comfy_moneta_bridge.vector — deterministic synthetic embedder.

Per mission v3.1 Phase 1 test table. CRUCIBLE bias: tests assert exact
equality where possible, hard numeric tolerances otherwise, and explicitly
exercise unicode + empty-string inputs that would silently corrupt under
naive encoding.
"""

from __future__ import annotations

import math

import pytest

from comfy_moneta_bridge import vector as vector_mod
from comfy_moneta_bridge.vector import (
    DIMENSION,
    ENV_VAR,
    encode_outcome,
    from_env,
    synthesize_vector,
)


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


# ----------------------------------------------------------------------
# from_env() — mode resolution from BRIDGE_EMBEDDER_MODE
# ----------------------------------------------------------------------


def test_from_env_default_is_bge(monkeypatch) -> None:
    # Default flipped to bge (leaf L4 / SPEC P2): real embedder by default.
    monkeypatch.delenv(ENV_VAR, raising=False)
    assert from_env() == "bge"


def test_from_env_explicit_synthetic(monkeypatch) -> None:
    monkeypatch.setenv(ENV_VAR, "synthetic")
    assert from_env() == "synthetic"


def test_from_env_bge(monkeypatch) -> None:
    monkeypatch.setenv(ENV_VAR, "bge")
    assert from_env() == "bge"


def test_from_env_case_insensitive(monkeypatch) -> None:
    monkeypatch.setenv(ENV_VAR, "BGE")
    assert from_env() == "bge"


def test_from_env_strips_whitespace(monkeypatch) -> None:
    monkeypatch.setenv(ENV_VAR, "  bge  ")
    assert from_env() == "bge"


def test_from_env_unknown_falls_back(monkeypatch) -> None:
    monkeypatch.setenv(ENV_VAR, "bogus_mode")
    assert from_env() == "bge"  # unrecognised -> default (now bge)


def test_from_env_empty_falls_back(monkeypatch) -> None:
    monkeypatch.setenv(ENV_VAR, "")
    assert from_env() == "bge"  # empty -> default (now bge)


# ----------------------------------------------------------------------
# encode_outcome() — mocked encoder
# ----------------------------------------------------------------------


class _StubBGEModel:
    """Stand-in for sentence_transformers.SentenceTransformer.

    Records every encode() call so tests can assert on the exact text
    BGE would have seen, then returns a deterministic 384-dim vector
    shaped by hashing the input text.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def encode(self, text: str, **kwargs):
        import hashlib
        import random as _random

        self.calls.append((text, kwargs))
        seed = int.from_bytes(
            hashlib.sha256(text.encode("utf-8")).digest()[:8], "big"
        )
        rng = _random.Random(seed)
        raw = [rng.gauss(0.0, 1.0) for _ in range(DIMENSION)]
        if kwargs.get("normalize_embeddings"):
            norm = math.sqrt(sum(x * x for x in raw)) or 1.0
            raw = [x / norm for x in raw]
        return raw


@pytest.fixture
def stub_bge(monkeypatch):
    """Replace the lazy BGE singleton with a recording stub."""
    stub = _StubBGEModel()
    monkeypatch.setattr(vector_mod, "_bge_model", stub)
    return stub


def test_encode_outcome_returns_dim_384(stub_bge) -> None:
    v = encode_outcome({"session": "default", "workflow_summary": "x"})
    assert len(v) == DIMENSION


def test_encode_outcome_unit_norm(stub_bge) -> None:
    v = encode_outcome({"session": "default", "workflow_summary": "x"})
    norm_sq = sum(x * x for x in v)
    assert abs(norm_sq - 1.0) < 1e-9


def test_encode_outcome_passes_normalize_flag(stub_bge) -> None:
    encode_outcome({"session": "default"})
    assert stub_bge.calls[-1][1].get("normalize_embeddings") is True


def test_encode_outcome_text_includes_session(stub_bge) -> None:
    encode_outcome({"session": "alpha"})
    assert "session: alpha" in stub_bge.calls[-1][0]


def test_encode_outcome_text_includes_workflow_summary(stub_bge) -> None:
    encode_outcome({"session": "a", "workflow_summary": "shot 17"})
    text = stub_bge.calls[-1][0]
    assert "workflow: shot 17" in text


def test_encode_outcome_text_includes_vision_notes(stub_bge) -> None:
    encode_outcome({"session": "a", "vision_notes": ["bright", "rim light"]})
    text = stub_bge.calls[-1][0]
    assert "notes: bright | rim light" in text


def test_encode_outcome_text_includes_key_params(stub_bge) -> None:
    encode_outcome({"session": "a", "key_params": {"steps": 30, "cfg": 7}})
    text = stub_bge.calls[-1][0]
    # sort_keys=True → cfg before steps
    assert '"cfg": 7' in text
    assert '"steps": 30' in text


def test_encode_outcome_skips_falsy_fields(stub_bge) -> None:
    encode_outcome({"session": "a", "workflow_summary": None, "key_params": {}})
    text = stub_bge.calls[-1][0]
    assert "workflow:" not in text
    assert "params:" not in text


def test_encode_outcome_session_only_stub(stub_bge) -> None:
    """capsule.py's bge-mode query path passes a session-only stub."""
    v = encode_outcome({"session": "default"})
    assert len(v) == DIMENSION
    assert stub_bge.calls[-1][0] == "session: default"


def test_encode_outcome_caches_singleton(monkeypatch) -> None:
    """``_get_bge_model()`` returns the same instance across calls."""
    monkeypatch.setattr(vector_mod, "_bge_model", None)

    constructed: list[object] = []

    class _ConstructorSpy:
        def __init__(self, name, **kwargs):  # accept local_files_only (L3)
            constructed.append(name)

        def encode(self, text, **kwargs):
            return [0.0] * DIMENSION

    import sys
    import types

    fake_st = types.ModuleType("sentence_transformers")
    fake_st.SentenceTransformer = _ConstructorSpy
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_st)

    encode_outcome({"session": "a"})
    encode_outcome({"session": "b"})
    encode_outcome({"session": "c"})

    assert len(constructed) == 1, (
        f"BGE model constructed {len(constructed)}× — singleton broken"
    )
