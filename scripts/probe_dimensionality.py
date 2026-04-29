"""Phase 0.5b probe: discover Moneta's accepted embedding dimensions.

Usage (Moneta on PYTHONPATH):
    PYTHONPATH=C:/Users/User/Moneta/src python scripts/probe_dimensionality.py

For each candidate dim size, attempts a fresh ``Moneta.deposit`` with a
random unit vector. Each candidate uses its own ``MonetaConfig.storage_uri``
and its own snapshot/wal directory so the dim is not locked across probes
within a single process.

Reports JSON-line per candidate: ``{"dim": int, "ok": bool, ...}``. The
final summary line reports the chosen dim per the bridge mission's
v3 disposition rules.
"""

from __future__ import annotations

import json
import math
import random
import shutil
from pathlib import Path

from moneta import Moneta, MonetaConfig

CANDIDATES = [64, 128, 256, 384, 512, 768, 1024, 1536, 3072]
PROBE_ROOT = Path("./.moneta_probe/dim")


def random_unit_vector(dim: int, rng: random.Random) -> list[float]:
    raw = [rng.gauss(0, 1) for _ in range(dim)]
    norm = math.sqrt(sum(x * x for x in raw))
    return [x / norm for x in raw]


def fresh_dir(label: str) -> Path:
    d = PROBE_ROOT / label
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    return d


def probe_dim(dim: int, rng: random.Random) -> dict:
    """Try to deposit a vector of ``dim`` size into a fresh Moneta handle."""
    storage = fresh_dir(f"dim_{dim}")
    config = MonetaConfig(
        storage_uri=f"moneta-probe://dim-{dim}",
        snapshot_path=storage / "snapshot.json",
        wal_path=storage / "wal.jsonl",
        mock_target_log_path=storage / "usd_authorings.jsonl",
    )
    try:
        with Moneta(config) as m:
            eid = m.deposit(
                payload=f"probe-dim-{dim}",
                embedding=random_unit_vector(dim, rng),
            )
            results = m.query(random_unit_vector(dim, rng), limit=5)
            return {
                "dim": dim,
                "ok": True,
                "entity_id": str(eid),
                "query_returned": len(results),
            }
    except Exception as e:
        return {
            "dim": dim,
            "ok": False,
            "error_type": type(e).__name__,
            "error_message": str(e),
        }


def main() -> None:
    rng = random.Random(0xC0FFEE)
    PROBE_ROOT.mkdir(parents=True, exist_ok=True)
    results = [probe_dim(d, rng) for d in CANDIDATES]
    for r in results:
        print(json.dumps(r))
    accepted = [r["dim"] for r in results if r.get("ok")]
    rejected = [r for r in results if not r.get("ok")]
    print(json.dumps({
        "summary": True,
        "accepted_dims": accepted,
        "rejected_count": len(rejected),
        "all_accepted": len(rejected) == 0,
    }))


if __name__ == "__main__":
    main()
