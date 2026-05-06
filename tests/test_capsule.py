"""Tests for comfy_moneta_bridge.capsule.

Per mission v3.1 Phase 5 test table. ``Moneta`` is mocked; the capsule
writer does no Moneta-mutating work (query is read-only) so a mock that
returns canned memories is sufficient.

CRUCIBLE bias: the filter test deliberately injects a "wrong-session"
memory the cosine-similarity layer would otherwise return; the
chronological-order test feeds timestamps out of order; the unicode
test stresses round-trip through both the Moneta payload and the
schema_v2 capsule write.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from comfy_moneta_bridge import capsule
from comfy_moneta_bridge.capsule import (
    CAPSULE_SCHEMA_VERSION,
    write_capsule,
)
from comfy_moneta_bridge.vector import (
    EMBEDDER_VERSION_BGE,
    EMBEDDER_VERSION_SYNTHETIC,
)


def _outcome(**overrides) -> dict:
    base = {
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
    base.update(overrides)
    return base


class _MemoryStub:
    """Minimal stand-in for moneta.types.Memory — only ``.payload`` used."""

    def __init__(self, payload: str) -> None:
        self.payload = payload


def _memory_from(outcome: dict) -> _MemoryStub:
    return _MemoryStub(json.dumps(outcome))


class CapsuleMonetaMock:
    instances: list["CapsuleMonetaMock"] = []
    canned: list[_MemoryStub] = []

    @classmethod
    def reset(cls) -> None:
        cls.instances = []
        cls.canned = []

    def __init__(self, config) -> None:
        self.config = config
        self.query_calls: list[tuple] = []
        CapsuleMonetaMock.instances.append(self)

    def __enter__(self) -> "CapsuleMonetaMock":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def query(self, embedding, limit):
        self.query_calls.append((list(embedding), limit))
        return list(CapsuleMonetaMock.canned[:limit])


@pytest.fixture
def patched_moneta(monkeypatch):
    CapsuleMonetaMock.reset()
    monkeypatch.setattr(
        "comfy_moneta_bridge.capsule.Moneta", CapsuleMonetaMock
    )
    return CapsuleMonetaMock


def test_writes_valid_schema_v2(tmp_path, patched_moneta) -> None:
    patched_moneta.canned = [
        _memory_from(_outcome(timestamp=1.0)),
        _memory_from(_outcome(timestamp=2.0)),
        _memory_from(_outcome(timestamp=3.0)),
    ]
    out = write_capsule("default", tmp_path, tmp_path / "moneta")
    assert out.exists()

    with open(out, encoding="utf-8") as fp:
        capsule_dict = json.load(fp)

    assert capsule_dict["schema_version"] == CAPSULE_SCHEMA_VERSION == 2
    assert capsule_dict["name"] == "default"
    assert "saved_at" in capsule_dict
    assert isinstance(capsule_dict["notes"], list)
    assert isinstance(capsule_dict["workflow"], dict)
    assert isinstance(capsule_dict["metadata"], dict)
    # Three outcomes contributed, each producing >= 1 note (vision_notes +
    # one preference). Rough lower bound: 3 preference notes.
    assert len(capsule_dict["notes"]) >= 3


def test_filters_by_session_name(tmp_path, patched_moneta) -> None:
    """PRNG-collision sim: Moneta returns mixed-session results."""
    patched_moneta.canned = [
        _memory_from(_outcome(session="default", timestamp=1.0)),
        _memory_from(_outcome(session="default", timestamp=2.0)),
        _memory_from(_outcome(session="default", timestamp=3.0)),
        _memory_from(_outcome(session="other_session", timestamp=4.0)),
        _memory_from(_outcome(session="other_session", timestamp=5.0)),
    ]
    out = write_capsule("default", tmp_path, tmp_path / "moneta")
    capsule_dict = json.loads(out.read_text(encoding="utf-8"))
    assert capsule_dict["metadata"]["memory_count"] == 3


def test_chronological_order(tmp_path, patched_moneta) -> None:
    """Notes follow outcome timestamp order, not Moneta query order."""
    patched_moneta.canned = [
        _memory_from(_outcome(
            timestamp=300.0,
            vision_notes=["third"],
            key_params={"k": 3},
            quality_score=0.3,
        )),
        _memory_from(_outcome(
            timestamp=100.0,
            vision_notes=["first"],
            key_params={"k": 1},
            quality_score=0.1,
        )),
        _memory_from(_outcome(
            timestamp=200.0,
            vision_notes=["second"],
            key_params={"k": 2},
            quality_score=0.2,
        )),
    ]
    out = write_capsule("default", tmp_path, tmp_path / "moneta")
    capsule_dict = json.loads(out.read_text(encoding="utf-8"))
    observation_texts = [
        n["text"] for n in capsule_dict["notes"] if n["type"] == "observation"
    ]
    assert observation_texts == ["first", "second", "third"]
    preference_texts = [
        n["text"] for n in capsule_dict["notes"] if n["type"] == "preference"
    ]
    # Preferences also follow chronological order.
    assert preference_texts[0].startswith('Workflow params {"k": 1}')
    assert preference_texts[-1].startswith('Workflow params {"k": 3}')


def test_atomic_replace(tmp_path, patched_moneta) -> None:
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    target = sessions / "default.json"
    target.write_text("pre-existing capsule", encoding="utf-8")

    patched_moneta.canned = [_memory_from(_outcome())]
    out = write_capsule("default", tmp_path, tmp_path / "moneta")
    assert out == target

    # Pre-existing content has been replaced with a valid JSON capsule.
    capsule_dict = json.loads(out.read_text(encoding="utf-8"))
    assert capsule_dict["schema_version"] == 2

    # Temp file should not remain.
    leftover = list(sessions.glob("*.tmp"))
    assert leftover == [], f"temp file leaked: {leftover}"


def test_unicode_in_payloads(tmp_path, patched_moneta) -> None:
    patched_moneta.canned = [
        _memory_from(_outcome(
            vision_notes=["✨ great work 🎨", "“smart quotes”"],
            key_params={"prompt": "séssîön_测试"},
        )),
    ]
    out = write_capsule("default", tmp_path, tmp_path / "moneta")
    raw = out.read_text(encoding="utf-8")
    # File contains the literal unicode, not escaped.
    assert "✨" in raw
    assert "🎨" in raw
    assert "测试" in raw
    # Round-trips through json.load.
    capsule_dict = json.loads(raw)
    texts = [n["text"] for n in capsule_dict["notes"]]
    assert "✨ great work 🎨" in texts


def test_empty_session_writes_empty_notes(tmp_path, patched_moneta) -> None:
    patched_moneta.canned = []
    out = write_capsule("nonexistent", tmp_path, tmp_path / "moneta")
    capsule_dict = json.loads(out.read_text(encoding="utf-8"))
    assert capsule_dict["schema_version"] == 2
    assert capsule_dict["notes"] == []
    assert capsule_dict["metadata"]["memory_count"] == 0


def test_skips_mixed_embedder_versions(tmp_path, patched_moneta, caplog) -> None:
    """Capsule writer drops deposits whose ``_embedder`` tag does not
    match the current mode and logs how many were skipped.

    Default test mode is synthetic, so synthetic-v0 deposits are kept
    and bge-tagged deposits are dropped. Untagged deposits (pre-Day-2)
    are treated as synthetic-v0 — kept under default mode.
    """
    patched_moneta.canned = [
        _memory_from({**_outcome(timestamp=1.0),
                      "_embedder": EMBEDDER_VERSION_SYNTHETIC}),
        _memory_from({**_outcome(timestamp=2.0),
                      "_embedder": EMBEDDER_VERSION_BGE}),
        _memory_from({**_outcome(timestamp=3.0),
                      "_embedder": EMBEDDER_VERSION_SYNTHETIC}),
        _memory_from({**_outcome(timestamp=4.0),
                      "_embedder": EMBEDDER_VERSION_BGE}),
        # Untagged legacy deposit — implicit synthetic-v0.
        _memory_from(_outcome(timestamp=5.0)),
    ]
    caplog.set_level(logging.INFO, logger="comfy_moneta_bridge.capsule")
    out = write_capsule("default", tmp_path, tmp_path / "moneta")
    capsule_dict = json.loads(out.read_text(encoding="utf-8"))

    # 3 synthetic-tagged (incl. legacy untagged) survive; 2 bge dropped.
    assert capsule_dict["metadata"]["memory_count"] == 3
    # Skip count surfaces in INFO logs so an operator notices mixed state.
    assert any(
        "skipped 2" in r.message and "embedder" in r.message
        for r in caplog.records
    ), f"expected 'skipped 2 ... embedder' in logs; got {[r.message for r in caplog.records]}"


def test_query_limit_warning(tmp_path, patched_moneta, caplog) -> None:
    # Stuff exactly query_limit memories into the mock so the trim
    # condition triggers.
    limit = 5
    patched_moneta.canned = [
        _memory_from(_outcome(timestamp=float(i))) for i in range(limit)
    ]
    caplog.set_level(logging.WARNING, logger="comfy_moneta_bridge.capsule")
    write_capsule(
        "default", tmp_path, tmp_path / "moneta", query_limit=limit
    )
    assert any(
        "query_limit" in r.message and "truncated" in r.message
        for r in caplog.records
    )


def test_schema_v2_workflow_stub(tmp_path, patched_moneta) -> None:
    patched_moneta.canned = [_memory_from(_outcome())]
    out = write_capsule("default", tmp_path, tmp_path / "moneta")
    capsule_dict = json.loads(out.read_text(encoding="utf-8"))
    workflow = capsule_dict["workflow"]
    assert workflow == {
        "loaded_path": None,
        "format": "api",
        "base_workflow": None,
        "current_workflow": None,
        "history_depth": 0,
    }
