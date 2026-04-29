# MONETA API SCOUT (pre-flight)

**Status (0.5a, 2026-04-29):** STOP — confidence LOW. Surfaced to Joe; resolved by mission v3, which adopts Gemini's pivot to deterministic synthetic embeddings against the four-op handle.

**Status (0.5b, 2026-04-29 PM):** STOP per strict criterion. New finding: bare deposit is not durable across handle close. See "Addendum — Phase 0.5b" at the bottom of this file.

**Scout date:** 2026-04-29
**Scouted source:** `C:\Users\User\Moneta\` at tag `v1.2.0-rc2` (commit `8651b35d`, branch `main` HEAD `76da067`)

---

## Tag verified

- Requested:  `v1.2.0-rc2`
- Actual:     `v1.2.0-rc2` exists. Tagger: Joseph Ibrahim, 2026-04-28. Subject: "v1.2.0-rc2: Free-threading guard". Tagged commit: `76da067` "Add free-threading guard to AttentionLog".
- Latest tags newest-first: `v1.2.0-rc2`, `v1.2.0-rc1`, `v1.0.0`.

Tag verification: PASS.

---

## Ingest surface

- Module:    **NOT FOUND**
- Function:  **NOT FOUND**
- Accepts:   N/A — no function in `src/moneta/` accepts an experience-event-shaped dict.

### What does exist

`Moneta` (the substrate handle, `src/moneta/api.py`) exposes a four-operation agent surface plus harness operators. The four operations:

1. `Moneta.deposit(payload: str, embedding: List[float], protected_floor: float = 0.0) -> UUID`
2. `Moneta.query(embedding: List[float], limit: int = 5) -> List[Memory]`
3. `Moneta.signal_attention(weights: Dict[UUID, float]) -> None`
4. `Moneta.get_consolidation_manifest() -> List[Memory]`

Plus `Moneta.run_sleep_pass()` (harness-level, `ConsolidationResult`) and `Moneta.close()` / `__enter__`/`__exit__`.

### The gap

The bridge mission (BRIDGE_BUILD_MISSION.md "Pre-Flight" §) asks for:

> An **ingest** entry point — accepts a single experience event and persists it.

Moneta has no such entrypoint. The closest mechanic is `deposit(payload, embedding, ...)`, which would require the bridge to:

- Invent a `payload: str` summary from the 12-field outcome record.
- Synthesize an `embedding: List[float]` for every line.

Synthesizing embeddings implies an embedder, which would need either an Anthropic SDK dependency or a local model. **The mission's "Out of scope" list explicitly excludes** "Anthropic SDK dependency in the bridge". No alternative embedder is specified. Without an embedder the deposit call has no valid `embedding` argument.

A grep confirms the absence: no `def ingest`, `def record_outcome`, `def persist_event`, `outcomes.jsonl`, `workflow_hash`, `key_params`, or `session_name` references anywhere under `src/moneta/`.

---

## Read / hydrate surface

- Module:    **NOT FOUND** (no session-keyed read)
- Function:  **NOT FOUND**
- Returns:   N/A

### What does exist

- `Moneta.query(embedding, limit)` — returns top-k `Memory` objects ranked by `cosine_similarity * utility`. Takes an embedding, not a session name.
- `Moneta.get_consolidation_manifest()` — returns `STAGED_FOR_SYNC` entities only.
- `durability.DurabilityManager.hydrate() -> Tuple[ECS, List[AttentionEntry]]` — internal-only. Restores the entire ECS from snapshot + WAL replay at handle construction. Not parameterized by session name and not in the public re-export from `moneta/__init__.py`.

### The gap

The bridge mission asks for:

> A **read** entry point — given a session name, returns the state needed to populate `sessions/{name}.json`.

Moneta has no concept of "session name" anywhere in `src/moneta/`. Storage identity is `MonetaConfig.storage_uri`, which is a logical handle URI, not a session label. The four-op API has no session-keyed view. There is no Phase 3 spec in `MONETA.md` for session hydration; in fact `SURGERY_complete_codeless_schema.md` carries forward "Cross-session USD hydration" as a "Phase 1 non-goal" — explicitly parked.

The Comfy-Cozy capsule schema (`schema_version=2`) requires fields like `workflow.base_workflow`, `workflow.current_workflow`, `notes[]`, and `metadata` — none of which Moneta tracks per-session. Moneta tracks `Memory` rows (payload + embedding + utility + state), which is a different abstraction.

---

## Other concerns (informational, not gating)

These are noted so the eventual fix-up brief sees them, not as additional blockers.

1. **Stale doc.** `Moneta/docs/api.md` documents a singleton-era API (`moneta.init()`, module-level `deposit`/`query`). The actual `src/moneta/api.py` and `__init__.py` at v1.2.0-rc2 are the post-substrate-handle surgery (instance methods on `Moneta`). The doc is wrong; the code is right. The bridge would import and use the handle pattern, not the doc-described singleton.
2. **Process exclusivity.** `Moneta` uses an in-memory `_ACTIVE_URIS` registry (`api.py:169`). Two live handles on the same `storage_uri` raise `MonetaResourceLockedError`. If the bridge holds an open handle for ingest while a Comfy-Cozy process or `bridge hydrate` invocation also constructs against the same URI, the second caller will fail synchronously. The mission spec does not address this — `bridge tail` is a long-running daemon and `bridge hydrate` is on-demand, so they can collide.
3. **No fire-and-forget durability without the daemon.** Moneta only persists if `MonetaConfig` is constructed with `snapshot_path` AND `wal_path`. With both unset, the handle is in-memory only and dies with the process. The bridge mission does not specify what configuration `bridge tail` constructs Moneta with, so even if `deposit` were the right call, the durability semantics are unspecified.
4. **No `EntityState.PRUNED` enum member** but the codeless schema reserves the `"pruned"` token. Not relevant to the bridge but worth knowing if the bridge ever needs to filter by state.

---

## Confidence

**LOW.**

**Reasoning:** The two API surfaces the mission's pre-flight requires are not present in Moneta `v1.2.0-rc2`. The bridge cannot adapt outcomes onto `deposit(payload, embedding, ...)` without inventing an embedder, which is explicitly out of scope; and it cannot satisfy `write_capsule(session_name)` because Moneta has no session-keyed read. The `docs/api.md` reference is stale and would mislead a less careful reader, but even the stale doc never described an experience-event ingest or session-name-keyed read.

---

## STOP conditions encountered

1. **Ingest API absent.** No function in Moneta `v1.2.0-rc2` accepts a Comfy-Cozy outcome record (the 12-field `schema_version=1` line shape from `*_outcomes.jsonl`) and persists it. The four-op `deposit` takes `(payload, embedding, protected_floor)` — fundamentally a different shape, and would require the bridge to synthesize embeddings, which is excluded by the mission's "Out of scope" list.

2. **Read/hydrate API absent.** No function takes a session name and returns the state needed to populate `sessions/{name}.json` per Comfy-Cozy `schema_version=2`. Moneta has no session-name dimension; it tracks `Memory` rows by `entity_id` (UUID) addressed by `storage_uri`. Cross-session USD hydration is explicitly listed in `SURGERY_complete_codeless_schema.md` as a "Phase 1 non-goal".

3. **Cannot fold a verified tag into `pyproject.toml` and proceed.** Phase 0 step 3 wants `moneta @ git+https://github.com/JosephOIbrahim/Moneta@{verified_tag}`. The tag verifies, but installing the package would not satisfy any of the bridge's planned imports because the planned imports do not exist on the package surface.

---

## Surfaced question (for Joe)

The bridge mission was written against an assumed Moneta API surface that is not present at `v1.2.0-rc2`. Possible resolutions, in increasing scope:

| # | Path | What it costs |
|---|---|---|
| 1 | **Spec correction.** The bridge wraps Moneta's four-op API directly — `tail` calls `deposit(payload=line["workflow_summary"], embedding=<???>, ...)`, `capsule` calls `query(...)` with some fixed embedding to dump top-k memories. | Cheap on bridge side, but requires either an embedding strategy (zero vector? hash-derived? tiny local model?) or relaxing the "no Anthropic SDK" exclusion. Also does not map cleanly to `sessions/{name}.json` schema_version=2. |
| 2 | **Add a Moneta adapter package.** `moneta.bridge_adapter` (or similar) exposes `ingest_outcome(line: dict, *, moneta: Moneta) -> None` and `read_session_state(name: str, *, moneta: Moneta) -> dict`. The bridge depends on this adapter; the adapter encapsulates payload-string composition, an embedder choice, and the session-name mapping. | New surgery on Moneta. Violates the bridge mission's Hard Rule §2 ("No modifications to Moneta source"), so this would be a separate Moneta surgery first, then this bridge mission resumes against the new tag. |
| 3 | **Different Moneta tag.** If a later Moneta tag (e.g. `v1.3.0-rc1`) is planned to ship `ingest`/`read_session_state` as a public surface, point the bridge at that tag instead. | Need to know whether such a tag exists or is planned. None is currently in `git tag -l`. |

Pre-flight halt. Awaiting direction. No commits made.

---

## Addendum — Phase 0.5b (2026-04-29 PM)

Run after mission v3 was issued. v3 resolves the 0.5a gap by having the bridge wrap the four-op handle directly with synthetic vectors. This addendum is the dimensionality + handle-overhead probe required by v3 §"Phase 0.5b" before Phase 1.

**Probe environment.**
- Moneta source: `C:\Users\User\Moneta\src\moneta\` at branch `main` HEAD `76da067` (= tag `v1.2.0-rc2`).
- Python: 3.14.2, GIL enabled (`sys._is_gil_enabled()` returned True; passes Moneta's free-threading guard).
- Storage URIs distinct per probe so `_ACTIVE_URIS` lock and `_dim` lock don't carry across.
- Imports via `PYTHONPATH=C:/Users/User/Moneta/src` (no pip install needed for the probe).

### Check 1 — Vector dimensionality

Probe script: `scripts/probe_dimensionality.py`. Tested 9 candidate sizes: 64, 128, 256, 384, 512, 768, 1024, 1536, 3072.

```
{"dim": 64,   "ok": true, "query_returned": 1}
{"dim": 128,  "ok": true, "query_returned": 1}
{"dim": 256,  "ok": true, "query_returned": 1}
{"dim": 384,  "ok": true, "query_returned": 1}
{"dim": 512,  "ok": true, "query_returned": 1}
{"dim": 768,  "ok": true, "query_returned": 1}
{"dim": 1024, "ok": true, "query_returned": 1}
{"dim": 1536, "ok": true, "query_returned": 1}
{"dim": 3072, "ok": true, "query_returned": 1}
```

All sizes accepted. The mechanism (per `src/moneta/vector_index.py:108-113` `VectorIndex.upsert`):

```python
if self._dim is None:
    self._dim = len(vector)
elif len(vector) != self._dim:
    raise ValueError(f"embedding dim mismatch: expected {self._dim}, got {len(vector)}")
```

Moneta is **variable-dim**: any positive integer is accepted at first deposit and locked thereafter for that handle's lifetime. `MonetaConfig.embedding_dim: Optional[int] = None` lets callers pre-declare a dim; if left None, Moneta infers from first deposit.

**Decision:** `DIMENSION = 384`. Per mission v3 disposition rule for the variable-dim case (BGE-small standard, lightweight, plenty of room for the synthetic vector's PRNG independence). This will be the value of `comfy_moneta_bridge.vector.DIMENSION` and the `MonetaConfig.embedding_dim` argument the bridge passes when constructing handles.

### Check 2 — Ephemeral handle overhead benchmark

Probe scripts: `scripts/probe_durability.py` (durability verification) and `scripts/benchmark_handle.py` (timing).

#### Critical sub-finding: bare deposit is NOT durable

The mission v3 Phase 4 ingest spec (§"Behavior contract") writes:

```python
with Moneta(...) as m:
    m.deposit(payload=payload, embedding=embedding, ...)
# exit context manager (closes handle, releases process lock)
```

This pattern **loses every deposit** on close. Verified empirically:

```json
{
  "A_deposit_only":              0,
  "B_deposit_then_sleep_pass":   1,
  "C_deposit_then_direct_snapshot": 1
}
```

(Each pattern: open, deposit one entry, do the corresponding finalization, close, re-open in a fresh handle, count `m.ecs.n`.)

Why: `Moneta.deposit` writes to the in-memory ECS + in-memory `VectorIndex` + `consolidation.mark_activity`. **None of those touch disk.** The `DurabilityManager` only fsyncs on `signal_attention` (WAL append) or `run_sleep_pass` (snapshot via `snapshot_ecs`). `Moneta.close()` flushes the WAL fp and stops the bg thread, but does not snapshot. The bg snapshot daemon (30s cadence) is opt-in via `start_background()` and the handle does not start it automatically.

So the bridge's "ephemeral handle per deposit" pattern requires a finalization call inside the with-block. Two viable shapes:

- **Pattern B (public API only):** `m.run_sleep_pass()` after each `m.deposit(...)`. Snapshots ECS + runs consolidation. The reference shape for the bridge.
- **Pattern C (private API):** `m.durability.snapshot_ecs(m.ecs)`. Same disk cost, skips the consolidation logic. Reaches into `m.durability` which is not part of the four-op surface — borderline, would need to be revisited if Moneta locks down attributes.

**Mission v3 Phase 4 spec needs amendment.** The current `with Moneta(...) as m: m.deposit(...)` block must include `m.run_sleep_pass()` (or equivalent) before exit, or every deposit is silently lost. This is an architectural correction, not an optimization.

#### Timing

Cycles per (WAL pre-population, mode):
- **ND** = non-durable — `with Moneta: m.deposit(...)`. Pattern A. *Lossy*; reported only as a baseline showing hydration cost alone.
- **D** = durable — `with Moneta: m.deposit(...); m.run_sleep_pass()`. Pattern B. The actual cost the bridge will pay.

WAL is pre-populated by `populate(n)` which runs `n` deposits + one `sleep_pass` so the on-disk snapshot actually contains `n` entries when cycles begin. Cycle counts are tiered (heavier WAL sizes do fewer cycles to keep total wall time bounded; each cycle independently measured).

```
DIM=384  cycles=tiered  (ND=non-durable / D=durable, all in ms)

  WAL     N   ND_mean   ND_p50   ND_p95   ND_p99    D_mean    D_p50    D_p95    D_p99
    0   100      0.15     0.14     0.19     0.31     28.40    28.58    44.07    47.42
  100   100     11.59    10.46    14.98    21.78     75.81    70.38   103.37   109.38
 1000    50    144.36   155.82   178.77   181.41    684.95   692.63   720.47   736.36
10000    20   1472.04  1544.03  1751.51  1751.51   4518.06  4155.26  6504.95  6504.95
```

The non-durable column is approximately the hydration cost — what `Moneta(config)` costs to construct when the snapshot already contains `n` entries. The durable-minus-non-durable gap is approximately the cost of `run_sleep_pass()`, dominated by writing the new snapshot file (one full ECS dump per call, atomic temp+fsync+rename via `DurabilityManager.snapshot_ecs`).

Both columns scale roughly linearly with WAL size — there's no log-time index hydration; each entity in the snapshot is round-tripped through JSON.

#### Disposition

**Strict disposition: STOP.** The mission's threshold is "mean per-cycle > 500ms → STOP", and the durable mean passes that at WAL≥1000 (685ms) and is 4500ms at WAL=10000.

**Demo-scale disposition: ship-with-v1-task.** The cold-vs-warm v0 demo arc generates ≤100 outcomes. At WAL≤100 the durable mean is 76ms — well inside the 100ms "ship-as-is" threshold. So a tightly-scoped v0 demo can ship; production-scale ingest cannot.

The v1 task is the deposit-batching layer Gemini already anticipated. Concrete shapes:

| Approach | Trade-off |
|---|---|
| **Periodic snapshot in `bridge tail`** — hold a long-lived handle, deposit per outcome, call `run_sleep_pass()` every N deposits or every T seconds. | Conflicts with v3 Hard Rule §6 ("Ephemeral Moneta handles only"). Window of unpersisted data is N or T. Frees up almost all of the durability-cycle cost. |
| **Stay ephemeral, snapshot every N** — bridge accumulates lines in memory, opens a handle, deposits the batch, runs one sleep_pass, closes. | Honors §6 between batches. Crash window is N. Throughput improves linearly with N. |
| **Direct durability snapshot (Pattern C)** — `m.durability.snapshot_ecs(m.ecs)` per deposit, skipping the consolidation cost. | Faster than `run_sleep_pass()` by the consolidation portion (small at v0 scale, larger as ECS grows). Reaches into a private attribute. |
| **Moneta-side change** — add `Moneta.flush()` or have `close()` snapshot. Or expose a "snapshot only" public method. | Forbidden by Hard Rule §2 from the bridge mission. Would be a separate Moneta surgery. |

### Phase 0.5b deliverable summary

- **Vector dimensionality:** any positive int accepted; **384 chosen.**
- **Benchmark numbers:** above table.
- **Disposition:** **STOP** by strict criterion; **ship-with-v1-task** is viable for the v0 demo arc IF the bridge's ingest with-block is amended to include a snapshot call (Pattern B). Surfaced for Joe to choose.

### STOP conditions encountered

1. **Architecture-affecting durability gap.** The mission v3 Phase 4 Behavior Contract as written would lose every deposit. Spec needs an amendment to include a snapshot call inside the with-block. This is not a "v1 task" — it's a v0 correctness requirement.

2. **Strict-threshold disposition.** Per the v3 acceptance criteria: durable mean > 500ms at WAL≥1000 → STOP, reconsider architecture. Resolution requires either (a) accepting the ship-with-v1-task interpretation for the v0 demo only, or (b) adopting one of the four batching approaches above before Phase 4 is implemented.

### Surfaced question (for Joe)

Disposition path? Three options, in order of escalation:

1. **Demo-only ship-with-v1-task.** Amend Phase 4 Behavior Contract to call `m.run_sleep_pass()` per deposit. Demo runs at <100ms/cycle. Annotate v1 candidate: a batching layer. Move on.
2. **Adopt batching now.** Pivot Phase 4 to one of the batching approaches (most likely: ephemeral handles, batch-of-N flush). Adds complexity to Phase 4 + needs a new failure-mode note in the README. Pushes the v1 candidate problem out further.
3. **Hold and request a Moneta-side change.** Smallest bridge code, biggest cross-project cost. Would push the bridge build out by a Moneta surgery cycle.

Halting at the Phase 0.5b gate. Scripts written and runnable; results reproducible. No commit yet — awaiting direction on which disposition to encode in the commit message.
