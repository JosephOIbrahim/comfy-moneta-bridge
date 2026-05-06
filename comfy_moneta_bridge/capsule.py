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
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from moneta import Moneta, MonetaConfig

from comfy_moneta_bridge.vector import (
    DIMENSION,
    EMBEDDER_VERSION_SYNTHETIC,
    current_embedder_version,
    encode_outcome,
    from_env,
    synthesize_vector,
)

_logger = logging.getLogger(__name__)

CAPSULE_SCHEMA_VERSION = 2


def _build_config(moneta_storage_path: Path) -> MonetaConfig:
    storage = Path(moneta_storage_path)
    storage.mkdir(parents=True, exist_ok=True)
    return MonetaConfig(
        storage_uri=f"moneta-bridge://{storage.as_posix()}",
        snapshot_path=storage / "snapshot.json",
        wal_path=storage / "wal.jsonl",
        mock_target_log_path=storage / "usd_authorings.jsonl",
        embedding_dim=DIMENSION,
    )


def _empty_workflow_block() -> dict:
    return {
        "loaded_path": None,
        "format": "api",
        "base_workflow": None,
        "current_workflow": None,
        "history_depth": 0,
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


def write_capsule(
    session_name: str,
    comfy_cozy_root: Path,
    moneta_storage_path: Path,
    query_limit: int = 1000,
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
    config = _build_config(moneta_storage_path)

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

    saved_at = time.strftime("%Y-%m-%dT%H:%M:%S")
    notes: list[dict] = []
    for outcome in parsed:
        notes.extend(_outcome_to_notes(outcome, saved_at))

    capsule = {
        "name": session_name,
        "saved_at": saved_at,
        "schema_version": CAPSULE_SCHEMA_VERSION,
        "workflow": _empty_workflow_block(),
        "notes": notes,
        "metadata": {
            "hydrated_from": "moneta",
            "memory_count": len(parsed),
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
