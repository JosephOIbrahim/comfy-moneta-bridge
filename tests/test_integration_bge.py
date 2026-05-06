"""BGE-mode integration test — Path B / cross-session semantic recall.

End-to-end: tail -> ingest (real BGE encoder, real Moneta) -> close all
handles -> recall() with a semantically-near query -> assert the
near-match ranks above the far-match.

Sibling to test_integration.py because the BGE path requires
``sentence_transformers``; module-level ``importorskip`` keeps the
suite green when the ``embeddings`` extra is absent (mirrors
test_vector_bge.py:19). The synthetic-mode integration test stays as
the no-extras baseline.

Hard Rule §12: ingest runs ``run_sleep_pass()`` per cycle (asserted in
test_ingest.py); recall is read-only and must NOT mutate state.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("sentence_transformers")

from watchfiles import Change

from comfy_moneta_bridge import recall as recall_mod
from comfy_moneta_bridge.state import CursorStore
from comfy_moneta_bridge.tail import Tailer
from comfy_moneta_bridge.vector import ENV_VAR


def _outcome(session: str, summary: str, timestamp: float) -> dict:
    return {
        "goal_id": None,
        "key_params": {"model": "sdxl-base"},
        "model_combo": ["sdxl-base"],
        "quality_score": 0.85,
        "render_time_s": None,
        "schema_version": 1,
        "session": session,
        "timestamp": timestamp,
        "user_feedback": "neutral",
        "vision_notes": [],
        "workflow_hash": f"hash_{int(timestamp)}",
        "workflow_summary": summary,
    }


def _append_lines(path: Path, lines: list[dict]) -> None:
    with open(path, "ab") as fp:
        for line in lines:
            fp.write(json.dumps(line).encode("utf-8") + b"\n")


def test_bge_round_trip_semantic_recall(tmp_path: Path, monkeypatch) -> None:
    """Two semantically distant outcomes deposited under BGE; recall with
    text near one returns it first.

    Without this test the Path B demo claim ("real semantic recall
    across sessions") is unverified. A passing recall_mod test with
    mocked Moneta proves the filter logic, not the ranking.
    """
    monkeypatch.setenv(ENV_VAR, "bge")

    comfy_root = tmp_path / "comfy"
    sessions = comfy_root / "sessions"
    sessions.mkdir(parents=True)
    moneta_storage = tmp_path / "moneta_storage"
    moneta_storage.mkdir()
    cursor_path = tmp_path / "cursor.json"

    tailer = Tailer(sessions, CursorStore(cursor_path), moneta_storage)
    outcomes_path = sessions / "default_outcomes.jsonl"

    # Two semantically distant outcomes in two different sessions.
    # Cross-session recall is the Path B demo claim, so the assertion
    # is that recall surfaces the right session by content, not by
    # session-string match.
    seascape = _outcome(
        session="alpha",
        summary="stormy seascape at dusk with crashing waves",
        timestamp=1.0,
    )
    meadow = _outcome(
        session="beta",
        summary="happy bright sunny meadow with butterflies",
        timestamp=2.0,
    )
    _append_lines(outcomes_path, [seascape, meadow])
    tailer._handle_change(Change.modified, outcomes_path)

    # Snapshot durable on-disk state before recall. Hard Rule §12 says
    # query is read-only, so the persistence artifact must not change
    # across recall(). After ingest's run_sleep_pass(), Moneta has
    # consolidated the WAL into snapshot.json (the WAL file no longer
    # exists), so snapshot.json is the right invariant to check.
    snapshot_path = moneta_storage / "snapshot.json"
    snapshot_size_before = snapshot_path.stat().st_size

    # Recall with text close to the seascape, far from the meadow.
    results = recall_mod.recall(
        "dark ocean storm at twilight",
        moneta_storage,
        top_k=10,
    )

    snapshot_size_after = snapshot_path.stat().st_size
    assert snapshot_size_after == snapshot_size_before, (
        "recall mutated snapshot.json — Hard Rule §12 says query is read-only"
    )

    # Both deposits land in the result set (top_k > deposit count).
    sessions_seen = [r["session"] for r in results]
    assert "alpha" in sessions_seen, (
        f"alpha (seascape) missing from recall results: {sessions_seen}"
    )
    assert "beta" in sessions_seen, (
        f"beta (meadow) missing from recall results: {sessions_seen}"
    )

    # Semantic ranking: seascape ranks above meadow for an ocean-storm
    # query. This is the property test_recall.py cannot assert because
    # it mocks Moneta.
    alpha_rank = sessions_seen.index("alpha")
    beta_rank = sessions_seen.index("beta")
    assert alpha_rank < beta_rank, (
        f"BGE ranking inverted: alpha at {alpha_rank}, beta at {beta_rank}; "
        f"semantic recall is not behaving"
    )

    # Embedder version tag round-trips: deposits were tagged bge-small,
    # recall's version filter kept them.
    for r in results:
        assert r["_embedder"] == "bge-small-en-v1.5", (
            f"unexpected embedder tag in recall result: {r['_embedder']}"
        )
