"""Embedder layer: legacy synthetic + opt-in BGE-small content encoder.

Phase 1 introduces a real semantic encoder behind a mode switch.
``synthesize_vector`` (the deterministic PRNG-keyed embedder) remains
the default and the legacy fallback. ``encode_outcome`` uses
BAAI/bge-small-en-v1.5 to embed the outcome's content (workflow
summary, vision notes, key params), giving Moneta retrieval a real
semantic signal once ``BRIDGE_EMBEDDER_MODE=bge`` is set.

Phase 0.5b probe established Moneta accepts any positive-int
dimensionality and locks it at first deposit. 384 is BGE-small's
native size and matches the synthetic embedder, so a storage URI
written under one mode stays dim-compatible with the other (the
embedding *meaning* changes, but the index does not need a rebuild).

Mode is read from ``BRIDGE_EMBEDDER_MODE`` (``"synthetic"`` | ``"bge"``,
default ``"synthetic"``). Anything unrecognised silently falls back to
synthetic — synthetic is always safe.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import random

DIMENSION = 384
DEFAULT_MODE = "synthetic"
ENV_VAR = "BRIDGE_EMBEDDER_MODE"
BGE_MODEL_NAME = "BAAI/bge-small-en-v1.5"
EMBEDDER_VERSION_SYNTHETIC = "synthetic-v0"
EMBEDDER_VERSION_BGE = "bge-small-en-v1.5"

# Lazy singleton — populated on first ``encode_outcome`` call. Module-level
# so a single process amortises the ~100MB model download / load.
_bge_model = None


def synthesize_vector(session: str, dim: int = DIMENSION) -> list[float]:
    """Generate a deterministic unit vector keyed off the session string.

    Implementation:
      1. seed   = first 8 bytes of sha256(session.encode("utf-8")), big-endian.
      2. rng    = random.Random(seed)  (CPython random.Random is documented
                  stable across versions for a given seed.)
      3. raw    = dim independent draws from gauss(0, 1).
      4. norm   = L2 norm of raw.
      5. return raw / norm.
    """
    seed = int.from_bytes(
        hashlib.sha256(session.encode("utf-8")).digest()[:8], "big"
    )
    rng = random.Random(seed)
    raw = [rng.gauss(0.0, 1.0) for _ in range(dim)]
    norm = math.sqrt(sum(x * x for x in raw))
    return [x / norm for x in raw]


def from_env() -> str:
    """Return the embedder mode read from ``BRIDGE_EMBEDDER_MODE``.

    Valid values: ``"synthetic"`` (default) and ``"bge"``. Anything else
    falls back to ``"synthetic"`` — the legacy path is always safe.
    """
    mode = os.environ.get(ENV_VAR, DEFAULT_MODE).strip().lower()
    return mode if mode in {"synthetic", "bge"} else DEFAULT_MODE


def current_embedder_version() -> str:
    """Version tag stamped onto every deposit so query-side can reject
    cross-mode noise. Pinned to the implementation, not the mode string,
    so a future model swap (e.g. bge-base) gets a distinct tag without
    breaking the synthetic path's stable identity."""
    if from_env() == "bge":
        return EMBEDDER_VERSION_BGE
    return EMBEDDER_VERSION_SYNTHETIC


def _get_bge_model():
    """Lazy-load the BGE-small model. Raises ImportError if extra absent."""
    global _bge_model
    if _bge_model is None:
        from sentence_transformers import SentenceTransformer

        _bge_model = SentenceTransformer(BGE_MODEL_NAME)
    return _bge_model


def _outcome_to_text(outcome: dict) -> str:
    """Render an outcome dict to a single BGE-input string.

    Concatenates the semantically meaningful fields in a stable order so
    structurally identical outcomes encode identically. For session-only
    stub dicts (capsule's query path), this still yields a usable
    session-anchored doc, and capsule's payload-side ``session`` filter
    catches anything cosine-similarity surfaces from a different session.
    """
    parts: list[str] = []
    session = outcome.get("session")
    if session:
        parts.append(f"session: {session}")
    summary = outcome.get("workflow_summary")
    if summary:
        parts.append(f"workflow: {summary}")
    notes = outcome.get("vision_notes")
    if notes:
        rendered = " | ".join(
            n if isinstance(n, str) else json.dumps(n, ensure_ascii=False)
            for n in notes
        )
        parts.append(f"notes: {rendered}")
    key_params = outcome.get("key_params")
    if key_params:
        parts.append(
            f"params: {json.dumps(key_params, sort_keys=True, ensure_ascii=False)}"
        )
    feedback = outcome.get("user_feedback")
    if feedback:
        parts.append(f"feedback: {feedback}")
    return "\n".join(parts)


def encode_outcome(outcome: dict) -> list[float]:
    """Encode an outcome dict to a 384-dim L2-normalized BGE embedding.

    ``normalize_embeddings=True`` ensures unit length so cosine similarity
    in Moneta's vector index reduces to a dot product, matching the
    synthetic embedder's output convention.
    """
    text = _outcome_to_text(outcome)
    model = _get_bge_model()
    vec = model.encode(text, normalize_embeddings=True)
    return [float(x) for x in vec]
