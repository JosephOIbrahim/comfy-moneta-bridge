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


def _prepare(outcome: dict) -> tuple[str, list[float]] | None:
    """Validate + embed one outcome. Returns ``(payload, embedding)``
    or ``None`` if the schema version is wrong (logged + dropped).

    Embedding/serialization happens outside any Moneta handle so the
    handle is held for the minimum time (Hard Rule §6 ephemerality).
    """
    schema_version = outcome.get("schema_version")
    if schema_version != EXPECTED_SCHEMA_VERSION:
        _logger.warning(
            "ingest: dropping line with schema_version=%r (expected %d)",
            schema_version,
            EXPECTED_SCHEMA_VERSION,
        )
        return None

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
    return payload, embedding


def ingest_batch(outcomes: list[dict], moneta_storage_path: Path) -> int:
    """Deposit a batch of outcomes under ONE Moneta handle + ONE snapshot.

    The latency fix (BRIDGE_BUILD_MISSION_v3_2 §performance / L1-L2):
    ``run_sleep_pass()`` snapshots the entire ECS to disk and is the
    per-deposit cost ceiling (architecture.md benchmark). Coalescing N
    deposits into one handle with a single trailing ``run_sleep_pass()``
    turns N full snapshots into one.

    Durability (Hard Rule §12) is preserved exactly: the function
    returns only after the single ``run_sleep_pass()`` has completed, so
    a clean return means every deposited line in the batch is on disk —
    the same signal ``tail.py`` uses to advance the cursor. Schema-
    invalid lines are dropped (logged); if nothing is valid, no handle
    is opened. Returns the number of outcomes actually deposited.
    """
    prepared = [p for p in (_prepare(o) for o in outcomes) if p is not None]
    if not prepared:
        return 0

    config = build_config(moneta_storage_path)
    with Moneta(config) as m:
        for payload, embedding in prepared:
            m.deposit(payload=payload, embedding=embedding)
        # Hard Rule §12: persistence requires run_sleep_pass(). One call
        # snapshots the whole batch. Without it every deposit lives only
        # in the in-memory ECS and is lost when the with-block exits.
        # Verified empirically by scripts/probe_durability.py.
        m.run_sleep_pass()
    return len(prepared)


def ingest_outcome(outcome: dict, moneta_storage_path: Path) -> None:
    """Validate one outcome line and deposit it durably to Moneta.

    Thin single-line wrapper over :func:`ingest_batch`. Returns once the
    with-block has exited cleanly: ECS state is snapshotted on disk, the
    URI lock is released. Caller uses that signal to advance the cursor.
    """
    ingest_batch([outcome], moneta_storage_path)

