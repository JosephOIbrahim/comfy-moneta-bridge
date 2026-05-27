"""PASS 5 integration — full chain, multi-session, cross-session memory.

Composes tail -> ingest (bge, real Moneta) for two sessions under one
storage_uri, then hydrates session B's capsule and asserts a session-A
memory flows through as a cross-session note. Exercises two seams together
at the system level:
  - tail -> ingest (JSONL line -> bge embed -> durable deposit)
  - recall -> capsule (cross-session injection into schema_v2 notes)
plus the error-propagation seam (recall failure is isolated; capsule still
writes session-local).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from watchfiles import Change

pytest.importorskip("sentence_transformers")

from comfy_moneta_bridge.capsule import write_capsule
from comfy_moneta_bridge.ingest import ingest_outcome
from comfy_moneta_bridge.state import CursorStore
from comfy_moneta_bridge.tail import Tailer


def _oc(session: str, summary: str, ts: float) -> dict:
    return {
        "goal_id": None,
        "key_params": {},
        "model_combo": [],
        "quality_score": 0.8,
        "render_time_s": None,
        "schema_version": 1,
        "session": session,
        "timestamp": ts,
        "user_feedback": "neutral",
        "vision_notes": [],
        "workflow_hash": f"h{ts}",
        "workflow_summary": summary,
    }


def _append(path: Path, lines: list[dict]) -> None:
    with open(path, "ab") as fp:
        for line in lines:
            fp.write(json.dumps(line).encode("utf-8") + b"\n")


def test_full_chain_cross_session(tmp_path: Path, monkeypatch):
    """tail -> ingest (two sessions) -> capsule(B) surfaces an A memory."""
    monkeypatch.setenv("BRIDGE_EMBEDDER_MODE", "bge")
    comfy = tmp_path / "comfy"
    sessions = comfy / "sessions"
    sessions.mkdir(parents=True)
    storage = tmp_path / "moneta"
    storage.mkdir()

    tailer = Tailer(sessions, CursorStore(tmp_path / "cursor.json"), storage)
    a_file = sessions / "alpha_outcomes.jsonl"
    b_file = sessions / "beta_outcomes.jsonl"
    _append(a_file, [_oc("alpha", "a stormy ocean seascape at dusk, crashing waves", 1.0)])
    _append(b_file, [_oc("beta", "dark turbulent sea storm at twilight", 2.0)])

    # tail -> ingest seam (real Moneta, bge embed, durable deposit).
    tailer._handle_change(Change.modified, a_file)
    tailer._handle_change(Change.modified, b_file)

    # recall -> capsule seam (cross-session injection).
    cap = write_capsule("beta", comfy, storage)
    capsule = json.loads(cap.read_text(encoding="utf-8"))

    assert capsule["metadata"]["cross_session_count"] >= 1
    assert any("alpha" in n["text"] for n in capsule["notes"]), (
        f"alpha memory did not flow through the chain; notes={capsule['notes']}"
    )


def test_cross_session_failure_is_isolated(tmp_path: Path, monkeypatch):
    """A recall failure must not abort the capsule write (error-propagation seam)."""
    monkeypatch.setenv("BRIDGE_EMBEDDER_MODE", "bge")
    comfy = tmp_path / "comfy"
    (comfy / "sessions").mkdir(parents=True)
    storage = tmp_path / "moneta"

    # Two related sessions: cross-session WOULD find alpha for solo.
    ingest_outcome(_oc("alpha", "a stormy ocean seascape", 1.0), storage)
    ingest_outcome(_oc("solo", "dark sea storm at night", 2.0), storage)

    def _boom(*args, **kwargs):
        raise RuntimeError("recall backend down")

    # capsule imports recall lazily from the recall module — patch there.
    monkeypatch.setattr("comfy_moneta_bridge.recall.recall", _boom)

    cap = write_capsule("solo", comfy, storage)  # must NOT raise
    capsule = json.loads(cap.read_text(encoding="utf-8"))

    assert capsule["metadata"]["cross_session_count"] == 0
    assert capsule["name"] == "solo"
    assert capsule["schema_version"] == 2
