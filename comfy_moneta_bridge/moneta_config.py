"""Shared MonetaConfig builder.

All bridge-side Moneta callers (ingest, capsule, recall, the agents
subpackage) construct the same ``MonetaConfig`` shape against the
same per-bridge storage URI. Duplicating that builder in three
modules made drift easy and a fourth-call-site impossible to keep in
sync; this module collapses them into one function.

The configuration is intentionally bound to ``vector.DIMENSION``
(currently 384) so any dim-mismatch surfaces loudly at first deposit
rather than silently corrupting the VectorIndex.
"""

from __future__ import annotations

from pathlib import Path

from moneta import MonetaConfig

from comfy_moneta_bridge.vector import DIMENSION


def build_config(moneta_storage_path: Path) -> MonetaConfig:
    """Construct the Moneta handle config for the bridge's storage URI.

    Creates the storage directory if missing. The returned config pins
    ``embedding_dim`` to ``DIMENSION`` so vector-index dim is locked
    from the first deposit.
    """
    storage = Path(moneta_storage_path)
    storage.mkdir(parents=True, exist_ok=True)
    return MonetaConfig(
        storage_uri=f"moneta-bridge://{storage.as_posix()}",
        snapshot_path=storage / "snapshot.json",
        wal_path=storage / "wal.jsonl",
        mock_target_log_path=storage / "usd_authorings.jsonl",
        embedding_dim=DIMENSION,
    )
