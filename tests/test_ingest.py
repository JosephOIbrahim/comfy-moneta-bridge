"""Tests for comfy_moneta_bridge.ingest.

Two categories per mission v3.1 Phase 4 table:

  - Mocked-Moneta tests verify the call shape: deposit signature,
    run_sleep_pass invoked, ordering, schema enforcement, None
    preservation, no dedupe.
  - One real-Moneta test (``test_deposit_persists_after_handle_close``)
    proves the Hard Rule §12 invariant end-to-end. **This test would
    have caught the v3 spec bug.** It is mandatory.

The mock is a context-manager class with an ``events`` log so order
assertions are straightforward.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from comfy_moneta_bridge import ingest
from comfy_moneta_bridge import vector as vector_mod
from comfy_moneta_bridge.vector import (
    DIMENSION,
    EMBEDDER_VERSION_BGE,
    EMBEDDER_VERSION_SYNTHETIC,
    ENV_VAR,
    synthesize_vector,
)


VALID_OUTCOME = {
    "goal_id": None,
    "key_params": {"model": "sdxl-base"},
    "model_combo": ["sdxl-base"],
    "quality_score": 0.9,
    "render_time_s": None,
    "schema_version": 1,
    "session": "default",
    "timestamp": 1775585087.95,
    "user_feedback": "neutral",
    "vision_notes": ["verify_decision: accept"],
    "workflow_hash": "e387419df4d2bf39",
    "workflow_summary": "MoE accepted on sdxl-base",
}


def _outcome(**overrides) -> dict:
    return {**VALID_OUTCOME, **overrides}


# ----------------------------------------------------------------------
# Moneta mock
# ----------------------------------------------------------------------


class MonetaMock:
    """Context-manager mock with ordered event log."""

    instances: list["MonetaMock"] = []

    @classmethod
    def reset(cls) -> None:
        cls.instances = []

    def __init__(self, config) -> None:
        self.config = config
        self.events: list[tuple] = []
        self.deposit_calls: list[tuple[str, list[float]]] = []
        self.sleep_pass_count = 0
        MonetaMock.instances.append(self)

    def __enter__(self) -> "MonetaMock":
        self.events.append(("enter",))
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.events.append(("exit",))
        return False

    def deposit(self, payload, embedding, **kwargs) -> str:
        self.events.append(("deposit", payload, list(embedding)))
        self.deposit_calls.append((payload, list(embedding)))
        return "fake-uuid"

    def run_sleep_pass(self):
        self.events.append(("sleep_pass",))
        self.sleep_pass_count += 1
        return None


@pytest.fixture
def patched_moneta(monkeypatch):
    MonetaMock.reset()
    monkeypatch.setattr("comfy_moneta_bridge.ingest.Moneta", MonetaMock)
    return MonetaMock


# ----------------------------------------------------------------------
# Mocked-Moneta tests
# ----------------------------------------------------------------------


def test_full_record_passes_through(tmp_path, patched_moneta) -> None:
    ingest.ingest_outcome(_outcome(), tmp_path / "moneta")
    assert len(patched_moneta.instances) == 1
    inst = patched_moneta.instances[0]
    assert len(inst.deposit_calls) == 1


def test_run_sleep_pass_called(tmp_path, patched_moneta) -> None:
    ingest.ingest_outcome(_outcome(), tmp_path / "moneta")
    inst = patched_moneta.instances[0]
    assert inst.sleep_pass_count == 1


def test_run_sleep_pass_called_after_deposit(tmp_path, patched_moneta) -> None:
    ingest.ingest_outcome(_outcome(), tmp_path / "moneta")
    inst = patched_moneta.instances[0]
    event_names = [e[0] for e in inst.events]
    assert event_names == ["enter", "deposit", "sleep_pass", "exit"]


def test_payload_is_full_json(tmp_path, patched_moneta) -> None:
    """Stored payload contains every original outcome field, plus the
    embedder-version tag (default mode → synthetic-v0)."""
    outcome = _outcome()
    ingest.ingest_outcome(outcome, tmp_path / "moneta")
    payload, _embedding = patched_moneta.instances[0].deposit_calls[0]
    parsed = json.loads(payload)
    # Every original outcome field round-trips verbatim.
    for k, v in outcome.items():
        assert parsed[k] == v, f"field {k!r} did not round-trip"
    # Plus exactly one bridge-side decoration: the embedder version.
    assert parsed["_embedder"] == EMBEDDER_VERSION_SYNTHETIC
    assert set(parsed.keys()) - set(outcome.keys()) == {"_embedder"}


def test_session_drives_embedding(tmp_path, patched_moneta) -> None:
    ingest.ingest_outcome(_outcome(session="alpha"), tmp_path / "moneta")
    ingest.ingest_outcome(_outcome(session="beta"), tmp_path / "moneta")
    ingest.ingest_outcome(_outcome(session="alpha"), tmp_path / "moneta")

    instances = patched_moneta.instances
    assert len(instances) == 3
    e_alpha_1 = instances[0].deposit_calls[0][1]
    e_beta = instances[1].deposit_calls[0][1]
    e_alpha_2 = instances[2].deposit_calls[0][1]

    assert e_alpha_1 == e_alpha_2
    assert e_alpha_1 != e_beta
    # Match the deterministic embedder.
    assert e_alpha_1 == synthesize_vector("alpha")
    assert len(e_alpha_1) == DIMENSION


def test_optional_quality_score_preserves_none(tmp_path, patched_moneta) -> None:
    ingest.ingest_outcome(_outcome(quality_score=None), tmp_path / "moneta")
    payload, _ = patched_moneta.instances[0].deposit_calls[0]
    parsed = json.loads(payload)
    assert parsed["quality_score"] is None


def test_optional_render_time_preserves_none(tmp_path, patched_moneta) -> None:
    ingest.ingest_outcome(_outcome(render_time_s=None), tmp_path / "moneta")
    payload, _ = patched_moneta.instances[0].deposit_calls[0]
    parsed = json.loads(payload)
    assert parsed["render_time_s"] is None


def test_zero_quality_score_preserved_distinctly(tmp_path, patched_moneta) -> None:
    ingest.ingest_outcome(_outcome(quality_score=0.0), tmp_path / "moneta")
    payload, _ = patched_moneta.instances[0].deposit_calls[0]
    parsed = json.loads(payload)
    assert parsed["quality_score"] == 0.0
    assert parsed["quality_score"] is not None


def test_wrong_schema_version_dropped(tmp_path, patched_moneta, caplog) -> None:
    import logging
    caplog.set_level(logging.WARNING, logger="comfy_moneta_bridge.ingest")
    ingest.ingest_outcome(_outcome(schema_version=2), tmp_path / "moneta")
    # No Moneta instance constructed at all.
    assert patched_moneta.instances == []
    assert any("schema_version" in r.message for r in caplog.records)


def test_embedder_version_flips_with_env(
    tmp_path, patched_moneta, monkeypatch
) -> None:
    """Two deposits under different BRIDGE_EMBEDDER_MODE values land
    with distinct ``_embedder`` tags in their payloads. This is the
    storage-side guard against silent mixing of vector spaces."""

    class _StubBGE:
        """Avoid loading the real model — the test asserts on tags only."""
        def encode(self, text, **kwargs):
            return [0.0] * DIMENSION

    monkeypatch.setattr(vector_mod, "_bge_model", _StubBGE())

    monkeypatch.delenv(ENV_VAR, raising=False)
    ingest.ingest_outcome(_outcome(session="alpha"), tmp_path / "moneta")

    monkeypatch.setenv(ENV_VAR, "bge")
    ingest.ingest_outcome(_outcome(session="alpha"), tmp_path / "moneta")

    assert len(patched_moneta.instances) == 2
    payload_synth, _ = patched_moneta.instances[0].deposit_calls[0]
    payload_bge, _ = patched_moneta.instances[1].deposit_calls[0]
    parsed_synth = json.loads(payload_synth)
    parsed_bge = json.loads(payload_bge)
    assert parsed_synth["_embedder"] == EMBEDDER_VERSION_SYNTHETIC
    assert parsed_bge["_embedder"] == EMBEDDER_VERSION_BGE
    assert parsed_synth["_embedder"] != parsed_bge["_embedder"]


def test_no_dedup_logic(tmp_path, patched_moneta) -> None:
    """Two byte-identical outcomes yield two deposits — Moneta owns idempotency."""
    o = _outcome()
    ingest.ingest_outcome(o, tmp_path / "moneta")
    ingest.ingest_outcome(o, tmp_path / "moneta")
    assert len(patched_moneta.instances) == 2
    assert all(len(i.deposit_calls) == 1 for i in patched_moneta.instances)


def test_handle_closed_after_deposit(tmp_path, patched_moneta) -> None:
    ingest.ingest_outcome(_outcome(), tmp_path / "moneta")
    inst = patched_moneta.instances[0]
    # __exit__ must be the last event.
    assert inst.events[-1] == ("exit",)


# ----------------------------------------------------------------------
# Real-Moneta durability test (no mock)
# ----------------------------------------------------------------------


def test_deposit_persists_after_handle_close(tmp_path) -> None:
    """Mandatory: deposit must survive close+reopen of the Moneta handle.

    This test would have caught the v3 spec bug. Without
    ``run_sleep_pass()`` inside the with-block, the deposit lives only
    in the in-memory ECS and the second handle would see ``ecs.n == 0``.
    """
    from moneta import Moneta

    from comfy_moneta_bridge.moneta_config import build_config

    storage = tmp_path / "moneta"
    ingest.ingest_outcome(_outcome(session="durable_session"), storage)

    # Second handle, same storage. If run_sleep_pass() didn't run, this
    # would hydrate an empty ECS.
    config2 = build_config(storage)
    with Moneta(config2) as m:
        assert m.ecs.n >= 1
        # Query with the same synthetic vector to confirm we can find it.
        results = m.query(
            embedding=synthesize_vector("durable_session"), limit=5
        )
        assert len(results) >= 1
        # Payload should round-trip back to a parseable dict.
        payloads = [json.loads(r.payload) for r in results]
        sessions = [p.get("session") for p in payloads]
        assert "durable_session" in sessions
