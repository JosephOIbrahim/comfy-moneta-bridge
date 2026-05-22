"""Cross-session semantic recall over Moneta state.

Public entry point: ``recall(query_text, moneta_storage_path,
top_k=10) -> list[dict]``.

Encodes ``query_text`` via the active embedder mode and queries
Moneta for the top-k matches by cosine similarity, dropping deposits
whose ``_embedder`` tag does not match the current mode (same logic as
``capsule.write_capsule``). Returns parsed outcome dicts in cosine-rank
order — best match first.

Pipeline:
  1. Encode the query (BGE under ``BRIDGE_EMBEDDER_MODE=bge``,
     synthetic otherwise).
  2. Open an ephemeral Moneta handle, over-fetch by cosine similarity.
     ``run_sleep_pass()`` is intentionally NOT called — query is
     read-only (Hard Rule §12 binds deposit, not query).
  3. Drop wrong-mode deposits; preserve cosine rank for the rest.
  4. Truncate to ``top_k``.

Synthetic mode is a degenerate fallback: ``synthesize_vector`` is
keyed off the literal query string, so synthetic-mode recall only
matches deposits whose session string equals ``query_text``. Real
semantic recall requires BGE mode; this is documented at the CLI
surface so an operator who runs without the env var sees the
limitation.
"""

from __future__ import annotations

import json
import logging
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


def recall(
    query_text: str,
    moneta_storage_path: Path,
    top_k: int = 10,
) -> list[dict]:
    """Return up to ``top_k`` outcome payloads ranked by semantic match.

    Over-fetches by a factor of 4 (floored at top_k+32) so version-
    mismatch drops do not starve the result set in mixed-mode storage.
    """
    if from_env() == "bge":
        # Real text on both sides of the encoder — no session-stub
        # workaround needed here. The asymmetry that capsule.py:120-127
        # had to backstop with a session-string filter does not apply
        # to cross-session recall.
        embedding = encode_outcome({"workflow_summary": query_text})
    else:
        embedding = synthesize_vector(query_text)

    fetch_limit = max(top_k * 4, top_k + 32)

    config = build_config(moneta_storage_path)
    with Moneta(config) as m:
        memories = m.query(embedding=embedding, limit=fetch_limit)

    expected_version = current_embedder_version()
    results: list[dict] = []
    skipped_wrong_version = 0
    for memory in memories:
        try:
            d = json.loads(memory.payload)
        except (json.JSONDecodeError, AttributeError):
            continue
        deposit_version = d.get("_embedder", EMBEDDER_VERSION_SYNTHETIC)
        if deposit_version != expected_version:
            skipped_wrong_version += 1
            continue
        results.append(d)
        if len(results) >= top_k:
            break

    if skipped_wrong_version:
        _logger.info(
            "recall: skipped %d memories from a different embedder "
            "version (current=%s)",
            skipped_wrong_version,
            expected_version,
        )

    return results
