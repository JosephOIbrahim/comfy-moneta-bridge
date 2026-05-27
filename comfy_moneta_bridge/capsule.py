"""Hydrate Comfy-Cozy session JSON from Moneta state.

Public entry point: ``write_capsule(session_name, comfy_cozy_root,
moneta_storage_path, query_limit=1000) -> Path``.

Pipeline (mission v3.1 §"Phase 5"):
  1. Synthesize the session's deterministic vector.
  2. Open an ephemeral Moneta handle, query top-k by cosine similarity.
     ``run_sleep_pass()`` is intentionally NOT called — query is
     read-only, no durability work needed.
  3. Filter results to those whose payload's ``session`` field matches
     ``session_name`` (PRNG-collision guard against cosine-near-1 false
     positives across distinct session strings).
  4. Sort chronologically by the outcome's ``timestamp``.
  5. Translate to Comfy-Cozy ``schema_version=2`` (see session.py at
     ``G:\\Comfy-Cozy\\agent\\memory\\session.py``).
  6. Atomic temp+rename to ``sessions/{name}.json``.

Schema translation:
  - ``vision_notes`` -> ``notes`` entries with ``type="observation"``
  - ``key_params`` + ``quality_score`` -> one ``notes`` entry with
    ``type="preference"``
  - ``workflow`` block stubbed with safe defaults (the bridge does not
    own the actual workflow JSON; ``bridge hydrate`` produces a capsule
    that loads with empty workflow state)
  - ``metadata`` records the hydration source and memory count
  - cross-session semantic memories (SPEC P7) are folded in as
    ``observation`` notes naming their origin session in the text
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from moneta import Moneta

from comfy_moneta_bridge.moneta_config import build_config
from comfy_moneta_bridge.vector import (
    EMBEDDER_VERSION_SYNTHETIC,
    current_embedder_version,
    encode_outcome,
    from_env,
    synthesize_vector,
)

_logger = logging.getLogger(__name__)

CAPSULE_SCHEMA_VERSION = 2


def _empty_workflow_block() -> dict:
    return {
        "loaded_path": None,
        "format": "api",
        "base_workflow": None,
        "current_workflow": None,
        "history_depth": 0,
    }


WORKFLOW_SNAPSHOT_KIND = "workflow_snapshot"


def _workflow_block_from_snapshot(snapshot_payload: dict) -> dict:
    """Build a populated workflow block from a `_kind=workflow_snapshot`
    deposit payload.

    Per BRIDGE_BUILD_MISSION_v3_2.md scope addition K. The orchestrator
    emits one snapshot deposit at end-of-run; ``write_capsule`` picks
    up the latest one per session and threads it into the capsule's
    workflow block so Comfy-Cozy can load with the actual workflow on
    the next ``AUTO_LOAD_SESSION`` spawn.

    The deposit payload schema:
      {
        "schema_version": 1,           # routed through ingest_outcome
        "session": <session_name>,
        "timestamp": <float>,
        "_kind": "workflow_snapshot",
        "_embedder": <tag>,
        "workflow": {<api-format dict>},
        "loaded_path": <str | None>,   # optional source path hint
      }
    """
    return {
        "loaded_path": snapshot_payload.get("loaded_path"),
        "format": "api",
        "base_workflow": snapshot_payload.get("workflow"),
        "current_workflow": snapshot_payload.get("workflow"),
        "history_depth": 1,
    }


def _outcome_to_notes(outcome: dict, saved_at: str) -> list[dict]:
    """Translate one outcome dict to zero or more schema_v2 note entries."""
    notes: list[dict] = []

    for vn in outcome.get("vision_notes") or []:
        text = vn if isinstance(vn, str) else json.dumps(vn, ensure_ascii=False)
        notes.append(
            {
                "text": text,
                "type": "observation",
                "added_at": saved_at,
            }
        )

    key_params = outcome.get("key_params")
    quality = outcome.get("quality_score")
    if key_params or quality is not None:
        params_text = json.dumps(
            key_params or {}, sort_keys=True, ensure_ascii=False
        )
        quality_text = "n/a" if quality is None else f"{quality}"
        notes.append(
            {
                "text": (
                    f"Workflow params {params_text} achieved quality "
                    f"{quality_text}"
                ),
                "type": "preference",
                "added_at": saved_at,
            }
        )

    return notes


def _session_query_text(outcomes: list[dict]) -> str:
    """Build a semantic query from a session's own recent outcomes.

    Concatenates recent workflow summaries + vision notes so cross-session
    recall lands on memories semantically related to what this session has
    been doing. Empty if the session has no textual content to anchor on.
    """
    parts: list[str] = []
    for o in outcomes[-3:]:
        summary = o.get("workflow_summary")
        if summary:
            parts.append(str(summary))
        for vn in o.get("vision_notes") or []:
            if isinstance(vn, str):
                parts.append(vn)
    return " ".join(parts).strip()


def _cross_session_notes(
    session_name: str,
    outcomes: list[dict],
    moneta_storage_path: Path,
    saved_at: str,
    top_k: int,
) -> list[dict]:
    """Fold in top-k cross-session semantic memories (SPEC P7 / leaf L5).

    Uses the session's own content as a query, retrieves semantically
    related memories from OTHER sessions via ``recall`` (same embedder
    mode + ``_embedder`` discipline), and renders them as schema_v2
    ``observation`` notes with the origin session named in the text — no
    new capsule field, so Comfy-Cozy's loader stays untouched.

    Best-effort: any failure is logged and the capsule still writes with
    its session-local notes.
    """
    query_text = _session_query_text(outcomes)
    if not query_text:
        return []

    from comfy_moneta_bridge.recall import recall

    try:
        hits = recall(query_text, moneta_storage_path, top_k=top_k + 16)
    except Exception as e:  # noqa: BLE001 — cross-session is non-critical
        _logger.warning(
            "write_capsule: cross-session recall failed (%s); capsule "
            "continues with session-local notes only",
            type(e).__name__,
        )
        return []

    notes: list[dict] = []
    seen: set[tuple] = set()
    for h in hits:
        sess = h.get("session")
        if sess == session_name or h.get("_kind") == WORKFLOW_SNAPSHOT_KIND:
            continue
        summary = (h.get("workflow_summary") or "").strip()
        key = (sess, summary)
        if key in seen:
            continue
        seen.add(key)
        notes.append(
            {
                "text": f"[related memory · session '{sess}'] {summary}".strip(),
                "type": "observation",
                "added_at": saved_at,
            }
        )
        if len(notes) >= top_k:
            break
    return notes


def write_capsule(
    session_name: str,
    comfy_cozy_root: Path,
    moneta_storage_path: Path,
    query_limit: int = 1000,
    cross_session_top_k: int = 3,
) -> Path:
    """Build and atomically write ``sessions/{session_name}.json``.

    Returns the path to the written capsule.
    """
    if from_env() == "bge":
        # Capsule has only the session name to query with. Hand the BGE
        # encoder a session-anchored stub so the query vector lands near
        # same-session deposits; the payload-side ``session`` filter
        # below catches anything cosine drags in from other sessions.
        embedding = encode_outcome({"session": session_name})
    else:
        embedding = synthesize_vector(session_name)
    config = build_config(moneta_storage_path)

    with Moneta(config) as m:
        memories = m.query(embedding=embedding, limit=query_limit)

    if len(memories) >= query_limit:
        _logger.warning(
            "write_capsule: query_limit=%d reached for session %r — "
            "older memories may be truncated; pagination is a v1 candidate",
            query_limit,
            session_name,
        )

    expected_version = current_embedder_version()
    parsed: list[dict] = []
    skipped_wrong_version = 0
    for memory in memories:
        try:
            d = json.loads(memory.payload)
        except (json.JSONDecodeError, AttributeError) as e:
            _logger.warning(
                "write_capsule: skipping unparseable payload (%s)",
                type(e).__name__,
            )
            continue
        # Reject deposits made under a different embedder. Pre-Day-2
        # deposits have no tag → treat as synthetic-v0 (the only mode
        # that existed). Mode-mismatch deposits are silently dropped so
        # the capsule never mixes vector spaces.
        deposit_version = d.get("_embedder", EMBEDDER_VERSION_SYNTHETIC)
        if deposit_version != expected_version:
            skipped_wrong_version += 1
            continue
        if d.get("session") != session_name:
            # PRNG-collision guard: cosine-near-1 across distinct seeds
            # would still surface another session's memory; filter it out.
            continue
        parsed.append(d)

    if skipped_wrong_version:
        _logger.info(
            "write_capsule: skipped %d memories from a different embedder "
            "version (current=%s)",
            skipped_wrong_version,
            expected_version,
        )

    parsed.sort(key=lambda o: o.get("timestamp") or 0.0)

    # Snapshot extraction is internal-only: agent-deposited workflow
    # snapshots ride the same ingest path as normal outcomes (same
    # schema_version=1, same _embedder tag) but are discriminated by
    # `_kind=workflow_snapshot`. We split them out of `parsed` so they
    # don't pollute the notes feed, then pick the latest by timestamp.
    snapshots = [o for o in parsed if o.get("_kind") == WORKFLOW_SNAPSHOT_KIND]
    outcomes = [o for o in parsed if o.get("_kind") != WORKFLOW_SNAPSHOT_KIND]

    workflow_block = (
        _workflow_block_from_snapshot(snapshots[-1])
        if snapshots
        else _empty_workflow_block()
    )

    saved_at = time.strftime("%Y-%m-%dT%H:%M:%S")
    notes: list[dict] = []
    for outcome in outcomes:
        notes.extend(_outcome_to_notes(outcome, saved_at))

    # SPEC P7 / leaf L5: fold in semantically-related memories from OTHER
    # sessions so a freshly-hydrated session sees relevant cross-session
    # context, not just its own history.
    cross_notes: list[dict] = []
    if cross_session_top_k > 0:
        cross_notes = _cross_session_notes(
            session_name, outcomes, moneta_storage_path, saved_at,
            cross_session_top_k,
        )
        notes.extend(cross_notes)

    capsule = {
        "name": session_name,
        "saved_at": saved_at,
        "schema_version": CAPSULE_SCHEMA_VERSION,
        "workflow": workflow_block,
        "notes": notes,
        "metadata": {
            "hydrated_from": "moneta",
            "memory_count": len(outcomes),
            "cross_session_count": len(cross_notes),
        },
    }

    sessions_dir = Path(comfy_cozy_root) / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    out_path = sessions_dir / f"{session_name}.json"
    tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")

    with open(tmp_path, "w", encoding="utf-8") as fp:
        json.dump(capsule, fp, sort_keys=True, ensure_ascii=False, indent=2)
        fp.flush()
        os.fsync(fp.fileno())
    os.replace(tmp_path, out_path)
    return out_path
