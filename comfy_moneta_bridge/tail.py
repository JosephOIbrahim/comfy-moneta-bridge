"""Rotation-aware, encoding-safe, partial-line-safe JSONL tailer.

Watches ``{sessions_dir}/*_outcomes.jsonl`` via watchfiles. On each
filesystem event, opens the file in binary mode, seeks to the cursor's
last_offset, reads remaining bytes, and closes — never holding a
persistent file handle (Windows would block Comfy-Cozy's ``os.replace``
during rotation).

Decoded utf-8 explicitly per line. The bytes/decode split keeps cursor
arithmetic unambiguous across multi-byte unicode (emoji in vision_notes,
etc.); a text-mode seek would couple to TextIOWrapper's opaque tell()
semantics. Hard Rule §4's intent — "no cp1252 silent corruption" — is
honored by the explicit utf-8 decode on every read.

Rotation handling: Comfy-Cozy renames ``foo_outcomes.jsonl`` to
``foo_outcomes.jsonl.1`` before creating a fresh ``.jsonl``. We detect
rotation via any of (Change.deleted event, file size shrunk below
last_offset, inode mismatch) and drain stranded lines from the ``.1``
file before resetting state for the new file. Any line that landed in
the original file between the cursor and the rename is recovered.

Cursor advancement is end-of-batch: after ``ingest_outcome`` is called
for every parsed line in a single drain. Per v3.1 Hard Rule §12,
``ingest_outcome`` itself is durable (it runs ``run_sleep_pass()`` inside
its with-block), so a clean return from the batch loop means every
deposited line is on disk. Cursor write happens last; if the bridge dies
mid-batch the next restart re-reads from the pre-batch cursor and
re-ingests, producing duplicate Moneta deposits within the batch span.
This is the documented v0 limitation.
"""

from __future__ import annotations

import asyncio
import fnmatch
import json
import logging
import time
from pathlib import Path

from watchfiles import Change, awatch

from comfy_moneta_bridge import ingest
from comfy_moneta_bridge.state import CursorStore, WatchState

_logger = logging.getLogger(__name__)

OUTCOMES_GLOB = "*_outcomes.jsonl"


class Tailer:
    """Rotation-aware JSONL tailer."""

    def __init__(
        self,
        sessions_dir: Path,
        cursor_store: CursorStore,
        moneta_storage_path: Path,
        batch_size: int = 1,
        batch_max_delay_s: float = 0.0,
    ) -> None:
        self._sessions_dir = Path(sessions_dir)
        self._cursor_store = cursor_store
        self._moneta_storage_path = Path(moneta_storage_path)
        # Opt-in cross-event batching (L1). Default (size=1, delay=0)
        # flushes every event — byte-identical to the v0 behavior.
        self._batch_size = max(1, int(batch_size))
        self._batch_max_delay_s = max(0.0, float(batch_max_delay_s))
        # Buffered-mode state. ``_live_state`` is the in-memory
        # authoritative (inode, read-offset) per watched file; the
        # persisted cursor lags it and only catches up on flush, so a
        # crash re-reads from the last *durable* offset (wider replay
        # window — the documented batching tradeoff).
        self._buffer: list[dict] = []
        self._live_state: dict[str, WatchState] = {}
        self._pending_keys: set[str] = set()
        self._buffer_started_at: float | None = None

    @property
    def _batching_enabled(self) -> bool:
        return self._batch_size > 1 or self._batch_max_delay_s > 0

    def _get_state(self, watched_key: str) -> WatchState | None:
        """Read the drain state. Default mode reads the persisted
        cursor; buffered mode reads the in-memory live state (seeded
        once from the cursor) so deferred-cursor events don't re-read
        already-buffered lines."""
        if not self._batching_enabled:
            return self._cursor_store.get(watched_key)
        if watched_key not in self._live_state:
            seeded = self._cursor_store.get(watched_key)
            if seeded is not None:
                self._live_state[watched_key] = seeded
        return self._live_state.get(watched_key)

    def _set_state(self, watched_key: str, state: WatchState) -> None:
        """Advance the drain state. Default mode writes the cursor
        immediately; buffered mode advances only the in-memory live
        state and marks the key pending — the cursor is persisted on
        flush, after the buffered batch is durable."""
        if not self._batching_enabled:
            self._cursor_store.set(watched_key, state)
            return
        self._live_state[watched_key] = state
        self._pending_keys.add(watched_key)
        if self._buffer_started_at is None:
            self._buffer_started_at = time.monotonic()

    def _maybe_flush(self, now: float | None = None) -> None:
        if not self._buffer and not self._pending_keys:
            return
        now = time.monotonic() if now is None else now
        count_trigger = len(self._buffer) >= self._batch_size
        time_trigger = (
            self._batch_max_delay_s > 0
            and self._buffer_started_at is not None
            and (now - self._buffer_started_at) >= self._batch_max_delay_s
        )
        if count_trigger or time_trigger:
            self._flush()

    def _flush(self) -> None:
        """Deposit the buffered batch (one handle + one run_sleep_pass)
        then persist every pending cursor. Order matters: the cursor is
        advanced only AFTER ``ingest_batch`` returns durable, preserving
        Hard Rule §12 — a crash before this point re-reads from the last
        flushed offset."""
        if self._buffer:
            ingest.ingest_batch(self._buffer, self._moneta_storage_path)
            self._buffer = []
        for key in self._pending_keys:
            self._cursor_store.set(key, self._live_state[key])
        self._pending_keys = set()
        self._buffer_started_at = None

    async def run(self, stop_event: asyncio.Event | None = None) -> None:
        """Watch the sessions directory until ``stop_event`` is set.

        Yields control to asyncio between change-set batches. Each event
        is dispatched to ``_handle_change``. Filename filtering happens
        here so non-matching writes (e.g. ``_goals.json``) are ignored.

        In buffered mode the watch yields on timeout so the delay-based
        flush fires even when no new events arrive, and a final flush on
        exit persists anything still buffered at shutdown.
        """
        watch_kwargs: dict = {"stop_event": stop_event}
        if self._batching_enabled and self._batch_max_delay_s > 0:
            watch_kwargs["yield_on_timeout"] = True
            watch_kwargs["rust_timeout"] = max(
                50, int(self._batch_max_delay_s * 1000)
            )
        try:
            async for change_set in awatch(
                self._sessions_dir, **watch_kwargs
            ):
                for change, raw_path in change_set:
                    path = Path(raw_path)
                    if not fnmatch.fnmatch(path.name, OUTCOMES_GLOB):
                        continue
                    try:
                        self._handle_change(change, path)
                    except Exception:  # noqa: BLE001
                        _logger.exception(
                            "tail._handle_change raised on %s; cursor "
                            "not advanced", path,
                        )
                if self._batching_enabled:
                    self._maybe_flush()
        finally:
            if self._batching_enabled:
                # Shutdown: persist whatever is still buffered.
                self._flush()

    def _handle_change(self, change: Change, path: Path) -> None:
        """Route one filesystem event to drain / rotation logic.

        Cursor is advanced once per batch, after every parsed line in
        the batch has been handed to ``ingest_outcome`` (and that call
        returned cleanly — it runs ``run_sleep_pass()`` so the deposit
        is durable on its return).
        """
        watched_key = str(path)
        state = self._get_state(watched_key) or WatchState(
            inode=0, offset=0
        )

        # Rotation detection: any of (a) Deleted event, (b) file size
        # shrunk below last_offset, (c) inode mismatch on the live file.
        rotation = False
        if change == Change.deleted:
            rotation = True
        elif path.exists():
            try:
                stat = path.stat()
            except OSError as e:
                _logger.warning("stat failed on %s: %s; deferring", path, e)
                return
            if state.inode != 0 and stat.st_ino != state.inode:
                rotation = True
            elif stat.st_size < state.offset:
                rotation = True
        else:
            # Path doesn't exist and event isn't Deleted — transient
            # disappear (e.g., pending rename). Defer to next event.
            return

        if rotation:
            # Flush any pre-rotation buffer first so its lines deposit
            # (and their cursor persists) before the offset resets to 0.
            if self._batching_enabled:
                self._flush()
            state = self._handle_rotation(path, state)
            self._set_state(watched_key, state)
            if not path.exists():
                # New file not yet created. Wait for the next event.
                return

        # First-time discovery of the live file: bind the inode now so
        # the next rotation check has a real reference value.
        if state.inode == 0:
            try:
                state = WatchState(
                    inode=path.stat().st_ino, offset=state.offset
                )
            except OSError:
                return

        parsed_lines, new_offset = self._drain_complete_lines(
            path, state.offset
        )
        # Default mode: one handle + one run_sleep_pass for this drained
        # batch, cursor advances now (replay window unchanged from v0).
        # Buffered mode: stage the lines and defer both the deposit and
        # the cursor advance to the next flush.
        if self._batching_enabled:
            if parsed_lines:
                self._buffer.extend(parsed_lines)
                if self._buffer_started_at is None:
                    self._buffer_started_at = time.monotonic()
            if new_offset != state.offset:
                self._set_state(
                    watched_key,
                    WatchState(inode=state.inode, offset=new_offset),
                )
            self._maybe_flush()
        else:
            if parsed_lines:
                ingest.ingest_batch(
                    parsed_lines, self._moneta_storage_path
                )
            if new_offset != state.offset:
                self._set_state(
                    watched_key,
                    WatchState(inode=state.inode, offset=new_offset),
                )

    def _drain_complete_lines(
        self, path: Path, start_offset: int
    ) -> tuple[list[dict], int]:
        """Read from ``start_offset``, return ``(parsed_lines, new_offset)``.

        Open in binary mode for unambiguous byte arithmetic; decode
        utf-8 explicitly per line. ``new_offset`` never advances past
        the start of an incomplete trailing line: if the file ends
        without a final ``\\n``, the trailing fragment is held back
        for the next event.

        Malformed lines (utf-8 decode error or json parse error on a
        complete line) are logged and skipped; the offset advances past
        them so they don't poison the cursor forever.
        """
        with open(path, "rb") as fp:
            fp.seek(start_offset)
            data = fp.read()

        # split[:-1] is "complete segments" in both cases:
        #   - data ending in \n  -> last split is empty
        #   - data ending mid-line -> last split is the incomplete trailer
        # We hold the last element back either way; offset only advances
        # for completed lines.
        segments = data.split(b"\n")
        complete_segments = segments[:-1]

        parsed_lines: list[dict] = []
        new_offset = start_offset
        for segment in complete_segments:
            bytes_consumed = len(segment) + 1  # +1 for the \n
            try:
                text = segment.decode("utf-8")
                parsed = json.loads(text)
            except UnicodeDecodeError as e:
                _logger.warning(
                    "malformed utf-8 at offset %d in %s: %s",
                    new_offset, path, e,
                )
            except json.JSONDecodeError as e:
                _logger.warning(
                    "malformed JSON at offset %d in %s: %s",
                    new_offset, path, e,
                )
            else:
                parsed_lines.append(parsed)
            new_offset += bytes_consumed

        return parsed_lines, new_offset

    def _handle_rotation(
        self, path: Path, state: WatchState
    ) -> WatchState:
        """Drain stranded bytes from ``{path}.1`` (if any), reset state.

        Comfy-Cozy's rotation: ``foo.jsonl`` -> ``foo.jsonl.1``, then a
        fresh ``foo.jsonl`` is created. Any lines past ``state.offset``
        in the original file land in ``.1``; we read them so they don't
        slip through. The new file's inode is bound here if it exists;
        otherwise inode is reset to 0 (sentinel) for next-event binding.
        """
        rotated = path.with_suffix(path.suffix + ".1")
        if rotated.exists() and state.offset > 0:
            stranded, _ = self._drain_complete_lines(rotated, state.offset)
            if stranded:
                ingest.ingest_batch(stranded, self._moneta_storage_path)

        if path.exists():
            try:
                new_inode = path.stat().st_ino
            except OSError:
                new_inode = 0
        else:
            new_inode = 0
        return WatchState(inode=new_inode, offset=0)
