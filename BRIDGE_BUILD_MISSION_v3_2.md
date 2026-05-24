# BRIDGE BUILD MISSION — v3.2

**Status:** Active scope amendment.
**Supersedes:** `BRIDGE_BUILD_MISSION_v3_1.md` §"Out of scope" clause for
workflow manipulation and MCP server interception. All other v3.1 scope
remains in force.
**Provenance:** v3.1 is retained unchanged; v3.2 is layered on top, in
the same pattern as v3 → v3.1 (an empirically-grounded amendment, not a
rewrite). The amendment is grounded in the user-confirmed need for
agent-driven workflow manipulation that v3.1's frozen pass-through
posture explicitly excluded.

---

## Why v3.2 exists

v3.1 codified the bridge as a thin, frozen memory pass-through with
explicit Hard Rules 1–12 protecting Moneta source, Comfy-Cozy source,
and persistence semantics. The mission's "Out of scope" list named
workflow manipulation and MCP server interception as forbidden.

Two empirical facts make the v3.1 posture worth amending:

1. **The bridge already extended scope once via opt-in modes.** Commit
   `7033b97 feat: add BGE-small encoder as opt-in v1 path
   (mode-switched)` shipped BGE-small despite v3.1 saying "synthetic
   deterministic only". The precedent: scope extensions are acceptable
   when they are **opt-in, mode-switched, and respect Hard Rules
   1–12**. v3.2 formalizes that pattern.

2. **Agents need a workflow surface.** External agents (and an
   optional internal Claude loop) cannot meaningfully drive ComfyUI
   without primitives to load, mutate, validate, and submit
   workflows. The capsule's hardcoded-null workflow block
   (`capsule.py:65-72`) was a design choice for v0; v0.2 needs it
   conditional on whether a workflow snapshot was deposited during
   the session.

v3.2's scope addition is **strictly additive** and lives entirely in
a new optional subpackage `comfy_moneta_bridge/agents/` gated by a
`[agents]` extras-install.

---

## Scope additions (v3.2)

| # | Addition | Lives in |
|---|---|---|
| A | ComfyUI HTTP+WS client (POST /prompt, /ws, /object_info, /interrupt, /queue DELETE). | `agents/client.py` |
| B | Workflow graph model + mutation primitives (load, dump, add_node, set_input, connect, remove_node, validate). | `agents/workflow.py` |
| C | Tool surface with parity between Anthropic SDK schemas and MCP `Tool` registrations. | `agents/tools.py` |
| D | Long-running async harness with crash-safe per-step checkpoints. | `agents/harness.py` |
| E | Runtime constitution loader. | `agents/constitution.py` + `AGENTS.md` |
| F | Role prompts and turn-taking (PLANNER/MUTATOR/EXECUTOR/CRITIC/MEMORIST). | `agents/roles.py` |
| G | Single-goal orchestrator that emits outcomes through the existing ingest pipeline. | `agents/orchestrator.py` |
| H | Stdio MCP server. | `agents/mcp_server.py` |
| I | Internal Anthropic-SDK Claude loop. | `agents/loop.py` |
| J | CLI commands `bridge orchestrate` and `bridge mcp` (lazy-imported so non-agent installs are unaffected). | `cli.py` |
| K | Capsule workflow block populated from latest `_kind=workflow_snapshot` deposit (signature unchanged; logic extends `write_capsule`'s existing filter loop). | `capsule.py` |

**Out of scope for v0.2 (deferred to a future version):**
- Remote (non-localhost) ComfyUI execution as default (must opt in per Rule §15).
- Long-running multi-session orchestration daemon.
- Replacing Comfy-Cozy's `agent mcp` server — `bridge mcp` is a
  **second, complementary** MCP surface, not a replacement.
- Direct modification of Moneta or Comfy-Cozy source (Hard Rules
  §2 and §3 remain in force).

---

## Hard Rules 13–16 (new)

These extend, do not amend, Rules 1–12. Rule 1–12 remain unchanged.

### §13 — Orchestration excludes Tail

`bridge orchestrate` and `bridge mcp` refuse to start if a
`{state_dir}/tail.pid` file is present. `bridge tail` refuses to
start if `{state_dir}/orchestrate.pid` is present. The two
processes share Moneta URI storage and would deadlock on
`MonetaResourceLockedError` (per Rule §6). The PID-file mutex
serializes them at the process level.

**Why empirically grounded:** the v0 `bridge tail` + `bridge
hydrate` collision was the original driver of Rule §6. Adding a
third Moneta-handle-opening process without a mutex would
reintroduce the failure mode that Rule §6 sidesteps.

### §14 — Validate against `/object_info` before submit

Every `workflow_submit` tool call first runs `workflow_validate`
against a freshly-fetched (or cached-per-orchestration)
`/object_info` map. If validation reports any errors, submission
is refused and the agent must mutate the workflow to fix the
errors before retrying.

**Why empirically grounded:** ComfyUI's executor surfaces
unknown-node-type and missing-required-input errors as opaque
500s with unstructured tracebacks. Local validation against the
authoritative schema map turns those into structured tool-error
responses the agent can react to.

### §15 — Localhost ComfyUI by default

`ComfyClient.__init__` defaults `base_url` to
`http://127.0.0.1:8188`. A non-localhost `COMFYUI_URL`
environment variable requires `BRIDGE_ALLOW_REMOTE_COMFY=1` set
explicitly; without it, construction raises before any network
call.

**Why empirically grounded:** an agent that accidentally
submits to a production ComfyUI consumes that production
instance's GPU queue, possibly with mutated workflows. The
explicit opt-in is the irreversible-transition gate
(Commandment §8) for that risk.

### §16 — Planner output requires checkpoint before Executor

The harness writes a `CheckpointStore` entry at every role
transition. EXECUTOR refuses to run if no fresh PLANNER
checkpoint exists for the current goal. The `--interactive`
flag adds an additional human-acknowledgement step at the
same transition.

**Why empirically grounded:** EXECUTOR is the role with
side-effects (it submits to ComfyUI). Without a checkpointed
plan, a crashed orchestration would resume with no record of
why a workflow is the way it is — making resume indistinguishable
from a fresh-start corruption. The checkpoint is the resumable
artifact; interactive mode is the optional human-in-the-loop
overlay.

---

## §7 amendment (clarifying, not weakening)

The original §7 reads: "No ML / embedding library — v0 demo arc is
same-session; semantic similarity isn't the operation needed."

v3.2 clarifies:

> ML and embedding libraries remain forbidden in core dependencies.
> LLM SDKs (`anthropic`, `mcp`, `httpx`, `websockets`) are permitted
> **exclusively** in the `agents/` subpackage and are gated by the
> `[agents]` optional-dependencies extra. A default
> `pip install comfy-moneta-bridge` continues to install only
> stdlib + `watchfiles` + `typer` + `moneta`, preserving the lean
> core. BGE-small remains separately gated by `[embeddings]` per
> the v3.1 amendment that shipped it.

This is consistent with the `[embeddings]` extras pattern v3.1
already accepted for BGE-small.

---

## Backwards-compatibility contract

v3.2 does not change the behavior of any v3.1 code path. Specifically:

1. All 75 v3.1 tests continue to pass unchanged.
2. `write_capsule` signature is unchanged. When no `_kind=workflow_snapshot`
   deposit exists for a session, the workflow block remains byte-equal
   to `_empty_workflow_block()`. The existing
   `test_schema_v2_workflow_stub` test (`tests/test_capsule.py:267-278`)
   stays as the regression canary.
3. `bridge tail`, `bridge hydrate`, `bridge recall` continue to work
   without `[agents]` extras installed.
4. `ingest_outcome`'s `schema_version == 1` strict check is preserved.
   Agent-emitted "workflow snapshot" deposits are tagged with
   `schema_version=1` plus a `_kind` discriminator so they flow
   through the existing ingest path unchanged.

---

## Considered and Rejected

- **Add a `workflow_payload` kwarg to `write_capsule`.** Rejected.
  Would have required Comfy-Cozy or some caller to know the workflow
  at hydrate time; the bridge's whole posture is that the workflow
  flows through deposits, not direct caller arguments. Extracting
  from query results aligns with the existing PRNG-collision filter
  loop and keeps the signature stable.
- **Internal Claude loop calls the MCP server over stdio.** Rejected.
  Adds subprocess management + JSON-RPC overhead for in-process calls
  that can dispatch directly. Both surfaces share the same tool
  functions in `tools.py`; the MCP server is a thin wrapper for
  external consumers.
- **Long-running orchestration daemon.** Rejected for v0.2. The MCP
  server is the right shape for "external agent drives multiple
  orchestrations"; a daemon would duplicate that with extra state.
- **Hidden amendment of Hard Rules 1–12.** Forbidden. v3.2 extends
  with Rules 13–16; Rules 1–12 remain word-for-word as v3.1 codified
  them. The §7 amendment is a clarification of scope (LLM SDKs are
  not ML libraries) and matches the existing `[embeddings]` precedent.
