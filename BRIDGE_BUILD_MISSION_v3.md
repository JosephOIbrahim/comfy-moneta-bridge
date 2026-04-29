# BRIDGE BUILD MISSION v3 — `comfy-moneta-bridge` v0

**Role:** `[SCAFFOLD × FORGE]` with `[SCAFFOLD × SCOUT]` pre-flight extension
**Type:** Greenfield build, phase-gated, test-driven
**Repo:** `comfy-moneta-bridge` (initialized at `C:\Users\User\Comfy_Moneta_Bridge\`)
**Architecture:** Adopted from Gemini Deep Think review (2026-04-29), ARCHITECTURALLY VIABLE WITH GAPS

---

## Frame

Build the v0 bridge that wires Comfy-Cozy's experience output into Moneta's persistence layer. Both source repos are frozen as law: no modifications to either. The bridge is a third, public-facing Python package that consumes Comfy-Cozy's outputs by file watch and produces inputs by file write. No code-level coupling. No process injection.

The original spec (v1) and its Windows hardening pass (v2) assumed Moneta exposed session-keyed ingest and read APIs. The pre-flight scout (`MONETA_API_SCOUT.md`) confirmed Moneta v1.2.0-rc2 exposes only the four-op substrate handle with no session dimension. v3 adopts Gemini's architectural pivot: **deterministic synthetic embeddings keyed off the session string**, ephemeral Moneta handles per call, stdlib-only.

This v3 is the corrected architecture. Build it. Do not re-litigate the previous versions.

---

## Hard Rules — Read Before Every Phase

1. **No push to origin without explicit per-call approval.** Joe's Git Authority Map: Level 3 operations (push, reset, rebase) require per-call human approval. Commits and tags are session-authorized for the duration of this mission. Force-push and history rewrites are forbidden.

2. **No modifications to Moneta source.** `C:\Users\User\Moneta\` is read-only for this mission. If the bridge needs an API that doesn't exist in Moneta, STOP and report — do not patch Moneta.

3. **No modifications to Comfy-Cozy source.** `G:\Comfy-Cozy\` is read-only for this mission. If the bridge needs a contract change in Comfy-Cozy, STOP and report — do not patch Comfy-Cozy.

4. **utf-8 explicit on every file I/O.** Every `open()` call gets `encoding="utf-8"`. No exceptions. Windows default is `cp1252` and that will silently break on emoji or smart quotes in `vision_notes`.

5. **Open-read-close pattern in `tail.py`.** Never hold a persistent file handle to `*_outcomes.jsonl`. Windows holds a read lock that will block Comfy-Cozy's `os.replace` during rotation and crash the agent.

6. **Ephemeral Moneta handles only.** Never hold a `Moneta(...)` instance open across function calls. Open inside a `with` block, do the deposit/query, exit. Process exclusivity (`MonetaResourceLockedError`) makes any other pattern unsafe when `bridge tail` and `bridge hydrate` may run concurrently.

7. **No ML / embedding library in dependencies.** No `sentence-transformers`, no `torch`, no `transformers`, no remote embedding APIs. The synthetic embedder uses stdlib only (`hashlib`, `random`, `struct`).

8. **Tests pass before commit.** Every module has tests. `pytest` clean before `git commit`. No `-x` skip flags.

9. **Reconnaissance before building.** Phase 0.5a is the Moneta API scout (already complete). Phase 0.5b is the **dimensionality + benchmark addendum** — must complete before Phase 1.

10. **Atomic commits with clear provenance.** One concept per commit. Commit message format below.

11. **STOP at gates.** Each phase ends with a gate. STOP, report what was built, wait for approval to proceed.

---

## Scope

**In scope:**

- Existing repo at `C:\Users\User\Comfy_Moneta_Bridge\` with `git init` already done and `MONETA_API_SCOUT.md` already in tree (uncommitted)
- Phase 0.5b scout addendum (dimensionality + benchmark)
- Seven modules: `tail.py`, `vector.py`, `ingest.py`, `capsule.py`, `state.py`, `launch.py`, `cli.py`
- Tests for each module
- Integration smoke test
- README with architecture diagram, survivorship-bias note, and idempotency-failure-mode note
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

---

## Considered and Rejected — Do Not Re-litigate

Recommendations that were considered and rejected during the architecture passes. Listed here so they are not re-introduced as "improvements."

| Recommendation | Status | Why rejected |
|---|---|---|
| Replace `watchfiles` with `os.stat` polling | REJECTED (v2 triage) | `watchfiles` does not require holding the file open. The Windows file-lock issue is solved by the open-read-close pattern alone. Polling is strictly worse: higher idle CPU, slower event detection, no compensating benefit. |
| Real semantic embeddings via `sentence-transformers` | REJECTED (v3 triage) | v0 demo arc is same-session cold-vs-warm replay. Semantic similarity is not the operation needed; session-keyed retrieval is. Synthetic deterministic vectors map cleanly to that, eliminate ~500MB of dependencies, and remove model-load latency on `bridge tail` startup. v1 candidate if cross-session semantic learning becomes a demo requirement. |
| Bridge-side `session_index.json` belt-and-suspenders | REJECTED (v3 triage) | The synthetic vector encodes session membership implicitly. An additional index file would duplicate that information. v1 candidate if debugging visibility becomes a real need. |
| Bridge-side dedupe `(session, timestamp, workflow_hash)` set | REJECTED (v3 triage) | Cursor durability is the v0 idempotency mechanism. Cursor loss is a documented failure mode that produces some duplicates. v1 candidate if duplicate rate causes demo problems. |

If a future change request asks to add any of these, it must first explain why the rejection reasoning above no longer holds.

---

## Architecture (Adopted from Gemini)

### Ingest path

```
1. watchfiles detects append on sessions/*_outcomes.jsonl
2. tail.py: open-read-close, parse new lines, hand each to ingest.py
3. ingest.py:
   a. extract session string (default "default")
   b. vector.synthesize_vector(session) → deterministic 384-dim
      unit vector (or matching Moneta's enforced dim per Phase 0.5b)
   c. payload_str = json.dumps(outcome_dict, sort_keys=True)
   d. with Moneta(...) as m: m.deposit(payload, embedding, ...)
   e. exit context manager (closes handle, releases process lock)
4. state.py: atomic cursor write + fsync, only after successful deposit
```

### Hydrate path

```
1. User runs: bridge hydrate <session_name>
2. capsule.py:
   a. vector.synthesize_vector(session_name) → same vector as ingest
   b. with Moneta(...) as m: memories = m.query(embedding, limit=N)
   c. filter: parsed["session"] == session_name (PRNG-collision guard)
   d. sort chronologically by timestamp
   e. translate to schema_version=2:
      - vision_notes → notes type="observation"
      - key_params + quality_score → notes type="preference"
      - workflow block stubbed safe defaults
   f. atomic temp+rename to sessions/{name}.json
3. cli.py: print hot-hydrate warning + AUTO_LOAD_SESSION instruction
```

### Bridge state

```
.bridge_cursor.json (atomic temp+rename + fsync after every successful deposit)
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

---

## Phase 0.5b — Pre-Flight Addendum (RUNS BEFORE PHASE 1)

**This is the only thing standing between us and Phase 1.** Two checks, both required.

### Check 1: Vector dimensionality

Moneta v1.2.0-rc2 accepts `embedding: List[float]`. The scout did not document whether Moneta enforces a specific dimensionality. Resolve before writing `vector.py`.

**Steps:**

1. Read `C:\Users\User\Moneta\moneta\__init__.py` for the `deposit` method signature and any dimension-related constant.
2. Search for `dim`, `dimension`, `embedding_dim`, `vector_size`, or similar identifiers.
3. Read the schema file referenced in the scout (e.g., `SURGERY_complete_codeless_schema.md`) or whatever Moneta uses for vector storage.
4. **Write a probe script** at `scripts/probe_dimensionality.py`:
   ```python
   # Probe Moneta for accepted vector dimensions.
   # Tries: 64, 128, 256, 384, 512, 768, 1024, 1536, 3072
   # Reports: which sizes deposit succeeds at, which raise.
   ```
5. Run the probe against a temp Moneta store (`./.moneta_probe/`).

**Result options:**

- **Fixed dim:** Moneta enforces exactly one size (e.g., 1536). `vector.py` produces vectors of that exact size. Document in scout addendum.
- **Variable dim:** Moneta accepts any size, infers from first deposit. Pick **384** (BGE-small standard, lightweight). Document choice and rationale.
- **Failed to determine:** STOP. Report findings, halt mission.

### Check 2: Ephemeral handle overhead benchmark

Gemini flagged that opening Moneta replays the WAL on every instantiation. With ephemeral handles per deposit, this could degrade at scale.

**Steps:**

1. Write `scripts/benchmark_handle.py`:
   ```python
   # Open Moneta, deposit one record, close. Repeat 100 times.
   # Measure: total wall time, mean per-cycle, p50, p95, p99.
   # Repeat with WAL pre-populated to 100 / 1,000 / 10,000 entries.
   ```
2. Run against a temp Moneta store.
3. Capture timings.

**Acceptance criteria:**

- Mean per-cycle < 100ms at all scales tested → ship as-is
- Mean per-cycle 100-500ms → ship with a v1 task to add a deposit-batching layer
- Mean per-cycle > 500ms → STOP. Architecture cost is too high. Reconsider.

### Phase 0.5b deliverable

Append to `MONETA_API_SCOUT.md` a new section: `## Addendum — Phase 0.5b (2026-04-29)`. Contains:

- Vector dimensionality finding + decision (specific value chosen)
- Benchmark table (cycles per WAL size, p50/p95/p99)
- Disposition (ship-as-is / ship-with-v1-task / STOP)

### Phase 0.5b commit

```
git add MONETA_API_SCOUT.md scripts/probe_dimensionality.py scripts/benchmark_handle.py
git commit -m "Phase 0.5b: dimensionality probe + handle benchmark

- Moneta enforces dim={value}
- Ephemeral handle overhead: {p50}/{p95}/{p99} ms
- Disposition: {ship-as-is | ship-with-v1-task | STOP}"
```

### Phase 0.5b gate

Report: dim chosen, benchmark numbers, disposition. STOP.

---

## Phase 0 — Repo init + packaging (resume after 0.5b)

**Goal:** Bridge repo with packaging in place, first real commit. (Note: `git init` was already executed during the original Phase 0 attempt and is preserved.)

### Steps

1. `cd C:\Users\User\Comfy_Moneta_Bridge\`. Confirm `git init` already ran (no `git init` again — `git status` will show "On branch master, no commits yet").
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
   .gitignore                             # standard Python + .venv* + .moneta_probe/
   ```

4. Run `pip install -e ".[dev]"`. Verify it succeeds (validates Moneta git+ install resolves).

5. Run `pytest`. Should report 0 tests collected, exit 0.

6. **Phase 0 commit:**
   ```
   git add .
   git commit -m "Phase 0: repo init + packaging skeleton

   - pyproject.toml with watchfiles + typer + moneta v1.2.0-rc2
   - comfy_moneta_bridge/ package skeleton, 7 module stubs
   - tests/ scaffolding
   - .gitignore
   - MONETA_API_SCOUT.md (pre-flight) included from prior session"
   ```

### Phase 0 gate

Report: deps resolve, pytest collects clean, commit hash. STOP.

---

## Phase 1 — `vector.py` + tests

**Goal:** Pure-Python deterministic synthetic embedder.

### Behavior contract

```python
def synthesize_vector(session: str, dim: int = DIMENSION) -> list[float]:
    """Generate a deterministic unit vector keyed off the session string.

    Same session string → exactly identical vector.
    Different session strings → orthogonal-ish vectors (PRNG independence).
    Output is L2-normalized to unit length.

    Implementation:
      1. seed = int.from_bytes(hashlib.sha256(session.encode("utf-8")).digest()[:8], "big")
      2. rng = random.Random(seed)
      3. raw = [rng.gauss(0, 1) for _ in range(dim)]
      4. norm = math.sqrt(sum(x*x for x in raw))
      5. return [x / norm for x in raw]
    """
```

`DIMENSION` is a module constant set to whatever Phase 0.5b determined.

No other public functions. No state. Pure.

### Tests

`tests/test_vector.py`.

| Test | Setup | Verify |
|---|---|---|
| `test_same_session_produces_identical_vector` | Call twice with `"default"` | Lists are exactly equal (no float drift — same RNG seed) |
| `test_different_sessions_produce_different_vectors` | Call with `"default"` and `"experimental"` | Vectors differ; cosine similarity < 0.5 |
| `test_output_is_unit_length` | Call with `"default"` | sum(x*x for x) ≈ 1.0 ± 1e-9 |
| `test_dimensionality_matches_constant` | Call with `"default"` | len(result) == DIMENSION |
| `test_unicode_session_name` | Call with `"séssîön_测试"` | No error, returns valid unit vector |
| `test_empty_string_handled` | Call with `""` | No error (empty string is a valid hash input) |

### Phase 1 commit

```
git commit -m "Phase 1: vector.py — deterministic synthetic embedder

- sha256(session) → seeded Random → unit vector
- Stdlib only (hashlib, random, math)
- DIMENSION = {value from Phase 0.5b}
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
        """Atomic write + fsync. Called only after successful Moneta deposit.

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

| Test | Setup | Verify |
|---|---|---|
| `test_empty_store_on_first_init` | No cursor file | `get()` returns None for any path |
| `test_set_then_get_round_trips` | Set state for path | `get()` returns equal WatchState |
| `test_persists_across_restarts` | Set, instantiate new CursorStore | New instance returns the persisted state |
| `test_atomic_write_no_partial_files` | Crash mid-write (mock `os.replace` to raise) | Cursor file remains in pre-write state |
| `test_fsync_called` | Set state, mock `os.fsync` | Verify fsync called on tmp fd |
| `test_malformed_cursor_file_returns_empty` | Hand-write garbage to cursor file | `_load` returns empty dict, no crash |
| `test_unicode_path_handled` | Path with unicode characters | Round-trips cleanly |

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

Identical to v2 spec for the tailer logic itself. Updated only to pass parsed lines to the new `ingest.py` signature (with synthetic vector strategy) and to use `state.CursorStore` for persistence.

- Watches `{COMFY_COZY_ROOT}/sessions/*_outcomes.jsonl` via `watchfiles.awatch`.
- `COMFY_COZY_ROOT` configurable; default `G:/Comfy-Cozy`.
- Open-read-close pattern. **Never holds file handles persistently.**
- Per-file state via `CursorStore` (persisted across restarts).
- On `Modified` event:
  - open with `encoding="utf-8"`, seek to `last_offset`, read remaining bytes, close
  - split on `\n`; trailing fragment is "incomplete"
  - for each complete line: `json.loads`; on success, hand to `ingest.ingest_outcome(parsed)`; on `JSONDecodeError`, log + skip
  - if trailing fragment non-empty: do NOT advance offset past its start. Wait for next event.
- On rotation detected (any of: `Deleted` event, file size shrunk below `last_offset`, inode mismatch on next read):
  - look for `{path}.1`
  - if exists: open at `last_offset`, drain to EOF, ingest those lines (the rotation race fix)
  - reset `last_offset = 0`, `last_inode = stat(path).st_ino`
  - resume normal operation on the new path

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
    def __init__(self, sessions_dir: Path, cursor_store: CursorStore): ...
    async def run(self): ...
    def _drain_complete_lines(self, path: Path, start_offset: int) -> tuple[list[dict], int]: ...
    def _handle_rotation(self, path: Path, state: WatchState) -> WatchState: ...
```

### Tests

`tests/test_tail.py`. All use `pytest.tmp_path` for filesystem isolation. `ingest_outcome` mocked with list-collector.

Same 7 tests as v2:

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
- 7 tests, all passing"
```

### Phase 3 gate

Report. STOP.

---

## Phase 4 — `ingest.py` + tests

**Goal:** Per-line schema validation + ephemeral Moneta deposit.

### Behavior contract

```python
def ingest_outcome(outcome: dict, moneta_storage_path: Path) -> None:
    """Validate outcome line, synthesize vector, deposit to Moneta.

    Pipeline:
      1. validate schema_version == 1; if not, log and drop
      2. extract session = outcome.get("session", "default")
      3. embedding = vector.synthesize_vector(session)
      4. payload = json.dumps(outcome, sort_keys=True)
      5. with Moneta(snapshot_path=..., wal_path=...) as m:
            m.deposit(
                payload=payload,
                embedding=embedding,
                protected_floor=float(outcome.get("quality_score") or 0.0)
            )
      6. exit context manager (handle closed, lock released)

    Optional fields preserved as None throughout. No truthiness coercion.
    No bridge-side dedupe — cursor durability is the idempotency mechanism.
    """
```

The exact `Moneta(...)` instantiation arguments come from Phase 0.5b findings. If `protected_floor` is not a real Moneta argument, drop it.

### Tests

`tests/test_ingest.py`. Moneta wrapped in a mock context manager.

| Test | Setup | Verify |
|---|---|---|
| `test_full_record_passes_through` | Valid 12-field record | `Moneta.deposit()` called once with payload + embedding |
| `test_payload_is_full_json` | Valid record | Payload string parses back to identical dict |
| `test_session_drives_embedding` | Records with sessions `"a"` and `"b"` | Different embeddings; same session twice → same embedding |
| `test_optional_quality_score_preserves_none` | Record with `quality_score: None` | `None` survives into the payload (not `0`) |
| `test_optional_render_time_preserves_none` | Record with `render_time_s: None` | `None` survives |
| `test_zero_quality_score_preserved_distinctly` | Record with `quality_score: 0.0` | `0.0` in payload (distinct from `None`) |
| `test_wrong_schema_version_dropped` | Record with `schema_version: 2` | `Moneta.deposit()` NOT called, warning logged |
| `test_no_dedup_logic` | Two identical records | `Moneta.deposit()` called twice |
| `test_handle_closed_after_deposit` | Successful deposit | Context manager `__exit__` invoked |

### Phase 4 commit

```
git commit -m "Phase 4: ingest.py — synthetic-vector deposit pipeline

- schema_version=1 enforced
- session string → deterministic vector via vector.synthesize_vector
- Full outcome JSON-dumped as payload
- Ephemeral Moneta handle (with-block per deposit)
- None preservation throughout (no zero coercion)
- No bridge-side dedupe (cursor owns idempotency)
- 9 tests, all passing"
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

If `query_limit` is reached (memories returned == limit), log a warning that older memories may be truncated. Phase 5 ships with the warning; v1 candidate to paginate.

### Tests

`tests/test_capsule.py`. Moneta mocked. `comfy_cozy_root` is a tmp_path.

| Test | Setup | Verify |
|---|---|---|
| `test_writes_valid_schema_v2` | Moneta returns 3 memories for "default" | File written, parses, `schema_version == 2` |
| `test_filters_by_session_name` | Moneta returns 5 memories: 3 "default" + 2 "other" (PRNG collision sim) | Capsule contains only the 3 |
| `test_chronological_order` | 3 memories with timestamps unsorted | Capsule notes appear in timestamp order |
| `test_atomic_replace` | Pre-existing capsule at target path | New write replaces atomically; no temp file left |
| `test_unicode_in_payloads` | Memory with emoji in `vision_notes` | Capsule round-trips through `json.load` |
| `test_empty_session_writes_empty_notes` | Moneta returns 0 memories | Capsule written with `notes: []`, valid schema_v2 |
| `test_query_limit_warning` | Moneta returns exactly `query_limit` items | Warning logged about possible truncation |
| `test_schema_v2_workflow_stub` | Standard write | `workflow` block present with safe defaults |

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

Identical to v2 spec — no architectural change here.

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

Updated from v2 to pass `moneta_storage_path` consistently.

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

**Goal:** End-to-end test in a single pytest run, no real Comfy-Cozy needed.

### Test shape

`tests/test_integration.py`.

One test: `test_cold_to_warm_arc`.

1. Create temp dir simulating `comfy-cozy-root/sessions/`.
2. Create temp dir for Moneta storage (real Moneta, not mocked — this test exercises the actual substrate).
3. Start `Tailer` in a background task.
4. Write 5 outcome lines to `default_outcomes.jsonl` over 200ms.
5. Wait for tailer to drain.
6. Assert: 5 lines deposited via `Moneta.query()` with the `"default"` synthetic vector.
7. Trigger rotation (rename `.jsonl` → `.jsonl.1`, create new empty `.jsonl`).
8. Write 3 more lines to new `.jsonl`.
9. Wait for tailer to drain.
10. Assert: 8 total lines retrievable via Moneta query.
11. Stop tailer.
12. Call `write_capsule("default", comfy_cozy_root, moneta_storage_path)`.
13. Assert: `sessions/default.json` exists, validates as `schema_version=2`, contains 8 chronological notes.
14. Assert: notes contain expected vision_notes content from the original outcomes.

### Phase 8 commit + gate

```
git commit -m "Phase 8: integration smoke test — cold-to-warm arc

- End-to-end: tail → rotate → ingest → hydrate
- Real Moneta substrate (not mocked) on temp storage
- All seven modules exercised
- Comfy-Cozy not invoked (file-only contract)"
```

STOP.

---

## Phase 9 — README + docs

**Goal:** Public-facing repo presentation.

### Files

1. `README.md`:
   - Title: `comfy-moneta-bridge`
   - Elevator: "Wires Comfy-Cozy's autonomous ComfyUI agent into Moneta's cognitive substrate. Cross-session memory for generative workflows."
   - Architecture diagram (ASCII) showing the synthetic-vector flow
   - Quickstart: `pip install -e .`, `bridge tail` in one terminal, `bridge hydrate default --launch` in another
   - Links to Moneta and Comfy-Cozy repos
   - **v0 limitations section** listing the documented failure modes:
     - Survivorship bias: only successful outcomes are captured (failed pipeline runs may not be persisted)
     - Cursor-loss idempotency: if `.bridge_cursor.json` is deleted, replaying the JSONL produces duplicate Moneta deposits. v1 candidate.
     - Synthetic vectors: v0 uses session-keyed deterministic vectors. Cross-session semantic learning is a v1 capability.
   - License (match Joe's pattern across Moneta and Comfy-Cozy)

2. `docs/architecture.md`:
   - Longer-form: ingest path, hydrate path, why synthetic vectors, Windows file-lock notes, rotation handling, ephemeral Moneta handle pattern
   - References the Gemini review (2026-04-29) as the architectural source
   - Documents Phase 0.5b findings (dim chosen + benchmark numbers)

3. `demo/workflow.json`: Minimal SDXL txt2img workflow (placeholder for the actual demo workflow).

4. `demo/shot_list.md`:
   - Cold-vs-warm two-session arc walkthrough per Gemini's Section 7
   - Screen recording cues, narration outline

### Phase 9 commit + gate

```
git commit -m "Phase 9: README + architecture docs + demo scaffolding

- README with quickstart, architecture diagram, v0 limitations
- docs/architecture.md with full path-flow descriptions
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
| 2 | `git add`, `git commit`, `git tag` (NOT `git init` — already done) | Session-authorized for the duration of this mission |
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
✅ Phase 0.5a: Moneta API scout (already complete)
✅ Phase 0.5b: Dimensionality + benchmark addendum
✅ Phase 0:    Repo init + packaging
✅ Phase 1:    vector.py deterministic embedder
✅ Phase 2:    state.py cursor with fsync
✅ Phase 3:    tail.py rotation-aware tailer
✅ Phase 4:    ingest.py synthetic-vector deposit
✅ Phase 5:    capsule.py session query + schema_v2
→  Phase 6:    launch.py Comfy-Cozy spawn        ← here
   Phase 7:    cli.py user-facing
   Phase 8:    Integration smoke test
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

## Kickoff

When Claude Code reads this file, the first action is:

1. Read this entire mission file end-to-end. Confirm understanding.
2. Confirm working directory is `C:\Users\User\Comfy_Moneta_Bridge\` and `MONETA_API_SCOUT.md` is present.
3. **Run Phase 0.5b BEFORE Phase 0** — dimensionality probe + handle benchmark. STOP if disposition is STOP.
4. If 0.5b passes, proceed with Phase 0 (real packaging + first commit).

Marathon markers every phase. STOP at every gate. No push without per-call approval. utf-8 everywhere. No ML libraries. Ephemeral Moneta handles only.

Go.
