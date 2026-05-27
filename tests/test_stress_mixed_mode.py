"""PASS 6 STRESS — real attacks on the bge-default system.

Safety invariants for the synthetic↔bge flip, exercised against real Moneta:
  - mixed-mode storage: a bge query returns ONLY bge deposits (F-MIGRATE)
  - a strong bge match is not starved by synthetic noise at scale
  - all-synthetic storage queried in bge returns empty, not error (F-COLDSTART)
  - schema_version drift is dropped, not mis-stored
  - cross-session injection is bounded by top_k
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("sentence_transformers")

from comfy_moneta_bridge.ingest import ingest_batch, ingest_outcome
from comfy_moneta_bridge.recall import recall
from comfy_moneta_bridge.vector import EMBEDDER_VERSION_BGE


def _oc(session: str, summary: str, ts: float) -> dict:
    return {
        "schema_version": 1,
        "session": session,
        "timestamp": ts,
        "workflow_summary": summary,
        "vision_notes": [],
        "key_params": {},
    }


def test_mixed_mode_query_returns_only_current_mode(tmp_path: Path, monkeypatch):
    """F-MIGRATE: synthetic + bge under ONE storage_uri; bge query → only bge."""
    storage = tmp_path / "moneta"

    monkeypatch.setenv("BRIDGE_EMBEDDER_MODE", "synthetic")
    ingest_outcome(_oc("s_old", "legacy memory about mountains and snow", 1.0), storage)
    ingest_outcome(_oc("s_old", "legacy memory about desert rivers", 2.0), storage)

    monkeypatch.setenv("BRIDGE_EMBEDDER_MODE", "bge")
    ingest_outcome(_oc("b_new", "a stormy ocean seascape at dusk", 3.0), storage)

    out = recall("ocean storm at dusk", storage, top_k=10)

    assert out, "bge query should surface the bge deposit"
    assert all(r["_embedder"] == EMBEDDER_VERSION_BGE for r in out), (
        f"bge query leaked non-bge deposits: {[r.get('_embedder') for r in out]}"
    )
    assert all(r["session"] != "s_old" for r in out), "synthetic deposit leaked"


def test_strong_bge_match_not_starved_by_synthetic_noise(tmp_path: Path, monkeypatch):
    """A relevant bge match survives among 100 synthetic deposits (over-fetch)."""
    storage = tmp_path / "moneta"

    monkeypatch.setenv("BRIDGE_EMBEDDER_MODE", "synthetic")
    noise = [_oc(f"s{i}", f"unrelated synthetic memory number {i}", float(i)) for i in range(100)]
    assert ingest_batch(noise, storage) == 100

    monkeypatch.setenv("BRIDGE_EMBEDDER_MODE", "bge")
    ingest_outcome(_oc("real", "a stormy ocean seascape with crashing waves", 200.0), storage)

    out = recall("ocean storm crashing waves", storage, top_k=5)
    assert any(r["session"] == "real" for r in out), (
        "strong bge match starved by synthetic noise"
    )
    assert all(r["_embedder"] == EMBEDDER_VERSION_BGE for r in out)


def test_coldstart_all_synthetic_bge_query_empty(tmp_path: Path, monkeypatch):
    """F-COLDSTART: all-synthetic store, bge query → empty (clean, not error)."""
    storage = tmp_path / "moneta"
    monkeypatch.setenv("BRIDGE_EMBEDDER_MODE", "synthetic")
    ingest_outcome(_oc("old", "a synthetic-only memory", 1.0), storage)

    monkeypatch.setenv("BRIDGE_EMBEDDER_MODE", "bge")
    out = recall("anything at all", storage, top_k=10)
    assert out == [], f"bge query over synthetic-only store should be empty, got {out}"


def test_schema_drift_dropped(tmp_path: Path, monkeypatch):
    """schema_version=2 is dropped at ingest, not mis-stored."""
    storage = tmp_path / "moneta"
    monkeypatch.setenv("BRIDGE_EMBEDDER_MODE", "bge")
    drifted = _oc("drift", "future schema outcome", 1.0)
    drifted["schema_version"] = 2
    ingest_outcome(drifted, storage)          # dropped
    ingest_outcome(_oc("ok", "a valid v1 outcome about forests", 2.0), storage)

    out = recall("forests", storage, top_k=10)
    assert all(r["session"] != "drift" for r in out), "schema-drifted outcome was stored"
    assert any(r["session"] == "ok" for r in out)


def test_cross_session_injection_bounded(tmp_path: Path, monkeypatch):
    """Cross-session notes never exceed cross_session_top_k regardless of corpus."""
    from comfy_moneta_bridge.capsule import write_capsule

    storage = tmp_path / "moneta"
    comfy = tmp_path / "comfy"
    (comfy / "sessions").mkdir(parents=True)
    monkeypatch.setenv("BRIDGE_EMBEDDER_MODE", "bge")

    # 8 other sessions, all about oceans (so all are plausible cross-session hits).
    for i in range(8):
        ingest_outcome(_oc(f"other{i}", f"ocean seascape variation {i} with waves", float(i)), storage)
    ingest_outcome(_oc("target", "stormy ocean sea at dusk", 100.0), storage)

    import json
    cap = write_capsule("target", comfy, storage, cross_session_top_k=3)
    capsule = json.loads(cap.read_text(encoding="utf-8"))
    assert capsule["metadata"]["cross_session_count"] <= 3, (
        f"cross-session injection exceeded cap: {capsule['metadata']}"
    )
