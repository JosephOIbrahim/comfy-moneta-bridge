"""Tests for comfy_moneta_bridge.tail — rotation-aware JSONL tailer.

Per mission v3.1 Phase 3 test table. Drives the tailer's internal methods
directly (``_handle_change``, ``_drain_complete_lines``, ``_handle_rotation``)
rather than the awatch loop, which is exercised by Phase 8's integration
test. ``ingest.ingest_outcome`` is monkeypatched with a list-collector
(raising=False because Phase 4 has not yet defined the real function on
the module).

CRUCIBLE bias: the partial-line / unicode / malformed-line / rotation
tests probe the boundaries where the v3 spec previously had silent bugs.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from watchfiles import Change

from comfy_moneta_bridge.state import CursorStore, WatchState
from comfy_moneta_bridge.tail import Tailer


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


def _write_lines(path: Path, lines: list[dict], append: bool = False) -> None:
    mode = "ab" if append else "wb"
    with open(path, mode) as fp:
        for line in lines:
            fp.write(json.dumps(line).encode("utf-8") + b"\n")


@pytest.fixture
def collected(monkeypatch):
    bucket: list[dict] = []

    def fake_ingest_batch(outcomes, moneta_storage_path):
        bucket.extend(outcomes)
        return len(outcomes)

    monkeypatch.setattr(
        "comfy_moneta_bridge.ingest.ingest_batch",
        fake_ingest_batch,
        raising=False,
    )
    return bucket


def _make_tailer(tmp_path: Path) -> Tailer:
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    cursor = CursorStore(tmp_path / "cursor.json")
    return Tailer(sessions, cursor, tmp_path / "moneta_storage")


def test_appends_one_line(tmp_path, collected) -> None:
    tailer = _make_tailer(tmp_path)
    path = tmp_path / "sessions" / "default_outcomes.jsonl"
    _write_lines(path, [_outcome()])

    tailer._handle_change(Change.modified, path)

    assert len(collected) == 1
    assert collected[0]["session"] == "default"
    state = tailer._cursor_store.get(str(path))
    assert state is not None
    assert state.offset == path.stat().st_size


def test_partial_line_then_completion(tmp_path, collected) -> None:
    tailer = _make_tailer(tmp_path)
    path = tmp_path / "sessions" / "default_outcomes.jsonl"

    # Write a half line (no trailing newline).
    full_blob = json.dumps(_outcome()).encode("utf-8") + b"\n"
    half = full_blob[: len(full_blob) // 2]
    with open(path, "wb") as fp:
        fp.write(half)

    tailer._handle_change(Change.modified, path)
    assert collected == []
    state_after_partial = tailer._cursor_store.get(str(path))
    # Cursor either absent or at offset 0 — we never advanced past a
    # complete line.
    assert state_after_partial is None or state_after_partial.offset == 0

    # Complete the line.
    with open(path, "ab") as fp:
        fp.write(full_blob[len(full_blob) // 2 :])

    tailer._handle_change(Change.modified, path)
    assert len(collected) == 1
    state = tailer._cursor_store.get(str(path))
    assert state.offset == path.stat().st_size


def test_rotation_drains_jsonl_1(tmp_path, collected) -> None:
    tailer = _make_tailer(tmp_path)
    path = tmp_path / "sessions" / "default_outcomes.jsonl"

    first_five = [_outcome(timestamp=1.0 + i) for i in range(5)]
    _write_lines(path, first_five)
    tailer._handle_change(Change.modified, path)
    assert len(collected) == 5

    # Rotation: rename to .1, write 5 new lines to a fresh file.
    rotated = path.with_suffix(path.suffix + ".1")
    os.rename(path, rotated)
    second_five = [_outcome(timestamp=10.0 + i) for i in range(5)]
    _write_lines(path, second_five)

    # Synthesize the rotation event sequence (Comfy-Cozy issues a Deleted
    # for the original, then a Modified on the new file).
    tailer._handle_change(Change.deleted, path)
    tailer._handle_change(Change.modified, path)

    assert len(collected) == 10
    assert [c["timestamp"] for c in collected] == [
        1.0, 2.0, 3.0, 4.0, 5.0, 10.0, 11.0, 12.0, 13.0, 14.0,
    ]


def test_rotation_with_pre_rotation_unread_bytes(tmp_path, collected) -> None:
    """Lines written between cursor advance and rename are recovered from .jsonl.1.

    Sets up: tailer reads 3 lines, advancing the cursor to end-of-line-3.
    Two more lines are appended to the original file (cursor doesn't see
    them yet). Rotation happens. Tailer must drain those 2 stranded lines
    from .jsonl.1 plus 3 fresh lines from the new file.
    """
    tailer = _make_tailer(tmp_path)
    path = tmp_path / "sessions" / "default_outcomes.jsonl"

    # First 3 lines, tailer ingests them.
    _write_lines(path, [_outcome(timestamp=float(i)) for i in range(3)])
    tailer._handle_change(Change.modified, path)
    assert len(collected) == 3

    # Append 2 more lines to the same file (cursor advance hasn't seen them).
    _write_lines(
        path,
        [_outcome(timestamp=10.0 + i) for i in range(2)],
        append=True,
    )

    # Rotation happens BEFORE tailer next reads the file.
    rotated = path.with_suffix(path.suffix + ".1")
    os.rename(path, rotated)

    # New file with 3 lines.
    _write_lines(path, [_outcome(timestamp=100.0 + i) for i in range(3)])

    tailer._handle_change(Change.deleted, path)
    tailer._handle_change(Change.modified, path)

    timestamps = [c["timestamp"] for c in collected]
    assert timestamps == [
        0.0, 1.0, 2.0,         # pre-rotation, ingested via first event
        10.0, 11.0,             # stranded in .jsonl.1, drained on rotation
        100.0, 101.0, 102.0,    # post-rotation new file
    ]


def test_unicode_emoji_in_vision_notes(tmp_path, collected) -> None:
    tailer = _make_tailer(tmp_path)
    path = tmp_path / "sessions" / "default_outcomes.jsonl"
    line = _outcome(vision_notes=["✨ great work 🎨", "“smart quotes”"])
    _write_lines(path, [line])

    tailer._handle_change(Change.modified, path)
    assert len(collected) == 1
    assert collected[0]["vision_notes"] == [
        "✨ great work 🎨",
        "“smart quotes”",
    ]


def test_state_persists_across_restart(tmp_path, collected) -> None:
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    cursor_path = tmp_path / "cursor.json"
    moneta_storage = tmp_path / "moneta_storage"

    path = sessions / "default_outcomes.jsonl"
    _write_lines(path, [_outcome(timestamp=float(i)) for i in range(3)])

    tailer_a = Tailer(sessions, CursorStore(cursor_path), moneta_storage)
    tailer_a._handle_change(Change.modified, path)
    assert len(collected) == 3

    # Simulate restart: brand-new Tailer instance, same cursor_path.
    _write_lines(
        path,
        [_outcome(timestamp=10.0 + i) for i in range(2)],
        append=True,
    )
    tailer_b = Tailer(sessions, CursorStore(cursor_path), moneta_storage)
    tailer_b._handle_change(Change.modified, path)

    # Total ingested: 3 from first run + 2 from second. NOT re-ingested.
    assert len(collected) == 5
    assert [c["timestamp"] for c in collected] == [
        0.0, 1.0, 2.0, 10.0, 11.0,
    ]


def test_malformed_line_skipped(tmp_path, collected) -> None:
    tailer = _make_tailer(tmp_path)
    path = tmp_path / "sessions" / "default_outcomes.jsonl"

    valid = json.dumps(_outcome(timestamp=1.0)).encode("utf-8") + b"\n"
    malformed = b"this is not json {{{\n"
    valid2 = json.dumps(_outcome(timestamp=2.0)).encode("utf-8") + b"\n"
    with open(path, "wb") as fp:
        fp.write(valid + malformed + valid2)

    tailer._handle_change(Change.modified, path)

    timestamps = [c["timestamp"] for c in collected]
    assert timestamps == [1.0, 2.0]
    # Cursor advanced past the malformed line so it isn't replayed.
    state = tailer._cursor_store.get(str(path))
    assert state.offset == path.stat().st_size


# ─── Opt-in cross-event buffering (L1) ────────────────────────────────


def _buffered_tailer(tmp_path: Path, batch_size=1, batch_max_delay_s=0.0):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    cursor = CursorStore(tmp_path / "cursor.json")
    return Tailer(
        sessions, cursor, tmp_path / "moneta_storage",
        batch_size=batch_size, batch_max_delay_s=batch_max_delay_s,
    )


def test_batching_disabled_by_default(tmp_path) -> None:
    tailer = _buffered_tailer(tmp_path)
    assert tailer._batching_enabled is False


def test_buffered_count_trigger_holds_then_flushes(tmp_path, collected) -> None:
    tailer = _buffered_tailer(tmp_path, batch_size=3)
    path = tmp_path / "sessions" / "default_outcomes.jsonl"

    # Two lines in one event: buffered, NOT flushed (2 < 3), and the
    # persisted cursor must NOT advance yet.
    _write_lines(path, [_outcome(timestamp=1.0), _outcome(timestamp=2.0)])
    tailer._handle_change(Change.modified, path)
    assert collected == []
    assert tailer._cursor_store.get(str(path)) is None

    # A third line crosses the threshold → flush deposits all three and
    # the cursor catches up to the durable offset.
    _write_lines(path, [_outcome(timestamp=3.0)], append=True)
    tailer._handle_change(Change.modified, path)
    assert [c["timestamp"] for c in collected] == [1.0, 2.0, 3.0]
    state = tailer._cursor_store.get(str(path))
    assert state is not None
    assert state.offset == path.stat().st_size


def test_buffered_does_not_reread_unflushed_lines(tmp_path, collected) -> None:
    """The in-memory live offset advances each event so buffered lines
    are not re-drained before they flush."""
    tailer = _buffered_tailer(tmp_path, batch_size=10)
    path = tmp_path / "sessions" / "default_outcomes.jsonl"

    _write_lines(path, [_outcome(timestamp=1.0)])
    tailer._handle_change(Change.modified, path)
    _write_lines(path, [_outcome(timestamp=2.0)], append=True)
    tailer._handle_change(Change.modified, path)

    # Nothing flushed yet (2 < 10), but the buffer holds exactly two
    # distinct lines — not four from re-reading.
    assert collected == []
    assert len(tailer._buffer) == 2
    assert [o["timestamp"] for o in tailer._buffer] == [1.0, 2.0]


def test_buffered_time_trigger(tmp_path, collected) -> None:
    tailer = _buffered_tailer(tmp_path, batch_size=100, batch_max_delay_s=10.0)
    path = tmp_path / "sessions" / "default_outcomes.jsonl"
    _write_lines(path, [_outcome(timestamp=1.0)])
    tailer._handle_change(Change.modified, path)
    # Count threshold (100) not met; buffer holds the line.
    assert collected == []
    assert tailer._buffer_started_at is not None

    # Advance the clock past the delay and poke the flush check.
    tailer._maybe_flush(now=tailer._buffer_started_at + 11.0)
    assert [c["timestamp"] for c in collected] == [1.0]
    assert tailer._cursor_store.get(str(path)).offset == path.stat().st_size


def test_buffered_shutdown_flush(tmp_path, collected) -> None:
    tailer = _buffered_tailer(tmp_path, batch_size=100)
    path = tmp_path / "sessions" / "default_outcomes.jsonl"
    _write_lines(path, [_outcome(timestamp=1.0), _outcome(timestamp=2.0)])
    tailer._handle_change(Change.modified, path)
    assert collected == []  # below threshold, still buffered

    # Explicit flush (what run()'s finally-block does on shutdown).
    tailer._flush()
    assert [c["timestamp"] for c in collected] == [1.0, 2.0]
    assert tailer._cursor_store.get(str(path)).offset == path.stat().st_size


def test_buffered_crash_replays_from_last_flush(tmp_path, collected) -> None:
    """A crash before flush loses no data: a fresh Tailer (cursor at the
    last flushed offset) re-reads and re-buffers the un-flushed lines."""
    moneta = tmp_path / "moneta_storage"
    cursor_path = tmp_path / "cursor.json"
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    path = sessions / "default_outcomes.jsonl"

    tailer_a = Tailer(sessions, CursorStore(cursor_path), moneta, batch_size=10)
    _write_lines(path, [_outcome(timestamp=1.0), _outcome(timestamp=2.0)])
    tailer_a._handle_change(Change.modified, path)
    # "Crash": tailer_a is discarded with its buffer un-flushed. The
    # persisted cursor never advanced.
    assert CursorStore(cursor_path).get(str(path)) is None

    # Restart: a fresh Tailer seeds from the (empty) cursor and re-reads.
    tailer_b = Tailer(sessions, CursorStore(cursor_path), moneta, batch_size=10)
    tailer_b._handle_change(Change.modified, path)
    tailer_b._flush()
    assert [c["timestamp"] for c in collected] == [1.0, 2.0]
