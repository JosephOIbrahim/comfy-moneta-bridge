# Demo shot list — cold-vs-warm session arc

The v0 demo is a two-run arc that visualizes the bridge in action. Run 1
is "cold" (no Moneta state, fresh process). Run 2 is "warm" (the bridge
has hydrated a session capsule from Run 1's outcomes).

## Pre-recording setup

- Three terminal windows tiled side-by-side:
  - **TERM A** — Comfy-Cozy agent
  - **TERM B** — `bridge tail` running continuously
  - **TERM C** — `bridge hydrate` invoked between runs
- ComfyUI server already running on the GPU.
- `~/.comfy-moneta-bridge/` cleaned out so we start with no cursor.
- No `sessions/default.json` in `G:/Comfy-Cozy/sessions/`.
- The demo workflow loaded in ComfyUI (replaces `demo/workflow.json`
  with Joe's actual one).

## Shots

### Shot 1 — Frame the architecture (15 s narration)

> "Comfy-Cozy is an autonomous agent for ComfyUI. Moneta is a cognitive
> substrate. The bridge is what wires them together — a thin file-watcher
> on Comfy-Cozy's outcomes, an ephemeral writer to Moneta. No code-level
> coupling, no process injection."

Visual: project repo tree side-by-side. Highlight `comfy_moneta_bridge/`
seven modules.

### Shot 2 — Start `bridge tail` (5 s)

TERM B:
```sh
bridge tail
# bridge tail watching G:/Comfy-Cozy/sessions
```

> "The bridge is now watching."

### Shot 3 — Run 1, cold start (~60 s)

TERM A:
```sh
agent run
```

> "Cold start. No `AUTO_LOAD_SESSION`. The agent has no prior context."

Prompt the agent: *"abandoned warehouse, dramatic lighting"*. Let it
run a few iterations. Each `record_outcome` writes a line to
`sessions/default_outcomes.jsonl`. TERM B emits an INFO log per ingest.

Visual cue: highlight TERM B's lines as they arrive.

### Shot 4 — Hydrate the capsule (10 s)

TERM C, after Run 1 completes:
```sh
bridge hydrate default
# ✓ Wrote sessions/default.json
# Note: Comfy-Cozy must be (re)started for this to take effect. ...
# Run with: AUTO_LOAD_SESSION=default agent run
```

> "Hydrate. The bridge queries Moneta with the `default` session vector,
> filters to that session, sorts chronologically, writes a `schema_v2`
> session capsule."

Cut to: open `sessions/default.json` in an editor. Show the `notes`
array — observations from `vision_notes`, preferences from
`key_params + quality_score`.

### Shot 5 — Run 2, warm start (~60 s)

TERM A:
```sh
AUTO_LOAD_SESSION=default agent run
```

> "Warm start. The agent loads the session capsule. It now sees its own
> prior preferences and observations — the parameters that landed quality
> 0.9 last time, the vision notes that mattered."

Prompt with the *same prompt*: *"abandoned warehouse, dramatic lighting"*.

Highlight: the agent reaches good quality faster. Fewer iterations to
the same `record_outcome` quality threshold. The contrast is the demo.

### Shot 6 — Wrap (15 s)

> "v0 ships with documented limitations: a performance ceiling at ~1000
> deposits per session, synthetic vectors instead of semantic ones,
> survivorship bias on outcomes. v1 unlocks each. But the architecture
> is in: file-watcher, deterministic vectors, ephemeral handles,
> `run_sleep_pass()` per deposit. Three modules, four-op API, no source
> modifications to either side."

End frame: README's architecture ASCII diagram.

## Recording cues

- TERM B should be visible throughout shots 3–5 to make the "ingest
  happens live" point.
- A lower-third overlay during shot 4 saying "schema_v2 capsule" with
  an arrow at the file path.
- Pause briefly before shot 5 so the viewer registers the warm start
  is happening with `AUTO_LOAD_SESSION` set.
- Total target length: ~3 minutes.

## Things that can go wrong on stage

- ComfyUI runs out of VRAM mid-run. *Mitigation*: pre-warm the model
  before recording.
- `bridge tail` misses an event due to filesystem-watch latency on
  Windows. *Mitigation*: brief pause between `record_outcome` calls
  (Comfy-Cozy already has this).
- The default `sessions/` directory layout might differ. *Mitigation*:
  pass `--comfy-cozy-root` explicitly if needed.
