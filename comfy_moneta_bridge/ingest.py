"""Per-line ingest pipeline: validate, embed, deposit, persist.

One public entry point: ``ingest_outcome(outcome, moneta_storage_path)``.
Validates ``schema_version == 1``, synthesizes a deterministic vector
keyed off the outcome's session string, deposits the JSON-dumped outcome
to a freshly-opened ephemeral Moneta handle, then calls
``m.run_sleep_pass()`` inside the same with-block to snapshot the ECS to
disk before close.

The ``run_sleep_pass()`` step is Hard Rule §12. ``Moneta.deposit()``
writes only to in-memory ECS + VectorIndex; ``Moneta.close()`` does not
snapshot. Without ``run_sleep_pass()`` the deposit is silently lost on
handle close (verified by ``scripts/probe_durability.py``). A clean
return from this function therefore means: deposit is on disk and
survives process death.

The function returns ``None``. Optional outcome fields (``goal_id``,
``render_time_s``, ``quality_score``) are preserved verbatim in the
JSON-dumped payload — no truthiness coercion, no defaulting to zero.
No bridge-side dedupe: cursor durability in ``state.CursorStore`` is
the v0 idempotency mechanism.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from moneta import Moneta

from comfy_moneta_bridge.moneta_config import build_config
from comfy_moneta_bridge.vector import (
    current_embedder_version,
    encode_outcome,
    from_env,
    synthesize_vector,
)

_logger = logging.getLogger(__name__)

EXPECTED_SCHEMA_VERSION = 1


def ingest_outcome(outcome: dict, moneta_storage_path: Path) -> None:
    """Validate one outcome line and deposit it durably to Moneta.

    Returns once the with-block has exited cleanly: ECS state is
    snapshotted on disk, the URI lock is released. Caller (``tail.py``)
    uses that signal to advance the cursor.
    """
    schema_version = outcome.get("schema_version")
    if schema_version != EXPECTED_SCHEMA_VERSION:
        _logger.warning(
            "ingest_outcome: dropping line with schema_version=%r "
            "(expected %d)",
            schema_version,
            EXPECTED_SCHEMA_VERSION,
        )
        return

    session = outcome.get("session", "default")
    if from_env() == "bge":
        embedding = encode_outcome(outcome)
    else:
        embedding = synthesize_vector(session)
    # Decorate the storage payload (not the caller's outcome) with the
    # embedder version. Mode flips between deposits would otherwise mix
    # incomparable vectors into one VectorIndex with no recovery signal.
    tagged = {**outcome, "_embedder": current_embedder_version()}
    payload = json.dumps(tagged, sort_keys=True, ensure_ascii=False)

    config = build_config(moneta_storage_path)
    with Moneta(config) as m:
        m.deposit(payload=payload, embedding=embedding)
        # Hard Rule §12: persistence requires run_sleep_pass(). Without
        # it the deposit lives only in the in-memory ECS and is lost
        # when the with-block exits. Verified empirically by
        # scripts/probe_durability.py.
        m.run_sleep_pass()
