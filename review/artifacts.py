"""Atomic disk persistence for the review harness.

Mirrors the durability pattern in ``comfy_moneta_bridge/state.py``:
temp file in the same directory + fsync + ``os.replace``. ``os.replace``
is atomic on Windows since CPython 3.3, so observers see either the old
or the new file but never a partial.

The findings store is append-only JSONL (no rewrite); each iteration
appends new ``Finding`` snapshots. The orchestrator keeps the latest
status per ``id`` by reading the file and folding history.

The trace log is append-only JSONL.

The run state file is overwritten atomically before every Anthropic
call so that a crash mid-call resumes idempotently.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path

from .schemas import Finding, RunState, TraceEntry


def repo_root() -> Path:
    """Return the repository root.

    Anchors on the package directory: ``review/`` lives at repo root,
    so ``Path(__file__).resolve().parent.parent`` is the repo.
    """
    return Path(__file__).resolve().parent.parent


def reviews_root() -> Path:
    return repo_root() / ".claude" / "reviews"


def runs_root() -> Path:
    return reviews_root() / "runs"


def new_run_id() -> str:
    """UTC timestamp run id, sortable lexically."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")


def run_dir(run_id: str) -> Path:
    return runs_root() / run_id


def iter_dir(run_id: str, iteration: int) -> Path:
    return run_dir(run_id) / f"iter-{iteration}"


def _atomic_write_text(path: Path, text: str) -> None:
    """Atomic write: temp + fsync + os.replace.

    Lifted from ``comfy_moneta_bridge/state.py:CursorStore.set``.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as fp:
        fp.write(text)
        fp.flush()
        os.fsync(fp.fileno())
    os.replace(tmp_path, path)


def _atomic_append_jsonl(path: Path, record: dict) -> None:
    """Atomic append for JSONL.

    JSONL append is single-line; on POSIX a single ``write`` of <PIPE_BUF
    bytes is atomic, but to honor the project's durability discipline
    we read-modify-write through a temp file. This is O(N) per append
    and is acceptable at the harness's call rate (~50 calls per run).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = ""
    if path.exists():
        with open(path, "r", encoding="utf-8") as fp:
            existing = fp.read()
    line = json.dumps(record, ensure_ascii=False) + "\n"
    _atomic_write_text(path, existing + line)


def _to_jsonable(obj: object) -> object:
    if is_dataclass(obj):
        return {k: _to_jsonable(v) for k, v in asdict(obj).items()}
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(v) for v in obj]
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    return obj


def write_state(run_id: str, state: RunState) -> None:
    path = run_dir(run_id) / "state.json"
    _atomic_write_text(
        path, json.dumps(_to_jsonable(state), ensure_ascii=False, indent=2)
    )


def read_state(run_id: str) -> RunState | None:
    path = run_dir(run_id) / "state.json"
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as fp:
        blob = json.load(fp)
    return RunState(**blob)


def append_trace(run_id: str, entry: TraceEntry) -> None:
    _atomic_append_jsonl(
        run_dir(run_id) / "trace.jsonl", _to_jsonable(entry)  # type: ignore[arg-type]
    )


def append_finding(run_id: str, finding: Finding) -> None:
    _atomic_append_jsonl(
        run_dir(run_id) / "findings.jsonl",
        _to_jsonable(finding),  # type: ignore[arg-type]
    )


def read_findings(run_id: str) -> list[Finding]:
    """Read all finding snapshots and fold to latest-per-id.

    Append-only JSONL means later snapshots supersede earlier ones for
    the same id. We keep the latest occurrence.
    """
    path = run_dir(run_id) / "findings.jsonl"
    if not path.exists():
        return []
    latest: dict[str, dict] = {}
    with open(path, "r", encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            blob = json.loads(line)
            latest[blob["id"]] = blob
    out: list[Finding] = []
    for blob in latest.values():
        history_blobs = blob.get("history", []) or []
        from .schemas import HistoryEntry

        history = tuple(HistoryEntry(**h) for h in history_blobs)
        out.append(
            Finding(
                id=blob["id"],
                iter_introduced=blob["iter_introduced"],
                expert=blob["expert"],
                severity=blob["severity"],
                confidence=blob["confidence"],
                file=blob["file"],
                line_start=blob["line_start"],
                line_end=blob["line_end"],
                title=blob["title"],
                claim=blob["claim"],
                evidence_quote=blob["evidence_quote"],
                remediation=blob["remediation"],
                constitution_articles=tuple(blob["constitution_articles"]),
                status=blob["status"],
                history=history,
            )
        )
    return out


def write_iter_artifact(
    run_id: str, iteration: int, actor: str, content: str
) -> Path:
    """Write a per-iteration markdown artifact for one actor."""
    path = iter_dir(run_id, iteration) / f"{actor}.md"
    _atomic_write_text(path, content)
    return path


def write_final_report(run_id: str, content: str) -> Path:
    path = run_dir(run_id) / "FINAL_REPORT.md"
    _atomic_write_text(path, content)
    return path
