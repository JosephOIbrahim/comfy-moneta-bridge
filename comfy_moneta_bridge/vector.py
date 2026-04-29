"""Deterministic synthetic embedder.

Same session string -> identical unit vector. Different session strings ->
orthogonal-ish vectors via PRNG independence. Output is L2-normalized.

Phase 0.5b probe established Moneta accepts any positive-int dimensionality
and locks it at first deposit (or via MonetaConfig.embedding_dim). 384 is
the BGE-small-standard size and gives plenty of room for PRNG independence
between session strings.
"""

from __future__ import annotations

import hashlib
import math
import random

DIMENSION = 384


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
