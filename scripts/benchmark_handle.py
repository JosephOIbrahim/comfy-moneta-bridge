"""Phase 0.5b benchmark: measure ephemeral Moneta handle overhead.

Usage (Moneta on PYTHONPATH):
    PYTHONPATH=C:/Users/User/Moneta/src python scripts/benchmark_handle.py

For each WAL pre-population size in [0, 100, 1000, 10000] and each cycle
pattern, run ``N_CYCLES`` cycles and report mean / p50 / p95 / p99 in
milliseconds.

Cycle patterns:
    NON_DURABLE:  open -> deposit -> close.
                  Mission v3 spec as written. Deposits are LOST on close
                  (probe_durability.py confirmed). Reported for reference.
    DURABLE:      open -> deposit -> run_sleep_pass -> close.
                  Includes the sleep pass that snapshots the ECS to disk.
                  This is what the bridge actually needs to call to make
                  deposits survive handle close.

Population uses Pattern B (deposit + sleep_pass) so the snapshot on
disk actually contains ``wal_size`` entries when the cycles begin.

Acceptance criteria (per BRIDGE_BUILD_MISSION_v3.md Phase 0.5b):
    DURABLE mean per-cycle < 100ms   -> ship-as-is
    100-500ms                        -> ship-with-v1-task (batching)
    > 500ms                          -> STOP, reconsider architecture
"""

from __future__ import annotations

import json
import math
import random
import shutil
import statistics
import time
from pathlib import Path

from moneta import Moneta, MonetaConfig

DIM = 384

# Tiered cycle counts: heavier WAL sizes do fewer cycles so the run
# completes in a reasonable wall time. Each (wal_size, n_cycles) pair
# is independent.
WAL_PLAN: list[tuple[int, int]] = [
    (0, 100),
    (100, 100),
    (1_000, 50),
    (10_000, 20),
]

BENCH_ROOT = Path("./.moneta_probe/benchmark")


def random_unit_vector(dim: int, rng: random.Random) -> list[float]:
    raw = [rng.gauss(0, 1) for _ in range(dim)]
    norm = math.sqrt(sum(x * x for x in raw))
    return [x / norm for x in raw]


def fresh_storage(label: str) -> Path:
    d = BENCH_ROOT / label
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    return d


def make_config(storage: Path, label: str) -> MonetaConfig:
    return MonetaConfig(
        storage_uri=f"moneta-bench://{label}",
        snapshot_path=storage / "snapshot.json",
        wal_path=storage / "wal.jsonl",
        mock_target_log_path=storage / "usd_authorings.jsonl",
        embedding_dim=DIM,
    )


def populate(config: MonetaConfig, n: int, rng: random.Random) -> None:
    """Populate the ECS to ``n`` entries and snapshot it to disk."""
    if n == 0:
        return
    with Moneta(config) as m:
        for i in range(n):
            m.deposit(
                payload=f"populate-{i}",
                embedding=random_unit_vector(DIM, rng),
            )
        # Force a snapshot via run_sleep_pass so the cycles below actually
        # exercise hydration of `n` entries from disk.
        m.run_sleep_pass()


def percentiles(times_ms: list[float]) -> dict:
    times_sorted = sorted(times_ms)
    n = len(times_sorted)
    return {
        "n": n,
        "total_ms": round(sum(times_ms), 2),
        "mean_ms": round(statistics.mean(times_ms), 3),
        "p50_ms": round(times_sorted[n // 2], 3),
        "p95_ms": round(times_sorted[min(n - 1, int(n * 0.95))], 3),
        "p99_ms": round(times_sorted[min(n - 1, int(n * 0.99))], 3),
    }


def benchmark(wal_size: int, n_cycles: int, rng: random.Random) -> dict:
    label = f"wal_{wal_size}"
    storage = fresh_storage(label)
    config = make_config(storage, label)

    print(f"  populate wal_size={wal_size} ...", flush=True)
    populate(config, wal_size, rng)

    print(f"  non-durable cycles n={n_cycles} ...", flush=True)
    non_durable_ms: list[float] = []
    for i in range(n_cycles):
        t0 = time.perf_counter()
        with Moneta(config) as m:
            m.deposit(
                payload=f"non-durable-{i}",
                embedding=random_unit_vector(DIM, rng),
            )
        non_durable_ms.append((time.perf_counter() - t0) * 1000.0)

    print(f"  durable cycles n={n_cycles} ...", flush=True)
    durable_ms: list[float] = []
    for i in range(n_cycles):
        t0 = time.perf_counter()
        with Moneta(config) as m:
            m.deposit(
                payload=f"durable-{i}",
                embedding=random_unit_vector(DIM, rng),
            )
            m.run_sleep_pass()
        durable_ms.append((time.perf_counter() - t0) * 1000.0)

    return {
        "wal_size": wal_size,
        "n_cycles": n_cycles,
        "non_durable": percentiles(non_durable_ms),
        "durable": percentiles(durable_ms),
    }


def disposition(rows: list[dict]) -> str:
    worst_durable_mean = max(r["durable"]["mean_ms"] for r in rows)
    if worst_durable_mean < 100:
        return "ship-as-is"
    if worst_durable_mean < 500:
        return "ship-with-v1-task"
    return "STOP"


def main() -> None:
    rng = random.Random(0xBADD00D5)
    if BENCH_ROOT.exists():
        shutil.rmtree(BENCH_ROOT)
    BENCH_ROOT.mkdir(parents=True, exist_ok=True)
    print(f"DIM={DIM} WAL_PLAN={WAL_PLAN}", flush=True)
    header = (
        f"{'WAL':>8} {'N':>5} "
        f"{'ND_mean':>9} {'ND_p50':>8} {'ND_p95':>8} {'ND_p99':>8}  "
        f"{'D_mean':>9} {'D_p50':>8} {'D_p95':>8} {'D_p99':>8}"
    )
    print(header, flush=True)
    rows: list[dict] = []
    for wal_size, n_cycles in WAL_PLAN:
        r = benchmark(wal_size, n_cycles, rng)
        rows.append(r)
        nd = r["non_durable"]
        d = r["durable"]
        print(
            f"{r['wal_size']:>8} {r['n_cycles']:>5} "
            f"{nd['mean_ms']:>9.2f} {nd['p50_ms']:>8.2f} {nd['p95_ms']:>8.2f} {nd['p99_ms']:>8.2f}  "
            f"{d['mean_ms']:>9.2f} {d['p50_ms']:>8.2f} {d['p95_ms']:>8.2f} {d['p99_ms']:>8.2f}",
            flush=True,
        )
    print(json.dumps({"summary": True, "rows": rows, "disposition": disposition(rows)}), flush=True)


if __name__ == "__main__":
    main()
