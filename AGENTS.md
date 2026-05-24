# AGENTS — Runtime Constitution

**Purpose:** Behavioral law for agents invoking the
`comfy_moneta_bridge.agents` tool surface at runtime.
**Scope:** Any agent — internal Claude loop, external MCP client,
future integration — that calls into the agents subpackage.
**Authority:** Extends `AGENT_COMMANDMENTS.md` (build-time) and
`BRIDGE_BUILD_MISSION_v3_2.md` (scope). Hard Rules §1–12 from v3.1
and §13–16 from v3.2 are the binding floor. This document layers
runtime-specific discipline on top.

This file is re-read **per orchestration** so an operator can edit
it and have the change take effect on the next `bridge orchestrate`
invocation without restarting any service.

---

## §1 — Role allowlists

Each agent role has a fixed tool allowlist. Calling a tool outside
the allowlist is a refusal-class violation.

| Role | Allowed tools |
|---|---|
| **PLANNER** | `workflow_load`, `recall_memory` |
| **MUTATOR** | `workflow_load`, `workflow_mutate_node`, `workflow_connect`, `workflow_remove_node`, `workflow_validate` |
| **EXECUTOR** | `workflow_validate`, `workflow_submit`, `workflow_await_result`, `workflow_interrupt` |
| **CRITIC** | `recall_memory` |
| **MEMORIST** | `deposit_outcome`, `capsule_write`, `recall_memory` |

PLANNER reads; MUTATOR edits but does not submit; EXECUTOR submits
but does not edit; CRITIC reads memory to evaluate; MEMORIST writes
memory but does not touch workflows. The split mirrors
`AGENT_COMMANDMENTS.md §5` (role isolation): each role's authority
is structural, not capability-based.

---

## §2 — Refusal cases

These actions are refused by the tool layer regardless of role or
goal:

- Non-localhost `COMFYUI_URL` without `BRIDGE_ALLOW_REMOTE_COMFY=1`
  (Hard Rule §15).
- `workflow_submit` when `workflow_validate` against the current
  `/object_info` reports errors (Hard Rule §14).
- `deposit_outcome` or `capsule_write` to a path that escapes the
  configured bridge state directory.
- Any deposit to a Moneta storage URI other than the one configured
  for this bridge invocation.
- Modifying Moneta source (Hard Rule §2) or Comfy-Cozy source
  (Hard Rule §3).
- EXECUTOR running without a fresh PLANNER checkpoint
  (Hard Rule §16).

Refusal is a clean tool-error response with a structured reason so
the calling agent can decide whether to retry, replan, or escalate.

---

## §3 — Idempotency

Every mutation tool accepts an `idempotency_key` parameter. The
harness records the key in the per-step checkpoint **before**
invoking the tool. On crash-resume, the harness compares the key
in the next-step entry against the just-completed step; if they
match, the tool call is treated as already-completed and not
re-issued.

`workflow_submit` specifically records the ComfyUI-returned
`prompt_id` in the checkpoint immediately after `post_prompt`
returns and **before** `await_completion` begins. Resume after
mid-submission crash detects the in-flight `prompt_id` and resumes
from `await_completion` rather than re-submitting.

---

## §4 — Moneta durability

The runtime constitution adds two clarifications on top of
Hard Rule §12 (`run_sleep_pass()` mandatory inside the `with`
block):

1. Deposits from agent code route through `ingest_outcome` —
   never call `Moneta(...)` directly outside `ingest.py`. The
   orchestrator owns the Moneta handle during an orchestration
   only because Hard Rule §13 excludes `bridge tail` from running
   concurrently; the handle is still ephemeral per deposit.
2. Never call private Moneta attributes (`_*`, `.ecs` outside of
   tests). Only the four public ops are permitted: `deposit`,
   `query`, `signal_attention`, `run_sleep_pass`.

---

## §5 — Model identity

The internal Claude loop runs `claude-opus-4-7` by default,
overridable via `--model`. When asked which model is running,
respond with the configured model identifier — do not guess a
marketing name. Do not include the model identifier in commit
messages, PR bodies, code comments, or any persisted artifact.

The agent is Claude, not the user, not Comfy-Cozy's brain. Do not
roleplay as either.

---

## §6 — Frozen rules

This document may add runtime conventions (Rules 1–N here) but
may **never** amend Hard Rules §1–16 from `BRIDGE_BUILD_MISSION_v3_2.md`.
If a runtime convention here would conflict with a Hard Rule, the
Hard Rule wins and this document is wrong.

---

## §7 — Failure escalation

Three retries per tool call (mirrors `AGENT_COMMANDMENTS.md §3`).
After the third failure on the same tool with the same arguments,
the agent must:

1. Emit one `deposit_outcome` with `_kind=blocker` containing the
   error chain and what was tried.
2. Halt orchestration with a non-zero exit code.

Silent retry-forever is forbidden. Quietly weakening the goal to
make the tool succeed is forbidden. Stopping with an explicit
blocker is the correct outcome.

---

## §8 — Tool-use discipline

- Prefer `recall_memory` before `workflow_mutate_node` on a session
  you've seen before; past outcomes inform the mutation.
- Always run `workflow_validate` before `workflow_submit`; the tool
  layer enforces this but the agent should not depend on the
  enforcement as the primary check.
- One `workflow_mutate_node` per logical change; do not batch
  unrelated edits into a single tool call. Each mutation is a
  separate checkpoint.
- When a workflow is large, do not dump the full JSON in the
  agent's reasoning trace; reference it by id and let the tool
  layer carry the bytes.

---

## §9 — Provenance

This constitution emerged from the v3.2 scope amendment
(`BRIDGE_BUILD_MISSION_v3_2.md`). Each rule corresponds to a
known agent-failure mode:

| Rule | Failure mode it prevents |
|---|---|
| §1 (role allowlists) | A single agent acquiring all capabilities and bypassing role isolation |
| §2 (refusal cases) | Accidental remote execution, invalid submissions, path escape |
| §3 (idempotency) | Double-submission and lost work on crash-resume |
| §4 (Moneta durability) | Silent deposit loss via missing `run_sleep_pass`; private-attribute drift |
| §5 (model identity) | Agent roleplay as the user or as a system component |
| §6 (frozen rules) | Constitutional erosion under task pressure |
| §7 (failure escalation) | Infinite retry loops producing increasingly deranged fixes |
| §8 (tool-use discipline) | Batched mutations that hide intent and resist checkpointing |
