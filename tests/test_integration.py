"""Phase 8 integration smoke test — cold-to-warm arc.

End-to-end: tail -> ingest (real Moneta) -> rotate -> ingest more ->
close all handles -> open a brand-new Moneta handle -> assert prior
deposits are retrievable -> hydrate the capsule -> validate schema_v2.

The ``real Moneta substrate`` (not mocked) and the ``brand-new handle``
re-open are non-negotiable per mission v3.1 §"Phase 8". They prove the
``run_sleep_pass()`` chain (Hard Rule §12) works through every layer:
the v3 spec bug — bare ``deposit()`` without ``run_sleep_pass()`` — would
fail step 12 below because the fresh handle would hydrate an empty ECS.

Comfy-Cozy is not invoked. The bridge is a file-only contract; we
write JSONL, exercise tail.py + ingest.py via direct calls, and then
exercise capsule.py to produce ``sessions/default.json``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from watchfiles import Change

from comfy_moneta_bridge.capsule import write_capsule
from comfy_moneta_bridge.state import CursorStore
from comfy_moneta_bridge.tail import Tailer
from comfy_moneta_bridge.vector import synthesize_vector


def _outcome(timestamp: float, **overrides) -> dict:
    base = {
        "goal_id": None,
        "key_params": {"model": "sdxl-base"},
        "model_combo": ["sdxl-base"],
        "quality_score": 0.85 + (timestamp * 1e-3),
        "render_time_s": None,
        "schema_version": 1,
        "session": "default",
        "timestamp": timestamp,
        "user_feedback": "neutral",
        "vision_notes": [f"observation_at_{timestamp:.0f}"],
        "workflow_hash": f"hash_{int(timestamp)}",
        "workflow_summary": f"step at t={timestamp}",
    }
    base.update(overrides)
    return base


def _append_lines(path: Path, lines: list[dict]) -> None:
    with open(path, "ab") as fp:
        for line in lines:
            fp.write(json.dumps(line).encode("utf-8") + b"\n")


def test_cold_to_warm_arc(tmp_path: Path) -> None:
    """End-to-end: rotation, durability, hydrate, schema_v2 conformance.

    Step numbering matches the mission's Phase 8 test shape.
    """
    # 1. Comfy-Cozy root + sessions dir.
    comfy_root = tmp_path / "comfy"
    sessions = comfy_root / "sessions"
    sessions.mkdir(parents=True)

    # 2. Real Moneta storage on a temp dir.
    moneta_storage = tmp_path / "moneta_storage"
    moneta_storage.mkdir()

    # Cursor store + tailer.
    cursor_path = tmp_path / "cursor.json"
    tailer = Tailer(sessions, CursorStore(cursor_path), moneta_storage)

    outcomes_path = sessions / "default_outcomes.jsonl"

    # 3-5. Write 5 lines to the JSONL, drive the tailer manually (the
    #      real awatch loop is exercised in dev; the test drives the
    #      handle_change path directly so it's deterministic).
    first_five = [_outcome(timestamp=float(i + 1)) for i in range(5)]
    _append_lines(outcomes_path, first_five)
    tailer._handle_change(Change.modified, outcomes_path)

    # 6. Verify 5 deposits via Moneta.query() — but NOT yet through a
    #    fresh handle. Use the same storage directory; this leans on
    #    Phase 4's run_sleep_pass call having snapshotted to disk.
    from moneta import Moneta, MonetaConfig

    def _open_fresh() -> Moneta:
        return Moneta(MonetaConfig(
            storage_uri=f"moneta-bridge://{moneta_storage.as_posix()}",
            snapshot_path=moneta_storage / "snapshot.json",
            wal_path=moneta_storage / "wal.jsonl",
            mock_target_log_path=moneta_storage / "usd_authorings.jsonl",
            embedding_dim=384,
        ))

    with _open_fresh() as m:
        results = m.query(synthesize_vector("default"), limit=100)
        assert len(results) >= 5, (
            f"expected >=5 deposits after first batch, got {len(results)}"
        )

    # 7. Trigger rotation: rename .jsonl -> .jsonl.1, create empty new file.
    rotated = outcomes_path.with_suffix(outcomes_path.suffix + ".1")
    os.rename(outcomes_path, rotated)
    outcomes_path.touch()

    # 8. Append 3 more lines to the new .jsonl.
    second_three = [_outcome(timestamp=float(10 + i)) for i in range(3)]
    _append_lines(outcomes_path, second_three)

    # 9. Drive the tailer through the rotation event sequence + the
    #    Modified event for the new file.
    tailer._handle_change(Change.deleted, outcomes_path)
    tailer._handle_change(Change.modified, outcomes_path)

    # 10. Stop tailer (we never started a real run loop; nothing to
    #     stop in this direct-method-driven test).

    # 11. Brand-new Moneta handle on the same storage. THIS is the
    #     fresh-handle proof — without run_sleep_pass() in the ingest
    #     pipeline, the handle would hydrate an empty ECS.
    with _open_fresh() as m:
        # 12. All 8 deposits retrievable via the fresh handle.
        results = m.query(synthesize_vector("default"), limit=100)
        sessions_seen = [
            json.loads(r.payload).get("session") for r in results
        ]
        default_count = sessions_seen.count("default")
        assert default_count >= 8, (
            f"expected >=8 default-session deposits via fresh handle, "
            f"got {default_count}"
        )

    # 13. Hydrate the capsule.
    capsule_path = write_capsule("default", comfy_root, moneta_storage)
    assert capsule_path == sessions / "default.json"
    assert capsule_path.exists()

    # 14. Capsule validates as schema_v2 with chronological notes.
    capsule = json.loads(capsule_path.read_text(encoding="utf-8"))
    assert capsule["schema_version"] == 2
    assert capsule["name"] == "default"
    # 8 outcomes contributed; each yields >= 1 observation note + 1
    # preference note. Lower bound: 8 preferences.
    pref_notes = [n for n in capsule["notes"] if n["type"] == "preference"]
    assert len(pref_notes) >= 8, (
        f"expected >=8 preference notes, got {len(pref_notes)}"
    )

    # Notes are chronologically ordered: extract timestamps from the
    # original observations (which we know contain "observation_at_{ts}").
    obs_notes = [n for n in capsule["notes"] if n["type"] == "observation"]
    obs_texts = [n["text"] for n in obs_notes]
    expected_obs = [
        f"observation_at_{ts:.0f}" for ts in
        [1.0, 2.0, 3.0, 4.0, 5.0, 10.0, 11.0, 12.0]
    ]
    assert obs_texts == expected_obs, (
        f"observation order wrong: {obs_texts} != {expected_obs}"
    )

    # 15. Vision-notes content from original outcomes survives the
    #     ingest -> deposit -> query -> capsule round-trip.
    for i in [1, 5, 12]:
        assert any(
            f"observation_at_{i}" in n["text"]
            for n in capsule["notes"]
        ), f"expected observation_at_{i} in capsule notes"
