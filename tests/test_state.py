"""Tests for comfy_moneta_bridge.state — CursorStore.

Per mission v3.1 Phase 2 test table. CRUCIBLE bias: assert atomicity by
mocking os.replace to raise mid-write; assert fsync invoked; assert
self-healing against a deliberately malformed cursor file; assert unicode
paths round-trip without encoding bugs.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from comfy_moneta_bridge.state import CursorStore, WatchState


def test_empty_store_on_first_init(tmp_path: Path) -> None:
    store = CursorStore(tmp_path / "cursor.json")
    assert store.get("any/path") is None
    assert store.get("") is None


def test_set_then_get_round_trips(tmp_path: Path) -> None:
    store = CursorStore(tmp_path / "cursor.json")
    state = WatchState(inode=12345, offset=2048)
    store.set("G:/Comfy-Cozy/sessions/default_outcomes.jsonl", state)
    got = store.get("G:/Comfy-Cozy/sessions/default_outcomes.jsonl")
    assert got == state
    # Other paths still absent.
    assert store.get("G:/Comfy-Cozy/sessions/other.jsonl") is None


def test_persists_across_restarts(tmp_path: Path) -> None:
    cursor_path = tmp_path / "cursor.json"
    state = WatchState(inode=99, offset=1024)
    store_a = CursorStore(cursor_path)
    store_a.set("p", state)

    # Fresh instance reads what the first one wrote.
    store_b = CursorStore(cursor_path)
    assert store_b.get("p") == state


def test_atomic_write_no_partial_files(tmp_path: Path) -> None:
    """If os.replace raises, the cursor file must not be partially updated."""
    cursor_path = tmp_path / "cursor.json"
    store = CursorStore(cursor_path)
    # Pre-populate so there is a "before" state to preserve.
    initial = WatchState(inode=1, offset=10)
    store.set("p", initial)

    new = WatchState(inode=2, offset=20)
    with patch(
        "comfy_moneta_bridge.state.os.replace",
        side_effect=OSError("simulated replace failure"),
    ):
        with pytest.raises(OSError):
            store.set("p", new)

    # On-disk state is the original; in-memory cache should not have
    # advanced past it (because replace raised before the cache update).
    fresh = CursorStore(cursor_path)
    assert fresh.get("p") == initial
    # The temp file should not exist (or if it does, it's not the cursor
    # file). Cursor file remains valid JSON containing the old state.
    with open(cursor_path, encoding="utf-8") as fp:
        blob = json.load(fp)
    assert blob["watches"]["p"]["offset"] == 10


def test_fsync_called(tmp_path: Path) -> None:
    """fsync must run on the temp fd before os.replace."""
    store = CursorStore(tmp_path / "cursor.json")
    with patch("comfy_moneta_bridge.state.os.fsync") as mock_fsync:
        store.set("p", WatchState(inode=1, offset=10))
        assert mock_fsync.called
        # Argument is a file descriptor (int), not a path or fd object.
        (call,) = mock_fsync.call_args_list
        (fd,) = call.args
        assert isinstance(fd, int)


def test_malformed_cursor_file_returns_empty(tmp_path: Path) -> None:
    cursor_path = tmp_path / "cursor.json"
    cursor_path.write_text("this is not json {{{", encoding="utf-8")
    store = CursorStore(cursor_path)
    assert store.get("p") is None
    # Set must still work (store reset to empty, then writes the new entry).
    store.set("p", WatchState(inode=1, offset=10))
    assert store.get("p") == WatchState(inode=1, offset=10)


def test_unicode_path_handled(tmp_path: Path) -> None:
    cursor_path = tmp_path / "cursor.json"
    store = CursorStore(cursor_path)
    weird_path = "G:/Comfy-Cozy/sessions/séssîön_测试_outcomes.jsonl"
    state = WatchState(inode=42, offset=512)
    store.set(weird_path, state)

    fresh = CursorStore(cursor_path)
    assert fresh.get(weird_path) == state

    # And the on-disk JSON is valid utf-8.
    raw = cursor_path.read_text(encoding="utf-8")
    assert "séssîön_测试" in raw
