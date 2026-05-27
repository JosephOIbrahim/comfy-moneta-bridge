"""P7 / leaf L5 — cross-session semantic memory reaches the capsule.

End-to-end with real Moneta in bge mode: deposit a memory under session A
and a semantically-related memory under session B (same storage_uri), then
hydrate B's capsule and assert a session-A memory is folded in as a note.
Also asserts the augmented capsule stays within schema_v2 (no new note
field, valid note type), so Comfy-Cozy's loader needs no change — the
NO-TOUCH boundary holds.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("sentence_transformers")

from comfy_moneta_bridge.capsule import write_capsule
from comfy_moneta_bridge.ingest import ingest_outcome

_VALID_NOTE_TYPES = {"observation", "preference", "decision", "tip"}
_NOTE_KEYS = {"text", "type", "added_at"}


def _outcome(session: str, summary: str, ts: float, notes=None) -> dict:
    return {
        "schema_version": 1,
        "session": session,
        "timestamp": ts,
        "workflow_summary": summary,
        "vision_notes": notes or [],
        "key_params": {},
    }


def test_cross_session_memory_reaches_capsule(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("BRIDGE_EMBEDDER_MODE", "bge")
    storage = tmp_path / "moneta"
    comfy_root = tmp_path / "comfy"
    (comfy_root / "sessions").mkdir(parents=True)

    # Session A: a stormy-ocean memory.
    ingest_outcome(
        _outcome(
            "alpha",
            "a stormy ocean seascape at dusk with crashing waves",
            1.0,
            ["high contrast", "dramatic sky"],
        ),
        storage,
    )
    # Session B: semantically related, so A is relevant to B's hydration.
    ingest_outcome(
        _outcome("beta", "dark turbulent sea storm at twilight", 2.0), storage
    )

    out_path = write_capsule("beta", comfy_root, storage)
    capsule = json.loads(out_path.read_text(encoding="utf-8"))

    # The Outcome: beta's capsule surfaces a memory originating from alpha.
    cross = [n for n in capsule["notes"] if "alpha" in n["text"]]
    assert cross, (
        f"expected a session-alpha memory in beta's capsule; "
        f"notes={capsule['notes']}"
    )
    assert capsule["metadata"]["cross_session_count"] >= 1

    # NO-TOUCH: every note conforms to schema_v2 (no new field, valid type),
    # so Comfy-Cozy's session.py loads it unchanged.
    for n in capsule["notes"]:
        assert set(n.keys()) == _NOTE_KEYS, f"note has non-schema keys: {n}"
        assert n["type"] in _VALID_NOTE_TYPES, f"invalid note type: {n['type']}"


def test_cross_session_excludes_own_session(tmp_path: Path, monkeypatch):
    """A session's own memories are not folded in as cross-session notes."""
    monkeypatch.setenv("BRIDGE_EMBEDDER_MODE", "bge")
    storage = tmp_path / "moneta"
    comfy_root = tmp_path / "comfy"
    (comfy_root / "sessions").mkdir(parents=True)

    ingest_outcome(
        _outcome("beta", "sunny meadow with wildflowers", 1.0), storage
    )
    out_path = write_capsule("beta", comfy_root, storage)
    capsule = json.loads(out_path.read_text(encoding="utf-8"))

    assert not any(
        "session 'beta'" in n["text"] for n in capsule["notes"]
    ), "own session must not appear as a cross-session note"
    assert capsule["metadata"]["cross_session_count"] == 0


def test_cross_session_disabled_by_zero_top_k(tmp_path: Path, monkeypatch):
    """cross_session_top_k=0 reverts to pure session-local hydration."""
    monkeypatch.setenv("BRIDGE_EMBEDDER_MODE", "bge")
    storage = tmp_path / "moneta"
    comfy_root = tmp_path / "comfy"
    (comfy_root / "sessions").mkdir(parents=True)

    ingest_outcome(_outcome("alpha", "stormy ocean seascape", 1.0), storage)
    ingest_outcome(_outcome("beta", "dark sea storm", 2.0), storage)

    out_path = write_capsule("beta", comfy_root, storage, cross_session_top_k=0)
    capsule = json.loads(out_path.read_text(encoding="utf-8"))
    assert capsule["metadata"]["cross_session_count"] == 0
    assert not any("alpha" in n["text"] for n in capsule["notes"])
