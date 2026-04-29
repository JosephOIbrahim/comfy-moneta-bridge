# BRIDGE BUILD MISSION v3.1 — `comfy-moneta-bridge` v0

**Role:** `[SCAFFOLD × FORGE]` resuming after Phase 0.5b
**Type:** Greenfield build, phase-gated, test-driven
**Repo:** `comfy-moneta-bridge` at `C:\Users\User\Comfy_Moneta_Bridge\`
**Architecture:** Adopted from Gemini Deep Think review (2026-04-29), amended with Phase 0.5b durability findings
**Supersedes:** `BRIDGE_BUILD_MISSION_v3.md` (replaced; v3 retained in repo for provenance only)

---

## What Changed From v3

Phase 0.5b ran (commit-pending in repo) and surfaced one architecture-affecting bug in v3's Phase 4 spec:

> **Moneta `deposit()` writes only to in-memory ECS + VectorIndex. `Moneta.close()` does not snapshot. Persistence requires a `run_sleep_pass()` call inside the `with`-block.**

Empirically verified (`scripts/probe_durability.py`, in tree, uncommitted):

| Pattern | Outcomes retrieved after handle close |
|---|---|
| `deposit()` only | **0** |
| `deposit()` + `run_sleep_pass()` | 1 |
| `deposit()` + direct `durability.snapshot_ecs()` | 1 |

A bridge built per v3's Phase 4 spec would lose every deposit. v3.1 fixes this and books the resulting performance ceiling as a v1 task.

The four amendments:

1. **New Hard Rule §12** — Persistence requires `run_sleep_pass()`
2. **Phase 4 contract** — adds the durability call + a test that proves persistence
3. **Phase 9 README** — documents the WAL=1000 performance ceiling
4. **Considered and Rejected** table — adds the three Path 2 batching variants that were rejected for v0

Phase 0.5a (`MONETA_API_SCOUT.md`) and Phase 0.5b (the addendum + three probe scripts) are **already in the repo tree, uncommitted**. The Phase 0 commit folds them in.

---

## Frame

Build the v0 bridge that wires Comfy-Cozy's experience output into Moneta's persistence layer. Both source repos are frozen as law: no modifications to either. The bridge is a third, public-facing Python package that consumes Comfy-Cozy's outputs by file watch and produces inputs by file write. No code-level coupling. No process injection.

Architecture: deterministic synthetic embeddings keyed off the session string, ephemeral Moneta handles per call, stdlib-only embedder, durable persistence via `run_sleep_pass()`.

This v3.1 is the corrected and durability-verified architecture. Build it. Do not re-litigate.

---

## Hard Rules — Read Before Every Phase

1. **No push to origin without explicit per-call approval.** Joe's Git Authority Map: Level 3 operations (push, reset, rebase) require per-call human approval. Commits and tags are session-authorized for the duration of this mission. Force-push and history rewrites are forbidden.

2. **No modifications to Moneta source.** `C:\Users\User\Moneta\` is read-only for this mission. If the bridge needs an API that doesn't exist in Moneta, STOP and report — do not patch Moneta.

3. **No modifications to Comfy-Cozy source.** `G:\Comfy-Cozy\` is read-only for this mission. If the bridge needs a contract change in Comfy-Cozy, STOP and report — do not patch Comfy-Cozy.

4. **utf-8 explicit on every file I/O.** Every `open()` call gets `encoding="utf-8"`. No exceptions. Windows default is `cp1252` and that will silently break on emoji or smart quotes in `vision_notes`.

5. **Open-read-close pattern in `tail.py`.** Never hold a persistent file handle to `*_outcomes.jsonl`. Windows holds a read lock that will block Comfy-Cozy's `os.replace` during rotation and crash the agent.

6. **Ephemeral Moneta handles only.** Never hold a `Moneta(...)` instance open across function calls. Open inside a `with` block, do the deposit (or query) plus any required durability calls, exit. Process exclusivity (`MonetaResourceLockedError`) makes any other pattern unsafe when `bridge tail` and `bridge hydrate` may run concurrently.

7. **No ML / embedding library in dependencies.** No `sentence-transformers`, no `torch`, no `transformers`, no remote embedding APIs. The synthetic embedder uses stdlib only (`hashlib`, `random`, `math`).

8. **Tests pass before commit.** Every module has tests. `pytest` clean before `git commit`. No `-x` skip flags.

9. **Reconnaissance before building.** Phase 0.5a and Phase 0.5b are complete. Findings live in `MONETA_API_SCOUT.md` (in tree, uncommitted). Phase 0 commits them.

10. **Atomic commits with clear provenance.** One concept per commit. Commit message format below.

11. **STOP at gates.** Each phase ends with a gate. STOP, report what was built, wait for approval to proceed.

12. **Persistence requires `run_sleep_pass()`.** *(v3.1 amendment.)* Every `with Moneta(...)` block that calls `deposit()` MUST also call `m.run_sleep_pass()` before the block exits. `Moneta.close()` does NOT snapshot. Skipping `run_sleep_pass()` produces silent data loss — every deposit is forgotten when the handle closes. This is verified empirically in `scripts/probe_durability.py`. Tests assert post-close persistence on every commit touching the deposit path.

---

## Scope

**In scope:**

- Existing repo at `C:\Users\User\Comfy_Moneta_Bridge\` with `git init` already done
- Files in tree, uncommitted, included in Phase 0 commit:
  - `MONETA_API_SCOUT.md` (with Phase 0.5b addendum appended)
  - `scripts/probe_dimensionality.py`
  - `scripts/probe_durability.py`
  - `scripts/benchmark_handle.py`
- Seven modules: `tail.py`, `vector.py`, `ingest.py`, `capsule.py`, `state.py`, `launch.py`, `cli.py`
- Tests for each module, including a durability assertion on the deposit pipeline
- Integration smoke test
- README with architecture diagram, survivorship-bias note, idempotency-failure-mode note, and **WAL=1000 performance ceiling note**
- Demo workflow JSON + shot list

**Out of scope (do not build, do not propose):**

- Watching `comfy-cozy-experience.jsonl` (full-rewrite snapshot)
- MCP server interception
- USD sidecar handling (`.usda` / `.ratchet.json` / `.experience.json`)
- `_goals.json` planner file reading
- Cross-process locking beyond what ephemeral Moneta handles provide
- Any LLM, ML, or embedding library
- Modifications to Moneta or Comfy-Cozy
- HTTP service or daemon mode
- Real semantic embeddings (synthetic deterministic only — v0 demo doesn't need semantic similarity)
- Batched-deposit / write-coalescing layer (v1 candidate)

---

## Considered and Rejected — Do Not Re-litigate

| Recommendation | Status | Why rejected |
|---|---|---|
| Replace `watchfiles` with `os.stat` polling | REJECTED (v2 triage) | `watchfiles` does not require holding the file open. The Windows file-lock issue is solved by the open-read-close pattern alone. Polling is strictly worse. |
| Real semantic embeddings via `sentence-transformers` | REJECTED (v3 triage) | v0 demo is same-session cold-vs-warm replay. Semantic similarity is not the operation needed; session-keyed retrieval is. v1 candidate. |
| Bridge-side `session_index.json` belt-and-suspenders | REJECTED (v3 triage) | The synthetic vector encodes session membership implicitly. Additional index file would duplicate that information. |
| Bridge-side dedupe `(session, timestamp, workflow_hash)` set | REJECTED (v3 triage) | Cursor durability is the v0 idempotency mechanism. Cursor loss → some duplicates is documented. v1 candidate. |
| Long-lived Moneta handle + background snapshot | REJECTED (v3.1 triage) | Conflicts with Hard Rule §6 (ephemeral handles only). Reopening §6 reopens the resource-lock contract Gemini designed around. |
| Ephemeral handle + batch-of-N flush in `ingest.py` | REJECTED (v3.1 triage) | Adds real complexity: N-sized buffer, timer-based flush, partial-flush-on-shutdown, recovery semantics if process dies mid-batch. ~2 weeks of work. v1 candidate as a dedicated batching layer. |
| Direct `m.durability.snapshot_ecs(...)` per deposit | REJECTED (v3.1 triage) | Same private API the sleep pass uses internally. Doesn't reduce per-cycle cost. Private APIs break across Moneta versions. |
| Wait for Moneta-side `flush()` public method | REJECTED (v3.1 triage) | Would require a Moneta surgery first (violates Hard Rule §2). Out of scope for this mission. |

If a future change request asks to add any of these, it must first explain why the rejection reasoning above no longer holds.

---

## Architecture (Adopted from Gemini, Amended for Durability)

### Ingest path

```
1. watchfiles detects append on sessions/*_outcomes.jsonl
2. tail.py: open-read-close, parse new lines, hand each to ingest.py
3. ingest.py:
   a. extract session string (default "default")
   b. vector.synthesize_vector(session) → deterministic 384-dim
      unit vector
   c. payload_str = json.dumps(outcome_dict, sort_keys=True)
   d. with Moneta(...) as m:
        m.deposit(payload, embedding, ...)
        m.run_sleep_pass()        # ← v3.1: persistence call
   e. exit context manager (snapshot complete, handle closed,
      process lock released)
4. state.py: atomic cursor write + fsync, only after the with-block
   exits cleanly
```

### Hydrate path

```
1. User runs: bridge hydrate <session_name>
2. capsule.py:
   a. vector.synthesize_vector(session_name) → same vector as ingest
   b. with Moneta(...) as m:
        memories = m.query(embedding, limit=N)
   c. (no sleep_pass needed — query is read-only)
   d. filter: parsed["session"] == session_name (PRNG-collision guard)
   e. sort chronologically by timestamp
   f. translate to schema_version=2 (vision_notes → observation,
      key_params + quality_score → preference)
   g. atomic temp+rename to sessions/{name}.json
3. cli.py: print hot-hydrate warning + AUTO_LOAD_SESSION instruction
```

### Bridge state

```
.bridge_cursor.json (atomic temp+rename + fsync after every successful
                     ingest with-block)
{
  "watches": {
    "G:/Comfy-Cozy/sessions/default_outcomes.jsonl": {
      "inode": 12345,
      "offset": 2048
    }
  }
}
```

That's the entire bridge-owned state. No session index. No deduplication set. No model cache.

### Performance envelope (Phase 0.5b benchmark)

Mean per-cycle latency for `deposit + run_sleep_pass + close` at WAL size N:

| WAL size | mean | p99 | Disposition |
|---|---|---|---|
| 0 | 28 ms | 47 ms | demo conditions |
| 100 | 76 ms | 109 ms | demo upper bound |
| 1,000 | 685 ms | 736 ms | past mission STOP threshold |
| 10,000 | 4,518 ms | 6,505 ms | unusable |

v0 demo workloads (≤100 outcomes per session) run in the green zone. Production beyond ~1,000 outcomes per session requires the v1 batching layer. Documented as a v0 limitation in README.

---

## Phase 0 — Repo init + packaging (resume)

**Goal:** Bridge repo with packaging in place, first real commit including the Phase 0.5 deliverables.

### Steps

1. `cd C:\Users\User\Comfy_Moneta_Bridge\`. Confirm:
   - `git init` already ran (`git status` shows "On branch master, no commits yet")
   - `MONETA_API_SCOUT.md` is in tree (with Phase 0.5b addendum)
   - `scripts/probe_dimensionality.py`, `scripts/probe_durability.py`, `scripts/benchmark_handle.py` are in tree
   - `BRIDGE_BUILD_MISSION_v3.md` (superseded) and `BRIDGE_BUILD_MISSION_v3_1.md` (this file) both in tree

2. Create `pyproject.toml`:
   ```toml
   [build-system]
   requires = ["hatchling"]
   build-backend = "hatchling.build"

   [project]
   name = "comfy-moneta-bridge"
   version = "0.1.0"
   description = "Wire Comfy-Cozy experience output into Moneta cognitive substrate"
   requires-python = ">=3.11"
   dependencies = [
       "watchfiles>=0.21.0",
       "typer>=0.9.0",
       "moneta @ git+https://github.com/JosephOIbrahim/Moneta@v1.2.0-rc2",
   ]

   [project.optional-dependencies]
   dev = [
       "pytest>=8.0",
       "pytest-asyncio>=0.23",
   ]

   [project.scripts]
   bridge = "comfy_moneta_bridge.cli:app"

   [tool.pytest.ini_options]
   asyncio_mode = "auto"
   ```

3. Create the package skeleton:
   ```
   comfy_moneta_bridge/__init__.py        # __version__ = "0.1.0"
   comfy_moneta_bridge/tail.py            # empty stub
   comfy_moneta_bridge/vector.py          # empty stub
   comfy_moneta_bridge/ingest.py          # empty stub
   comfy_moneta_bridge/capsule.py         # empty stub
   comfy_moneta_bridge/state.py           # empty stub
   comfy_moneta_bridge/launch.py          # empty stub
   comfy_moneta_bridge/cli.py             # empty stub
   tests/__init__.py
   .gitignore                             # Python + .venv* + .moneta_probe/ + .bridge_cursor.json
   ```

4. Run `pip install -e ".[dev]"`. Verify it succeeds (validates Moneta git+ install resolves).

5. Run `pytest`. Should report 0 tests collected, exit 0.

6. **Phase 0 commit:**
   ```
   git add .
   git commit -m "Phase 0: repo init + packaging skeleton + 0.5 deliverables

   - pyproject.toml with watchfiles + typer + moneta v1.2.0-rc2
   - comfy_moneta_bridge/ package skeleton, 7 module stubs
   - tests/ scaffolding
   - .gitignore
   - MONETA_API_SCOUT.md (Phase 0.5a + 0.5b addendum)
   - scripts/ probes for dimensionality, durability, benchmark
   - BRIDGE_BUILD_MISSION_v3.md retained for provenance
   - BRIDGE_BUILD_MISSION_v3_1.md is the active spec"
   ```

### Phase 0 gate

Report: deps resolve, pytest collects clean, commit hash. STOP.

---

## Phase 1 — `vector.py` + tests

**Goal:** Pure-Python deterministic synthetic embedder. `DIMENSION = 384` per Phase 0.5b.

### Behavior contract

```python
DIMENSION = 384  # Phase 0.5b: variable-dim Moneta, BGE-small-standard size.

def synthesize_vector(session: str, dim: int = DIMENSION) -> list[float]:
    """Generate a deterministic unit vector keyed off the session string.

    Same session string → exactly identical vector.
    Different session strings → orthogonal-ish vectors.
    Output is L2-normalized to unit length.

    Implementation:
      1. seed = int.from_bytes(
             hashlib.sha256(session.encode("utf-8")).digest()[:8],
             "big"
         )
      2. rng = random.Random(seed)
      3. raw = [rng.gauss(0, 1) for _ in range(dim)]
      4. norm = math.sqrt(sum(x*x for x in raw))
      5. return [x / norm for x in raw]
    """
```

No other public functions. No state. Pure.

### Tests

`tests/test_vector.py`.

| Test | Verify |
|---|---|
| `test_same_session_produces_identical_vector` | Lists exactly equal (same RNG seed) |
| `test_different_sessions_produce_different_vectors` | Cosine similarity < 0.5 |
| `test_output_is_unit_length` | sum(x*x for x) ≈ 1.0 ± 1e-9 |
| `test_dimensionality_matches_constant` | len(result) == 384 |
| `test_unicode_session_name` | `"séssîön_测试"` returns valid unit vector |
| `test_empty_string_handled` | Empty string is a valid hash input |

### Phase 1 commit

```
git commit -m "Phase 1: vector.py — deterministic synthetic embedder

- sha256(session) → seeded Random → unit vector
- Stdlib only (hashlib, random, math)
- DIMENSION = 384 (Phase 0.5b)
- 6 tests, all passing"
```

### Phase 1 gate

Report. STOP.

---

## Phase 2 — `state.py` + tests

**Goal:** Cursor file management with atomic write + fsync. The idempotency-floor primitive.

### Behavior contract

```python
@dataclass
class WatchState:
    inode: int
    offset: int

class CursorStore:
    def __init__(self, cursor_path: Path):
        self.cursor_path = cursor_path
        self._cache: dict[str, WatchState] = self._load()

    def get(self, watched_path: str) -> WatchState | None: ...

    def set(self, watched_path: str, state: WatchState) -> None:
        """Atomic write + fsync. Called only after successful Moneta deposit
        with-block exits cleanly.

        Implementation:
          1. update self._cache
          2. write self._cache to {cursor_path}.tmp (utf-8)
          3. fsync the tmp file
          4. os.replace(tmp, cursor_path)
        """

    def _load(self) -> dict[str, WatchState]:
        """Read on init. Returns empty dict if file missing or malformed."""
```

### Tests

`tests/test_state.py`.

| Test | Verify |
|---|---|
| `test_empty_store_on_first_init` | `get()` returns None for any path |
| `test_set_then_get_round_trips` | `get()` returns equal WatchState |
| `test_persists_across_restarts` | New CursorStore instance returns persisted state |
| `test_atomic_write_no_partial_files` | Mock `os.replace` to raise; cursor file unchanged |
| `test_fsync_called` | Verify fsync called on tmp fd |
| `test_malformed_cursor_file_returns_empty` | Hand-write garbage; `_load` returns empty dict |
| `test_unicode_path_handled` | Path with unicode characters round-trips |

### Phase 2 commit

```
git commit -m "Phase 2: state.py — cursor management with fsync

- Atomic temp+rename + fsync on every write
- Self-healing: malformed cursor → empty store, no crash
- Persists across daemon restarts
- 7 tests, all passing"
```

### Phase 2 gate

Report. STOP.

---

## Phase 3 — `tail.py` + tests

**Goal:** Rotation-aware, encoding-safe, partial-line-safe JSONL tailer.

### Behavior contract

- Watches `{COMFY_COZY_ROOT}/sessions/*_outcomes.jsonl` via `watchfiles.awatch`.
- `COMFY_COZY_ROOT` configurable; default `G:/Comfy-Cozy`.
- Open-read-close pattern. **Never holds file handles persistently.**
- Per-file state via `CursorStore`.
- On `Modified` event:
  - open with `encoding="utf-8"`, seek to `last_offset`, read remaining bytes, close
  - split on `\n`; trailing fragment is "incomplete"
  - for each complete line: `json.loads`; on success, hand to `ingest.ingest_outcome(parsed, ...)`; on `JSONDecodeError`, log + skip
  - if trailing fragment non-empty: do NOT advance offset past its start. Wait for next event.
- On rotation detected (any of: `Deleted` event, file size shrunk below `last_offset`, inode mismatch on next read):
  - look for `{path}.1`
  - if exists: open at `last_offset`, drain to EOF, ingest those lines (the rotation race fix)
  - reset `last_offset = 0`, `last_inode = stat(path).st_ino`
  - resume normal operation on the new path

**Cursor advancement happens AFTER `ingest_outcome` returns successfully.** That return is the bridge's "this is durable in Moneta" signal — because Phase 4's contract requires `run_sleep_pass()` inside the `with`-block, a clean return means the snapshot is on disk.

### Module shape

```python
# comfy_moneta_bridge/tail.py

from pathlib import Path
import asyncio
import json
import os

from watchfiles import awatch, Change

from comfy_moneta_bridge.state import CursorStore, WatchState
from comfy_moneta_bridge.ingest import ingest_outcome

class Tailer:
    def __init__(self, sessions_dir: Path, cursor_store: CursorStore,
                 moneta_storage_path: Path): ...
    async def run(self): ...
    def _drain_complete_lines(self, path: Path, start_offset: int)
        -> tuple[list[dict], int]: ...
    def _handle_rotation(self, path: Path, state: WatchState)
        -> WatchState: ...
```

### Tests

`tests/test_tail.py`. All use `pytest.tmp_path`. `ingest_outcome` mocked with list-collector.

| Test | Verify |
|---|---|
| `test_appends_one_line` | One outcome ingested, offset matches end of file |
| `test_partial_line_then_completion` | 0 ingested first event, 1 ingested second event |
| `test_rotation_drains_jsonl_1` | All 10 lines ingested in order |
| `test_rotation_with_pre_rotation_unread_bytes` | All 8 lines including stranded ones in `.jsonl.1` |
| `test_unicode_emoji_in_vision_notes` | Line ingested cleanly |
| `test_state_persists_across_restart` | Restart picks up at correct offset |
| `test_malformed_line_skipped` | Malformed logged + skipped, valid ingested |

### Phase 3 commit

```
git commit -m "Phase 3: tail.py — rotation-aware JSONL tailer

- Open-read-close pattern (no persistent handles, Windows-safe)
- watchfiles event detection
- Rotation handling: drains *.jsonl.1 before switching
- Partial-line safe: never advances past incomplete trailing line
- utf-8 encoding explicit on all reads
- State delegated to state.CursorStore
- Cursor advances only after ingest_outcome returns clean
- 7 tests, all passing"
```

### Phase 3 gate

Report. STOP.

---

## Phase 4 — `ingest.py` + tests *(amended for durability)*

**Goal:** Per-line schema validation + ephemeral Moneta deposit + persistence.

### Behavior contract

```python
def ingest_outcome(outcome: dict, moneta_storage_path: Path) -> None:
    """Validate outcome line, synthesize vector, deposit + persist.

    Pipeline:
      1. validate schema_version == 1; if not, log and drop, return
      2. extract session = outcome.get("session", "default")
      3. embedding = vector.synthesize_vector(session)
      4. payload = json.dumps(outcome, sort_keys=True)
      5. with Moneta(snapshot_path=..., wal_path=...) as m:
            m.deposit(
                payload=payload,
                embedding=embedding,
                # additional kwargs per Phase 0.5b findings
            )
            m.run_sleep_pass()    # ← Hard Rule §12. NOT optional.
      6. exit context manager (snapshot is now durable on disk)

    Optional fields preserved as None throughout. No truthiness coercion.
    No bridge-side dedupe — cursor durability is the idempotency mechanism.

    The function returns only after run_sleep_pass() has completed.
    A clean return means: deposit is on disk and survives process death.
    The caller (tail.py) uses that signal to advance the cursor.
    """
```

The exact `Moneta(...)` instantiation arguments come from `MONETA_API_SCOUT.md`. If Phase 0.5b found additional required kwargs (e.g., `protected_floor`), include them per the addendum.

### Tests

`tests/test_ingest.py`. Two test categories: mocked-Moneta (most tests) and real-Moneta (durability assertion).

#### Mocked-Moneta tests (Moneta wrapped in mock context manager)

| Test | Verify |
|---|---|
| `test_full_record_passes_through` | `Moneta.deposit()` called once with payload + embedding |
| `test_run_sleep_pass_called` | `m.run_sleep_pass()` called inside `with`-block (Hard Rule §12 enforcement) |
| `test_run_sleep_pass_called_after_deposit` | Order: `deposit()` then `run_sleep_pass()`, never reverse |
| `test_payload_is_full_json` | Payload string parses back to identical dict |
| `test_session_drives_embedding` | Different sessions → different embeddings; same session → same |
| `test_optional_quality_score_preserves_none` | `None` survives into payload (not `0`) |
| `test_optional_render_time_preserves_none` | `None` survives |
| `test_zero_quality_score_preserved_distinctly` | `0.0` distinct from `None` in payload |
| `test_wrong_schema_version_dropped` | `Moneta.deposit()` NOT called; warning logged |
| `test_no_dedup_logic` | Two identical records → `deposit()` called twice |
| `test_handle_closed_after_deposit` | Context manager `__exit__` invoked |

#### Real-Moneta durability test (no mock)

| Test | Verify |
|---|---|
| `test_deposit_persists_after_handle_close` | Ingest one outcome → close handle → open NEW handle on same storage path → query → outcome IS retrievable. **This test would have caught the v3 bug. It is mandatory and runs on every commit touching the deposit path.** |

### Phase 4 commit

```
git commit -m "Phase 4: ingest.py — synthetic-vector deposit with persistence

- schema_version=1 enforced
- session string → deterministic vector via vector.synthesize_vector
- Full outcome JSON-dumped as payload
- Ephemeral Moneta handle (with-block per ingest call)
- run_sleep_pass() called inside with-block (Hard Rule §12)
- Clean return == deposit is durable on disk
- None preservation throughout (no zero coercion)
- No bridge-side dedupe (cursor owns idempotency)
- 12 tests including real-Moneta durability assertion, all passing"
```

### Phase 4 gate

Report. STOP.

---

## Phase 5 — `capsule.py` + tests

**Goal:** Query Moneta for a session, build `schema_version=2` capsule.

### Behavior contract

```python
def write_capsule(
    session_name: str,
    comfy_cozy_root: Path,
    moneta_storage_path: Path,
    query_limit: int = 1000,
) -> Path:
    """Build sessions/{name}.json from Moneta state for a given session.

    Pipeline:
      1. embedding = vector.synthesize_vector(session_name)
      2. with Moneta(...) as m:
            memories = m.query(embedding=embedding, limit=query_limit)
         (no run_sleep_pass needed — query is read-only)
      3. parsed = [json.loads(m.payload) for m in memories]
      4. filter: keep only where parsed["session"] == session_name
         (PRNG-collision guard against cosine-near-1 false positives)
      5. sort by parsed["timestamp"] ascending
      6. translate to schema_version=2 dict (see schema below)
      7. atomic temp+rename to sessions/{name}.json
      8. return path

    Schema translation:
      - vision_notes → notes type="observation", one note per line
      - key_params + quality_score → notes type="preference"
        text: f"Workflow params {key_params} achieved quality {quality_score}"
      - workflow block stubbed:
          {"loaded_path": None, "format": "api",
           "base_workflow": None, "current_workflow": None,
           "history_depth": 0}
      - notes empty list if no memories matched
      - metadata: {"hydrated_from": "moneta", "memory_count": N}
    """
```

If `query_limit` is reached (memories returned == limit), log a warning that older memories may be truncated. v0 ships with the warning; v1 candidate to paginate.

### Tests

`tests/test_capsule.py`. Moneta mocked (capsule does no writes that need persistence verification).

| Test | Verify |
|---|---|
| `test_writes_valid_schema_v2` | File written, parses, `schema_version == 2` |
| `test_filters_by_session_name` | PRNG-collision sim: only matching session in capsule |
| `test_chronological_order` | Capsule notes appear in timestamp order |
| `test_atomic_replace` | New write replaces atomically; no temp file left |
| `test_unicode_in_payloads` | Emoji round-trips through `json.load` |
| `test_empty_session_writes_empty_notes` | `notes: []`, valid schema_v2 |
| `test_query_limit_warning` | Warning logged at limit |
| `test_schema_v2_workflow_stub` | `workflow` block present with safe defaults |

### Phase 5 commit

```
git commit -m "Phase 5: capsule.py — Moneta query → Comfy-Cozy session JSON

- schema_version=2 conformance to Comfy-Cozy memory/session.py
- Filters memories by session string (PRNG-collision guard)
- Chronological ordering by outcome timestamp
- Schema translation: vision_notes → observation, params+score → preference
- Atomic temp+rename
- 8 tests passing"
```

### Phase 5 gate

Report. STOP.

---

## Phase 6 — `launch.py` + tests

**Goal:** Spawn Comfy-Cozy with `AUTO_LOAD_SESSION` env set.

### Behavior contract

- One public function: `launch_with_session(session_name, comfy_cozy_root, mode="run") -> subprocess.Popen`
- Verifies `{comfy_cozy_root}/sessions/{session_name}.json` exists; raises `FileNotFoundError` with clear message if not.
- Builds env: copy `os.environ`, set `AUTO_LOAD_SESSION={session_name}`.
- Spawns: `subprocess.Popen(["agent", mode], env=env, cwd=comfy_cozy_root)`.
- Returns the `Popen` handle.

### Tests

`tests/test_launch.py`. Mock `subprocess.Popen` via `monkeypatch`.

| Test | Verify |
|---|---|
| `test_missing_capsule_raises` | `FileNotFoundError`, no spawn |
| `test_env_contains_auto_load` | env has `AUTO_LOAD_SESSION=name` |
| `test_default_mode_is_run` | args include `["agent", "run"]` |
| `test_mcp_mode` | args include `["agent", "mcp"]` |

### Phase 6 commit + gate

```
git commit -m "Phase 6: launch.py — Comfy-Cozy spawn with AUTO_LOAD_SESSION

- Verifies capsule existence pre-spawn
- Env-var contract via subprocess.Popen
- 4 tests passing"
```

STOP.

---

## Phase 7 — `cli.py` + tests

**Goal:** Two-command Typer CLI.

### Behavior contract

```
bridge tail [--comfy-cozy-root PATH] [--moneta-storage PATH] [--state-dir PATH]
  - Defaults: --comfy-cozy-root G:/Comfy-Cozy
              --moneta-storage  ~/.comfy-moneta-bridge/moneta/
              --state-dir       ~/.comfy-moneta-bridge/
  - Runs Tailer.run() until SIGINT
  - Logs deposit events at INFO

bridge hydrate <session_name> [--comfy-cozy-root PATH] [--moneta-storage PATH] [--launch]
  - Calls capsule.write_capsule
  - Prints: ✓ Wrote sessions/{name}.json
  - Prints: Note: Comfy-Cozy must be (re)started for this to take effect.
            Running instances will not auto-load.
  - Prints: Run with: AUTO_LOAD_SESSION={name} agent run
  - If --launch: also calls launch.launch_with_session, prints PID
```

### Tests

`tests/test_cli.py`. Use `typer.testing.CliRunner`. Mock `Tailer.run`, `write_capsule`, `launch_with_session`.

| Test | Verify |
|---|---|
| `test_hydrate_writes_capsule` | Exit 0, success message in stdout |
| `test_hydrate_prints_warning` | "must be (re)started" warning present |
| `test_hydrate_with_launch_spawns` | Both functions called, PID printed |
| `test_tail_kicks_off` | Exit 0 |

### Phase 7 commit + gate

```
git commit -m "Phase 7: cli.py — bridge tail / bridge hydrate <name>

- Typer-based, two commands
- Hot-hydrate warning printed on every hydrate
- --launch flag for one-shot hydrate-and-spawn
- 4 tests passing"
```

STOP.

---

## Phase 8 — Integration smoke test

**Goal:** End-to-end test in a single pytest run, no real Comfy-Cozy needed. **Real Moneta substrate** (not mocked) — exercises the durability contract.

### Test shape

`tests/test_integration.py`.

One test: `test_cold_to_warm_arc`.

1. Create temp dir simulating `comfy-cozy-root/sessions/`.
2. Create temp dir for Moneta storage (real Moneta).
3. Start `Tailer` in a background task.
4. Write 5 outcome lines to `default_outcomes.jsonl` over 200ms.
5. Wait for tailer to drain.
6. Assert: 5 lines deposited (verify via `Moneta.query()` with the `"default"` synthetic vector — uses a fresh handle to prove durability).
7. Trigger rotation (rename `.jsonl` → `.jsonl.1`, create new empty `.jsonl`).
8. Write 3 more lines to new `.jsonl`.
9. Wait for tailer to drain.
10. Stop tailer cleanly.
11. **Open a brand-new Moneta handle** (proves persistence across handle close+reopen).
12. Assert: 8 total lines retrievable via Moneta query on the fresh handle.
13. Call `write_capsule("default", comfy_cozy_root, moneta_storage_path)`.
14. Assert: `sessions/default.json` exists, validates as `schema_version=2`, contains 8 chronological notes.
15. Assert: notes contain expected vision_notes content from the original outcomes.

The "fresh handle" step at #11 is non-negotiable. Without it, the test would pass against the same in-memory ECS that the deposits populated, and would not actually verify the `run_sleep_pass()` chain works end-to-end.

### Phase 8 commit + gate

```
git commit -m "Phase 8: integration smoke test — cold-to-warm arc

- End-to-end: tail → rotate → ingest → close → reopen → hydrate
- Real Moneta substrate on temp storage (not mocked)
- Fresh handle re-open between ingest and query proves durability
- All seven modules exercised
- Comfy-Cozy not invoked (file-only contract)"
```

STOP.

---

## Phase 9 — README + docs

**Goal:** Public-facing repo presentation.

### Files

1. **`README.md`:**
   - Title: `comfy-moneta-bridge`
   - Elevator: "Wires Comfy-Cozy's autonomous ComfyUI agent into Moneta's cognitive substrate. Cross-session memory for generative workflows."
   - Architecture diagram (ASCII) showing the synthetic-vector flow with `run_sleep_pass()` step explicit
   - Quickstart: `pip install -e .`, `bridge tail` in one terminal, `bridge hydrate default --launch` in another
   - Links to Moneta and Comfy-Cozy repos
   - **v0 limitations section** (full list, Hard Rule §12-aware):
     - **Survivorship bias:** only successful outcomes are captured (failed pipeline runs may not be persisted in v0). v1 candidate.
     - **Cursor-loss idempotency:** if `.bridge_cursor.json` is deleted, replaying the JSONL produces duplicate Moneta deposits. v1 candidate.
     - **Synthetic vectors:** v0 uses session-keyed deterministic vectors. Cross-session semantic learning is a v1 capability.
     - **Performance ceiling at WAL ≈ 1000:** each deposit triggers a full snapshot via `run_sleep_pass()`. Mean per-cycle latency: 28 ms at WAL=0, 76 ms at WAL=100, **685 ms at WAL=1000**, 4.5 s at WAL=10000. v0 is comfortable for demo workloads (~100 outcomes/session). Production beyond ~1000 outcomes/session requires the v1 batched-deposit layer.
   - License (match Joe's pattern)

2. **`docs/architecture.md`:**
   - Longer-form: ingest path, hydrate path, why synthetic vectors, Windows file-lock notes, rotation handling, ephemeral Moneta handle pattern, why `run_sleep_pass()` is mandatory
   - References the Gemini review (2026-04-29) as the architectural source
   - Documents Phase 0.5b findings (dim chosen + benchmark numbers + durability contract)
   - Section: "Why deposit alone doesn't persist" — short explanation grounded in `probe_durability.py` results

3. **`demo/workflow.json`:** Minimal SDXL txt2img workflow placeholder.

4. **`demo/shot_list.md`:**
   - Cold-vs-warm two-session arc walkthrough
   - Screen recording cues, narration outline

### Phase 9 commit + gate

```
git commit -m "Phase 9: README + architecture docs + demo scaffolding

- README with quickstart, architecture diagram, v0 limitations (4 items)
- WAL-1000 performance ceiling documented with benchmark table
- docs/architecture.md with full path-flow + durability contract
- demo/ with workflow.json placeholder + shot_list.md
- Tag candidate: v0.1.0 (do not push without approval)"
```

Final gate. STOP.

---

## Git Discipline

Per Joe's CLAUDE.md Git Authority Map:

| Tier | Operations | Authority for this mission |
|---|---|---|
| 1 | Read (`git log`, `git diff`, `git status`) | Autonomous |
| 2 | `git add`, `git commit`, `git tag` | Session-authorized |
| 3 | `git push`, `git reset`, `git rebase` | **Per-call human approval required.** |
| 4 | `git push --force`, history rewrites | **Forbidden.** |

### Commit message format

```
Phase N: <module> <one-line summary>

- bullet
- bullet
- bullet
```

No emojis. No `Co-authored-by` lines.

### When to ask about push

After every phase commit, ask:

> "Phase N committed locally as {hash}. Push to origin? [y/N]"

Default no. Wait for explicit y.

---

## Mile Markers

Surface these at the start of each phase response:

```
✅ Phase 0.5a: Moneta API scout
✅ Phase 0.5b: Dimensionality + benchmark + durability finding
✅ Phase 0:    Repo init + packaging + 0.5 deliverables committed
✅ Phase 1:    vector.py deterministic embedder
✅ Phase 2:    state.py cursor with fsync
✅ Phase 3:    tail.py rotation-aware tailer
✅ Phase 4:    ingest.py synthetic-vector deposit + run_sleep_pass
✅ Phase 5:    capsule.py session query + schema_v2
→  Phase 6:    launch.py Comfy-Cozy spawn        ← here
   Phase 7:    cli.py user-facing
   Phase 8:    Integration smoke test (real Moneta + fresh-handle proof)
   Phase 9:    README + docs
```

---

## What Gets Reported Back After Each Phase

```
Phase N — {name}
- Files created/modified: {list}
- Lines of code: {count}
- Tests added: {count}
- Tests passing: {count}/{count}
- Commit hash: {hash}
- Push status: pending Joe approval | pushed
- Anything unexpected: {describe or "none"}
```

Then STOP. Wait for Joe's "proceed" before starting the next phase.

---

## Kickoff (resuming after Phase 0.5b halt)

When Claude Code reads this file, the first action is:

1. Read this entire mission file end-to-end. Confirm understanding.
2. Confirm working directory is `C:\Users\User\Comfy_Moneta_Bridge\`.
3. Confirm `MONETA_API_SCOUT.md` (with Phase 0.5b addendum), the three probe scripts, and `BRIDGE_BUILD_MISSION_v3.md` (superseded) are in tree, all uncommitted.
4. **Begin Phase 0.** Phase 0.5a and 0.5b are complete; their deliverables fold into the Phase 0 commit per Phase 0 step 6.

Marathon markers every phase. STOP at every gate. No push without per-call approval. utf-8 everywhere. No ML libraries. Ephemeral Moneta handles only. **`run_sleep_pass()` after every `deposit()` inside the same `with`-block.**

Go.
