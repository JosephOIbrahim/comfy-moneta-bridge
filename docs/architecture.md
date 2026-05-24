# Architecture — comfy-moneta-bridge

Source-of-truth for design decisions. This document complements the
README's quick-tour by going deeper on the WHY of each module.

## Architectural source

This v0 architecture was adopted from a 2026-04-29 Gemini Deep Think
review and amended by the Phase 0.5b durability finding. The full chain:

| Iteration | Decision |
| --- | --- |
| `BRIDGE_BUILD_MISSION.md` (v1) | First spec — assumed Moneta exposed `ingest_event(dict)` and `read_session_state(name)`. Phase 0.5a scout proved otherwise. |
| `BRIDGE_BUILD_MISSION_v3.md` | Pivoted to deterministic synthetic vectors against Moneta's four-op handle. Phase 0.5b probe found that bare `deposit()` is not durable across handle close. |
| `BRIDGE_BUILD_MISSION_v3_1.md` | Adds Hard Rule §12 (`run_sleep_pass()` mandatory) and books the resulting performance ceiling as a v1 task. |
| `BRIDGE_BUILD_MISSION_v3_2.md` (current) | Unlocks the agent workflow surface explicitly listed as out-of-scope in v3.1. Adds Hard Rules §13–16 (tail/orchestrate mutex, validate-before-submit, localhost-default, planner-checkpoint-before-executor). Clarifies §7 to permit LLM SDKs in the optional `agents/` subpackage only. |

The v3 -> v3.1 amendment is empirically grounded. See
`scripts/probe_durability.py`:

```
A_deposit_only:                        0 entries survive close+reopen
B_deposit_then_sleep_pass:             1
C_deposit_then_direct_snapshot:        1
```

`Moneta.deposit()` writes only to in-memory ECS + `VectorIndex`.
`Moneta.close()` flushes the WAL fp and stops the bg thread but does
not snapshot. Persistence is the explicit responsibility of either
`run_sleep_pass()` (public) or `durability.snapshot_ecs()` (private).
v0 uses `run_sleep_pass()`; bridge code never reaches into private
attributes.

## Hard Rules and their reasons

| # | Rule | Why |
|---|---|---|
| 1 | No push to origin without per-call approval | Joe's Git Authority Map tier 3 |
| 2 | No modifications to Moneta source | Frozen by mission scope |
| 3 | No modifications to Comfy-Cozy source | Frozen by mission scope |
| 4 | utf-8 explicit on every file I/O | Windows default `cp1252` would silently corrupt emoji in `vision_notes` |
| 5 | Open-read-close pattern in `tail.py` | Persistent Windows read locks block Comfy-Cozy's `os.replace` during rotation |
| 6 | Ephemeral Moneta handles only | `MonetaResourceLockedError` makes long-lived handles unsafe when `bridge tail` and `bridge hydrate` may run concurrently |
| 7 | No ML / embedding library | v0 demo arc is same-session; semantic similarity isn't the operation needed |
| 8 | Tests pass before commit | Net-positive verification per Commandment §2 |
| 9 | Reconnaissance before building | Phase 0.5a/0.5b before Phase 1 |
| 10 | Atomic commits with clear provenance | One concept per commit |
| 11 | STOP at gates | Per Commandment §8 — gates only at irreversible transitions in the autonomous build harness |
| 12 | Persistence requires `run_sleep_pass()` | Empirically verified; without it every deposit is lost (see above) |
| 13 | `bridge tail` and `bridge orchestrate`/`bridge mcp` are mutually exclusive | PID-file mutex serializes them; they share Moneta URI lock |
| 14 | `workflow_submit` validates against `/object_info` first | Local catch of bad node types / missing required inputs |
| 15 | ComfyUI defaults to localhost; remote requires `BRIDGE_ALLOW_REMOTE_COMFY=1` | Prevents accidental execution against production ComfyUI |
| 16 | EXECUTOR refuses without a fresh PLANNER/MUTATOR checkpoint | Crash-resume must be distinguishable from fresh-start corruption |

## Why deposit alone doesn't persist

Moneta's `Moneta` class is a single-process resource handle:

- Constructor: acquires the URI lock in `_ACTIVE_URIS`, hydrates
  `ECS` from `snapshot.json` + `wal.jsonl` if both paths are
  configured. Constructs `VectorIndex` (in-memory) and the consolidation
  runner.
- `deposit(payload, embedding)`: appends to `ECS` row store + `VectorIndex`.
  Both are in-memory data structures.
- `signal_attention(weights)`: appends to attention log + WAL fp
  (durable per call).
- `run_sleep_pass()`: drains attention log, runs decay + classification,
  prunes / stages, and **calls `durability.snapshot_ecs(self.ecs)`**
  — the only path that writes the ECS to disk.
- `close()`: stops bg thread, closes WAL fp, releases URI lock. Does
  not snapshot.

The bg snapshot daemon (30 s cadence) is opt-in via `start_background()`
and not invoked automatically. So in an ephemeral-handle pattern, the
only on-disk persistence path is `run_sleep_pass()` (or the equivalent
private `durability.snapshot_ecs()` that the bridge does not call).

## Phase 0.5b probe findings

### Vector dimensionality

Moneta's `VectorIndex.upsert` (`src/moneta/vector_index.py:108-113`):

```python
if self._dim is None:
    self._dim = len(vector)
elif len(vector) != self._dim:
    raise ValueError(...)
```

`MonetaConfig.embedding_dim: Optional[int] = None`. Variable-dim:
any positive integer is accepted at first deposit and locked
thereafter. Probe ran with sizes 64, 128, 256, 384, 512, 768, 1024,
1536, 3072 — all accepted.

**Decision**: `comfy_moneta_bridge.vector.DIMENSION = 384`. BGE-small
standard size; plenty of room for PRNG independence between session
strings. Pinned via `MonetaConfig.embedding_dim=384` so dim mismatch
surfaces loudly if it ever happens.

### Ephemeral handle benchmark

`scripts/benchmark_handle.py`:

```
DIM=384  (ND=non-durable / D=durable, ms)

  WAL     N   ND_mean   ND_p50   ND_p95   ND_p99    D_mean    D_p50    D_p95    D_p99
    0   100      0.15     0.14     0.19     0.31     28.40    28.58    44.07    47.42
  100   100     11.59    10.46    14.98    21.78     75.81    70.38   103.37   109.38
 1000    50    144.36   155.82   178.77   181.41    684.95   692.63   720.47   736.36
10000    20   1472.04  1544.03  1751.51  1751.51   4518.06  4155.26  6504.95  6504.95
```

The non-durable column is hydration cost alone (no `run_sleep_pass`);
the gap to the durable column is the snapshot write. Both grow roughly
linearly with ECS size — JSON round-trip per row, no log-time index.

v0 demo workloads (≤100 outcomes per session) sit comfortably in the
green zone (~76 ms mean per ingest). Beyond ~1000 outcomes per session,
the per-ingest cost crosses the 500 ms threshold the mission set as a
STOP signal. v1 candidate: a batched-deposit layer that flushes every
N outcomes or every T seconds, amortizing the snapshot cost.

## Module-by-module notes

### `vector.py` — deterministic synthetic embedder

Pure function. `synthesize_vector(session)` returns the same 384-d
unit vector for the same session string, every time, with no hidden
state. Different sessions produce orthogonal-ish vectors (cosine
similarity well under 0.5 in practice).

Stdlib only: `hashlib`, `random`, `math`. The seed is the first 8
bytes of `sha256(session.encode("utf-8"))`, big-endian. CPython's
`random.Random(seed)` is documented stable across versions for a
given seed; same seed -> same gauss sequence -> same vector.

### `state.py` — `CursorStore`

The bridge owns one piece of persistent state: a per-watched-file
cursor `(inode, offset)`. Written atomically: temp file in the cursor
file's directory, fsync, `os.replace`. `os.replace` is atomic on
Windows since CPython 3.3.

Self-healing reads: malformed cursor file -> empty store. Worst case
on cursor loss: at most one outcome line of replay (Moneta's idempotency
isn't bridge-side, so the v0 limitation note covers this).

### `tail.py` — rotation-aware tailer

Open-read-close pattern. **Binary mode + explicit utf-8 decode** rather
than text-mode seek/tell — text-mode `tell()` returns opaque tokens by
spec, and seeking mid-codepoint with multi-byte utf-8 raises mid-read.
Binary mode keeps cursor arithmetic unambiguous; the explicit decode
honors Hard Rule §4's intent ("no cp1252 silent corruption").

Rotation detection: any of (a) `Change.deleted` event, (b) live file
size shrunk below `last_offset`, (c) inode mismatch on the live file.
Rotation handler drains stranded lines from `{path}.1` before resetting
state for the new file.

Cursor advances **end-of-batch** within a single `_handle_change`. Per
Hard Rule §12, every line in the batch was already durable (its ingest
call ran `run_sleep_pass`), so a clean batch return means cursor is
safe to advance. Mid-batch failure -> next restart re-reads from the
pre-batch cursor; some duplicates within the batch span. Documented v0
limitation.

### `ingest.py` — deposit + run_sleep_pass

The pipeline. `ingest_batch(outcomes, path)` validates+embeds each
outcome (dropping `schema_version != 1`), opens one ephemeral handle,
deposits all, calls `m.run_sleep_pass()` **once**, exits the with-block.
The function returns only after `run_sleep_pass` has completed — clean
return == on-disk durability. `ingest_outcome` is a thin single-line
wrapper over `ingest_batch([outcome])`.

`MonetaConfig.embedding_dim` is pinned to 384 so dim mismatch surfaces
at deposit time as a `ValueError` rather than silently corrupting the
vector index.

### Batched-deposit layer (v0.3 — realizes the v1 candidate)

`run_sleep_pass()` snapshots the entire ECS to disk and is the
per-deposit cost ceiling (benchmark above: 28 ms @0 → 685 ms @1000 →
4518 ms @10000). Two batching tiers reduce how often it runs:

- **Per-drain coalescing (always on).** `tail._handle_change` deposits
  a whole drained batch via one `ingest_batch` call — one snapshot for
  the batch instead of one per line. The cursor still advances only at
  end-of-drain, so the crash-replay window is **unchanged** from v0.
  This is a strict, contract-preserving win for multi-line drains
  (rotation catch-up, cold-start backlog).

- **Cross-event buffering (opt-in).** `--batch-size N` / `--batch-max-delay T`
  (Tailer `batch_size` / `batch_max_delay_s`) accumulate outcomes
  across watch events and flush when `count >= N` **or** the oldest
  buffered outcome is older than `T` seconds, or on shutdown. This
  amortizes the snapshot across a sustained single-line append stream
  (the case per-drain coalescing can't help).

  **Durability tradeoff (why it is opt-in, default off):** buffered
  mode keeps an in-memory `_live_state` (inode + read offset) seeded
  from the persisted cursor, and the cursor is persisted only on flush.
  So the crash-replay/duplicate window grows from one watch event to
  one flush interval (≤ N outcomes or ≤ T seconds). No data is lost —
  a crash re-reads from the last *flushed* offset and re-deposits the
  un-flushed lines (Moneta owns idempotency) — but the duplicate span
  is wider. The default (`N=1, T=0`) flushes every event and is
  byte-identical to v0. Pair `N>1` with a small `T` to bound how long
  an outcome can sit non-durable. Hard Rule §6 (ephemeral handles) is
  preserved: buffering holds raw outcomes in memory, not an open
  Moneta handle — the handle is still opened only at flush.

### `capsule.py` — query -> schema_v2

Read path. No `run_sleep_pass` needed (query is read-only). Filters
results by `payload["session"]` (PRNG-collision guard against the
small but non-zero chance that a different session's vector is
cosine-near-1). Sort chronologically. Translate to Comfy-Cozy
`schema_version=2` (`G:/Comfy-Cozy/agent/memory/session.py`).

### `launch.py` — `subprocess.Popen(["agent", mode], env=...)`

Verifies the capsule exists pre-spawn so a missing capsule fails
locally rather than as a Comfy-Cozy startup error.

### `cli.py` — typer

Five commands: `bridge tail` (run forever), `bridge hydrate <name>`
(one-shot), `bridge recall <query>` (one-shot), `bridge orchestrate
<goal>` (v0.2, opt-in), `bridge mcp` (v0.2, opt-in). The hydrate
command always prints the hot-hydrate warning ("must be (re)started
... running instances will not auto-load"). `--launch` spawns
Comfy-Cozy in addition. The tail command writes
`{state_dir}/tail.pid` via `PidFileGuard` and refuses to start if
`{state_dir}/orchestrate.pid` is present — the §13 mutex.

### `agents/` — v0.2 workflow manipulation surface

Opt-in subpackage gated by the `[agents]` extra. See
`BRIDGE_BUILD_MISSION_v3_2.md` for the formal scope amendment and
`AGENTS.md` for the runtime constitution. The subpackage adds:

- `client.py` — async ComfyUI HTTP+WS client. Defaults to
  `http://127.0.0.1:8188`; non-localhost refused without explicit
  opt-in (§15). `/object_info` is cached per-client with explicit
  `refresh_schema()` invalidation.
- `workflow.py` — typed `Workflow` dataclass wrapping ComfyUI's
  API-format JSON. Round-trip preserves node ordering, `_meta`,
  and top-level extras like `_comment`. Mutation primitives:
  `add_node`, `set_input`, `connect`, `remove_node`. `validate()`
  catches unknown class_types, missing required inputs, and
  dangling connection sources.
- `tools.py` — single source of truth for the tool surface. One
  `ToolSpec` list yields both Anthropic schemas (`as_anthropic_tools`)
  and MCP `Tool` registrations (`register_with_mcp`). Both surfaces
  dispatch through `dispatch(name, args, ctx)` so refusal hooks live
  in one place.
- `constitution.py` — loads `AGENTS.md` per orchestration (no
  module-level cache so operators can edit live).
- `roles.py` — five-role split with deterministic turn-taking.
- `harness.py` — `CheckpointStore` (atomic temp+fsync+os.replace,
  same pattern as `state.CursorStore`), `PidFileGuard` context
  manager, and §13/§16 enforcement.
- `orchestrator.py` — drives one goal through the role machine.
  Owns the Moneta handle implicitly (safe because §13 excludes
  Tailer concurrency). Final outcomes flow through `ingest_outcome`
  so the existing durability discipline is preserved.
- `mcp_server.py` — stdio MCP server wrapping `ALL_TOOLS`.
  `BRIDGE_MCP_ROLE` env locks the session into one role's allowlist.
- `loop.py` — `ClaudeRoleDriver` implements the `RoleDriver`
  protocol against the Anthropic Messages API with prompt-caching
  on the (long, stable) constitution+role-charter system prompt.

Workflows carried forward through `bridge hydrate`: agents emit a
`_kind=workflow_snapshot` deposit through the normal ingest path;
`write_capsule` scans queried memories for the latest snapshot per
session and populates the capsule's workflow block from it. When no
snapshot exists, the block remains byte-equal to the pre-v0.2
`_empty_workflow_block()` (regression-canaried by
`tests/test_capsule.py::test_schema_v2_workflow_stub`).

## Considered and rejected

See `BRIDGE_BUILD_MISSION_v3_1.md` §"Considered and Rejected".

The two amendments worth re-reading every quarter:
- **Bridge-side dedupe set**. Rejected because Moneta has its own
  notion of "what exists" via `VectorIndex` upsert keyed by
  `entity_id`. The bridge owning a separate dedupe key would compete
  for source-of-truth.
- **Long-lived Moneta handle + bg snapshot**. Rejected because it
  conflicts with Hard Rule §6 (`MonetaResourceLockedError` would
  poison `bridge hydrate` whenever `bridge tail` is up). v1 batched-
  deposit layer is the right shape for that.
