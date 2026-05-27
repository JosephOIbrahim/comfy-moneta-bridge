"""P5 regression — synthetic-mode storage stays queryable in synthetic mode.

Operator decision (PASS 2 fork F-MIGRATE): P5 is *mode-matched* — a
synthetic-v0 ``storage_uri`` remains queryable when queried in synthetic
mode, regardless of the package default. This test pins that guarantee
explicitly so it survives the bge default flip (leaf L4): it forces
``BRIDGE_EMBEDDER_MODE=synthetic`` and exercises a real Moneta
ingest -> recall round-trip, asserting the deposits come back tagged
``synthetic-v0``.

Real (not mocked) Moneta substrate, mirroring test_integration.py's
fresh-handle durability discipline.
"""

from __future__ import annotations

from pathlib import Path

from comfy_moneta_bridge.ingest import ingest_outcome
from comfy_moneta_bridge.recall import recall
from comfy_moneta_bridge.vector import EMBEDDER_VERSION_SYNTHETIC


def _outcome(session: str, summary: str, ts: float) -> dict:
    return {
        "schema_version": 1,
        "session": session,
        "timestamp": ts,
        "workflow_summary": summary,
        "vision_notes": [],
        "key_params": {},
    }


def test_synthetic_roundtrip_is_mode_matched(tmp_path: Path, monkeypatch):
    """Synthetic deposits remain queryable in synthetic mode (P5)."""
    monkeypatch.setenv("BRIDGE_EMBEDDER_MODE", "synthetic")
    storage = tmp_path / "moneta"

    ingest_outcome(_outcome("legacy_sess", "a stormy seascape at dusk", 1.0), storage)
    ingest_outcome(_outcome("legacy_sess", "a sunny bright meadow", 2.0), storage)

    # Synthetic recall is keyed off the query string; querying the session
    # name reproduces the deposit vectors (synthesize_vector(session)).
    out = recall("legacy_sess", storage, top_k=10)

    assert len(out) >= 2, f"expected >=2 synthetic deposits, got {len(out)}"
    assert all(
        r["_embedder"] == EMBEDDER_VERSION_SYNTHETIC for r in out
    ), "synthetic round-trip must preserve the synthetic-v0 embedder tag"


def test_synthetic_deposits_survive_default_flip(tmp_path: Path, monkeypatch):
    """Deposit in synthetic mode, then query in synthetic mode after a
    simulated default flip: the data is still there and still tagged."""
    # Deposit under explicit synthetic mode.
    monkeypatch.setenv("BRIDGE_EMBEDDER_MODE", "synthetic")
    storage = tmp_path / "moneta"
    ingest_outcome(_outcome("pre_flip", "vintage film grain portrait", 1.0), storage)

    # Simulate post-flip world: default would be bge, but the operator
    # queries the legacy URI in synthetic mode. Mode-matched -> readable.
    monkeypatch.setenv("BRIDGE_EMBEDDER_MODE", "synthetic")
    out = recall("pre_flip", storage, top_k=10)
    assert len(out) >= 1
    assert out[0]["_embedder"] == EMBEDDER_VERSION_SYNTHETIC
    assert out[0]["session"] == "pre_flip"
