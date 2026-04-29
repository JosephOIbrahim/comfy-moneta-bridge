"""Phase 0.5b probe: verify Moneta deposit durability across handle close.

Usage (Moneta on PYTHONPATH):
    PYTHONPATH=C:/Users/User/Moneta/src python scripts/probe_durability.py

Tests three patterns:
    A. open -> deposit -> close -> reopen -> count
       (mission v3 spec; deposit-only inside with-block)
    B. open -> deposit -> run_sleep_pass -> close -> reopen -> count
       (deposit + explicit consolidation snapshot)
    C. open -> deposit -> direct snapshot_ecs -> close -> reopen -> count
       (deposit + direct durability call, bypassing sleep_pass)

Reports survivor count after re-open for each pattern.
"""

from __future__ import annotations

import json
import math
import random
import shutil
from pathlib import Path

from moneta import Moneta, MonetaConfig

DIM = 384
PROBE_ROOT = Path("./.moneta_probe/durability")


def random_unit_vector(dim: int, rng: random.Random) -> list[float]:
    raw = [rng.gauss(0, 1) for _ in range(dim)]
    norm = math.sqrt(sum(x * x for x in raw))
    return [x / norm for x in raw]


def fresh_storage(label: str) -> Path:
    d = PROBE_ROOT / label
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    return d


def make_config(storage: Path, label: str) -> MonetaConfig:
    return MonetaConfig(
        storage_uri=f"moneta-durprobe://{label}",
        snapshot_path=storage / "snapshot.json",
        wal_path=storage / "wal.jsonl",
        mock_target_log_path=storage / "usd_authorings.jsonl",
        embedding_dim=DIM,
    )


def survivor_count(config: MonetaConfig) -> int:
    with Moneta(config) as m:
        return m.ecs.n


def pattern_a_deposit_only(rng: random.Random) -> int:
    storage = fresh_storage("A_deposit_only")
    config = make_config(storage, "A")
    with Moneta(config) as m:
        m.deposit("a-payload", random_unit_vector(DIM, rng))
    return survivor_count(config)


def pattern_b_deposit_then_sleep(rng: random.Random) -> int:
    storage = fresh_storage("B_deposit_sleep")
    config = make_config(storage, "B")
    with Moneta(config) as m:
        m.deposit("b-payload", random_unit_vector(DIM, rng))
        m.run_sleep_pass()
    return survivor_count(config)


def pattern_c_deposit_then_snapshot(rng: random.Random) -> int:
    storage = fresh_storage("C_deposit_snapshot")
    config = make_config(storage, "C")
    with Moneta(config) as m:
        m.deposit("c-payload", random_unit_vector(DIM, rng))
        m.durability.snapshot_ecs(m.ecs)
    return survivor_count(config)


def main() -> None:
    rng = random.Random(0xDEADBEEF)
    PROBE_ROOT.mkdir(parents=True, exist_ok=True)
    results = {
        "A_deposit_only": pattern_a_deposit_only(rng),
        "B_deposit_then_sleep_pass": pattern_b_deposit_then_sleep(rng),
        "C_deposit_then_direct_snapshot": pattern_c_deposit_then_snapshot(rng),
    }
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
