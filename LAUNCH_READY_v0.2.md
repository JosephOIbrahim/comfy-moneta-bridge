# LAUNCH READY — comfy-moneta-bridge v0.2.0

**Pushed:** TBD (filled in post-merge of PR #1)
**Tag:** `v0.2.0` (annotated; created post-merge against `master`)
**Repo URL:** https://github.com/JosephOIbrahim/comfy-moneta-bridge
**Default branch:** `master`
**Visibility:** public
**No GitHub Release created** — v0.2 launch decision is yours; promote `v0.2.0` to a Release manually via the GitHub Releases UI when ready.
**Previous release:** `LAUNCH_READY.md` (v0.1.0, 2026-04-29 — preserved unchanged for provenance).

---

## What's new in v0.2

The bridge gains an opt-in agent-driven workflow manipulation surface. v0.1's thin-pass-through behavior is unchanged; new functionality lives in `comfy_moneta_bridge/agents/` behind the `[agents]` extras install.

- **Agent layer (`agents/`)** — `Workflow` graph model + mutation primitives, ComfyUI HTTP+WS client (localhost-only by default), single-source tool surface with Anthropic+MCP schema parity, five-role split (PLANNER/MUTATOR/EXECUTOR/CRITIC/MEMORIST) with deterministic turn-taking, `CheckpointStore` reusing `state.CursorStore`'s atomic-write pattern, orchestrator, stdio MCP server, and internal Anthropic-SDK Claude loop.
- **New CLI commands** — `bridge orchestrate <goal>` (internal Claude loop, default model `claude-opus-4-7`) and `bridge mcp` (stdio MCP server). Both lazy-import the `[agents]` extras and fail with a clear install hint if missing.
- **Capsule workflow block populates from snapshots** — `write_capsule` now scans queried memories for the latest `_kind=workflow_snapshot` deposit per session and threads it into the workflow block. The function signature is unchanged; the existing `test_schema_v2_workflow_stub` regression canary still asserts the exact pre-v0.2 null-block dict on the no-snapshot path.
- **Mission addendum `BRIDGE_BUILD_MISSION_v3_2.md`** — formally unlocks the workflow-agent scope that v3.1 listed as out-of-scope. v3.1 is preserved unchanged.
- **Runtime constitution `AGENTS.md`** — re-read per orchestration (no module-level cache) so operators can edit live. Codifies role-tool allowlists, refusal cases, idempotency, Moneta durability discipline, model identity, frozen-rules clause, and failure escalation.
- **Four new Hard Rules** (extend, do not amend, Rules 1–12):

  | # | Rule |
  |---|---|
  | §13 | `bridge tail` and `bridge orchestrate`/`bridge mcp` are mutually exclusive (PID-file mutex on `{state_dir}/tail.pid` and `{state_dir}/orchestrate.pid`). They share the Moneta URI lock; this prevents the `MonetaResourceLockedError` deadlock class. |
  | §14 | `workflow_submit` runs `workflow_validate` against `/object_info` first. Validation errors block submission with a structured `RefusalError`. |
  | §15 | `ComfyClient` defaults to `http://127.0.0.1:8188`. Non-localhost requires `BRIDGE_ALLOW_REMOTE_COMFY=1`. Refusal happens before any network call. |
  | §16 | EXECUTOR refuses without a fresh PLANNER/MUTATOR checkpoint. `--interactive` adds a human-ack step at the same point (`AGENT_COMMANDMENTS.md §8` irreversible-transition gate). |

- **§7 clarifying amendment** — LLM SDKs (`anthropic`, `mcp`) are permitted exclusively in the `agents/` subpackage, gated by the `[agents]` extras. The lean core install (`pip install comfy-moneta-bridge`) is unchanged.

---

## Pinned dependency versions

### Moneta — `v1.2.0-rc2` (unchanged from v0.1)

- Pinned in `pyproject.toml` via direct git URL:
  ```toml
  "moneta @ git+https://github.com/JosephOIbrahim/Moneta@v1.2.0-rc2",
  ```
- Tag points at Moneta commit `76da067` ("Add free-threading guard to AttentionLog").
- Phase 0.5b dimensionality probe + handle benchmark numbers in `docs/architecture.md` correspond to this version.

### Comfy-Cozy — frozen at scout HEAD `8827772` (unchanged from v0.1)

- Not a Python dependency; bridge reads Comfy-Cozy state via the filesystem.
- Schema_v2 translation in `capsule.py` was validated against `G:/Comfy-Cozy/agent/memory/session.py` at this SHA.

### NEW: `[agents]` optional dependencies (v0.2)

```toml
[project.optional-dependencies]
agents = [
    "anthropic>=0.40.0",
    "mcp>=1.0.0",
    "httpx>=0.27.0",
    "websockets>=12.0",
]
```

Default `pip install` does not pull these. They install only when the operator opts in via `pip install "comfy-moneta-bridge[agents]"`.

### `[dev]` extras additions (v0.2)

```toml
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "pytest-httpx>=0.30",   # NEW: mocks httpx for agent client tests
    "jsonschema>=4.21",     # NEW: validates ToolSpec schemas
]
```

---

## Quickstart (paste-ready)

### Lean install (v0.1 surface only)

```sh
pip install "comfy-moneta-bridge @ git+https://github.com/JosephOIbrahim/comfy-moneta-bridge.git@v0.2.0"

# Terminal A — JSONL tailer (long-running)
bridge tail

# Terminal B — hydrate a session capsule from current Moneta state
bridge hydrate default

# Cross-session semantic recall (requires BRIDGE_EMBEDDER_MODE=bge for real similarity)
bridge recall "abandoned warehouse"
```

### Agent install (v0.2 unlock)

```sh
pip install "comfy-moneta-bridge[agents] @ git+https://github.com/JosephOIbrahim/comfy-moneta-bridge.git@v0.2.0"

# Internal Claude loop against a local ComfyUI on 127.0.0.1:8188
bridge orchestrate "swap the checkpoint to sdxl-turbo" --session demo --interactive

# Stdio MCP server (for Claude Code, Claude Desktop, or any MCP-aware client)
bridge mcp
```

Defaults assume:

| Flag | Default |
|---|---|
| `--comfy-cozy-root` | `G:/Comfy-Cozy` |
| `--moneta-storage` | `~/.comfy-moneta-bridge/moneta/` |
| `--state-dir` | `~/.comfy-moneta-bridge/` |
| `--model` (orchestrate) | `claude-opus-4-7` |
| `--max-steps` (orchestrate) | `12` |
| `COMFYUI_URL` env | `http://127.0.0.1:8188` |

Override any with the corresponding flag or env var.

### Self-test (after clone)

**Lean** — exercises v0.1 paths plus the v0.2 capsule-snapshot and `moneta_config` modules. All `agents/*` tests skip cleanly via `pytest.importorskip`:

```sh
git clone https://github.com/JosephOIbrahim/comfy-moneta-bridge.git
cd comfy-moneta-bridge
pip install -e ".[dev]"
python -m pytest
# Expected: ~85 passed, ~165 skipped (every test that imports anthropic/mcp/httpx skips)
```

**Full** — installs the `[agents]` extras and runs the entire suite:

```sh
pip install -e ".[dev,agents]"
python -m pytest
# Expected: 250 passed, 2 skipped (BGE-only tests skip without sentence-transformers)
```

---

## Demo arc

The v0.1 cold-vs-warm two-session demo arc is fully documented in `demo/shot_list.md` and is unchanged for v0.2. The placeholder `demo/workflow.json` (SDXL txt2img) still needs to be replaced with the actual demo workflow before recording.

A v0.2 cold→warm→agent-orchestrated arc is a **v0.3 candidate**: it would showcase `bridge orchestrate "<goal>"` after the warm session, with the agent loop mutating the warm-state workflow toward a higher quality score, then `bridge hydrate` picking up the mutated workflow into the capsule for the next spawn.

---

## v0.2 limitations (cross-reference)

Full v0.1 list still applies (see `LAUNCH_READY.md` and `README.md` §"v0 limitations"). v0.2-specific limitations:

| # | Limitation | v0.3 remediation |
|---|---|---|
| 1 | **Live ComfyUI smoke unverified.** The end-to-end integration test uses mocked httpx + mocked Anthropic; no actual ComfyUI was driven during v0.2 build. | Run `bridge orchestrate` against a real localhost ComfyUI on 127.0.0.1:8188 and capture the round-trip. |
| 2 | **No demo recording for the agent arc.** | Add `demo/agent_arc.md` shot list mirroring the v0.1 cold→warm format. |
| 3 | **No GitHub Actions CI.** Regression coverage is local pytest only. | Add `.github/workflows/test.yml` running pytest on both lean and `[agents]` installs. |
| 4 | **Single-turn per role in the internal loop.** Each role gets one Anthropic Messages call; multi-turn within a role (e.g., MUTATOR inspecting between mutations) is not supported in v0.2. | If a role's task can't fit in one turn, add an internal loop inside `loop.py:ClaudeRoleDriver.step()`. |
| 5 | **No tool-result feedback to Claude in subsequent role turns.** The transcript carries one-line summaries; full tool-result JSON is not replayed. | Add a `tool_result` block on the next user message so Claude sees structured tool output. |

---

## Verification (after push)

```sh
git ls-remote https://github.com/JosephOIbrahim/comfy-moneta-bridge.git
# TBD post-merge:
#   <merge-sha>   HEAD
#   <merge-sha>   refs/heads/master
#   26508b875a6c24c9aaa77e428c39e3aa6efae4b6   refs/tags/v0.1.0
#   922bc55326d5f7dcc2fd8bda89566eb01fc2ef33   refs/tags/v0.1.0^{}
#   <tag-object-sha>   refs/tags/v0.2.0
#   <merge-sha>   refs/tags/v0.2.0^{}
```

`master` and `v0.2.0^{}` will both point at the PR #1 merge commit. The tag object SHA is filled in here once `git tag -a v0.2.0` runs against `master`.

---

## Build provenance

- **Mission spec (active):** `BRIDGE_BUILD_MISSION_v3_2.md`
- **Mission spec (superseded, retained):** `BRIDGE_BUILD_MISSION_v3_1.md`, `BRIDGE_BUILD_MISSION_v3.md`
- **Runtime constitution:** `AGENTS.md` (re-read per orchestration)
- **Build-time constitution:** `AGENT_COMMANDMENTS.md` (unchanged from v0.1)
- **Pre-flight scout:** `MONETA_API_SCOUT.md` (Phase 0.5a + 0.5b addendum, unchanged)
- **Probe scripts:** `scripts/probe_dimensionality.py`, `scripts/probe_durability.py`, `scripts/benchmark_handle.py` — unchanged.
- **Architecture details:** `docs/architecture.md` — extended with the v0.2 agent layer section.
- **Previous release artifact:** `LAUNCH_READY.md` (v0.1.0, 2026-04-29, preserved unchanged).

---

## What's still ahead (Joe-only)

- Promote `v0.2.0` to a GitHub Release (Releases UI → Draft → choose tag `v0.2.0`). Optionally attach `BRIDGE_BUILD_MISSION_v3_2.md` and `AGENTS.md` as release notes.
- Run the live ComfyUI smoke (PR #1 test plan item 4) against `127.0.0.1:8188` and capture the actual orchestration transcript.
- Run the MCP smoke (PR #1 test plan item 5) — `bridge mcp` + the `mcp` CLI inspector to confirm tool advertisement.
- Replace `demo/workflow.json` placeholder with the actual demo workflow before recording (carry-over from v0.1).
- Consider enabling GitHub Actions CI (`.github/workflows/test.yml` running pytest on lean + `[agents]` installs) so v0.3+ has regression coverage at PR time, not just local.
- Consider editing the GitHub repo description to mention agent-driven workflow manipulation now that v0.2 ships it.
