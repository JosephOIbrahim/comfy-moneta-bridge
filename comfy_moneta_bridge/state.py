"""Cursor durability store for the JSONL tailer.

The bridge owns one piece of persistent state: a per-watched-file cursor
recording (inode, byte offset). Written atomically (temp file + fsync +
os.replace) after every successful ingest with-block exits cleanly. That
durability ordering — Moneta deposit + run_sleep_pass first, cursor write
second — is the bridge's idempotency floor. If the process dies between
the deposit and the cursor write, replay re-ingests the line and Moneta's
own logic resolves the duplicate. If the cursor write succeeds, the line
will not be re-ingested on restart.

Self-healing on read: a missing or malformed cursor file resolves to an
empty store. The next successful ingest re-establishes the cursor; the
"loss" is at most one outcome line of replay.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WatchState:
    """Per-watched-file durability cursor."""

    inode: int
    offset: int


class CursorStore:
    """Persistent (path -> WatchState) map with atomic, fsynced writes."""

    def __init__(self, cursor_path: Path) -> None:
        self.cursor_path = Path(cursor_path)
        self._cache: dict[str, WatchState] = self._load()

    def get(self, watched_path: str) -> WatchState | None:
        return self._cache.get(watched_path)

    def set(self, watched_path: str, state: WatchState) -> None:
        """Update the cursor for ``watched_path`` durably.

        Order: update the in-memory cache, write the full cache to a
        temp file in the same directory, fsync the temp fd, then
        os.replace the temp onto the cursor path. os.replace is atomic
        on Windows since CPython 3.3 — observers see either the old or
        the new file, never a partial.
        """
        new_cache = dict(self._cache)
        new_cache[watched_path] = state

        self.cursor_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.cursor_path.with_suffix(
            self.cursor_path.suffix + ".tmp"
        )
        serialized = {
            "watches": {
                path: asdict(s) for path, s in new_cache.items()
            }
        }
        with open(tmp_path, "w", encoding="utf-8") as fp:
            json.dump(serialized, fp, ensure_ascii=False)
            fp.flush()
            os.fsync(fp.fileno())
        os.replace(tmp_path, self.cursor_path)

        # Cache update is last so a crash mid-write does not advance the
        # in-memory state past the on-disk state.
        self._cache = new_cache

    def _load(self) -> dict[str, WatchState]:
        if not self.cursor_path.exists():
            return {}
        try:
            with open(self.cursor_path, "r", encoding="utf-8") as fp:
                blob = json.load(fp)
            watches = blob.get("watches", {})
            result: dict[str, WatchState] = {}
            for path, raw in watches.items():
                result[path] = WatchState(
                    inode=int(raw["inode"]),
                    offset=int(raw["offset"]),
                )
            return result
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
            _logger.warning(
                "cursor file %s is malformed (%s); starting empty",
                self.cursor_path,
                type(e).__name__,
            )
            return {}
