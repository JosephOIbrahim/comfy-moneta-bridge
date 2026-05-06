# LAUNCH READY — comfy-moneta-bridge v0.1.0

**Pushed:** 2026-04-29
**Tag:** `v0.1.0` (annotated, object `26508b87`) on commit `922bc55`
**Repo URL:** https://github.com/JosephOIbrahim/comfy-moneta-bridge
**Default branch:** `master`
**Visibility:** public
**No GitHub release created** — v0 launch decision is yours; promote `v0.1.0` to a Release manually via the GitHub Releases UI when ready.

---

## Pinned dependency versions

### Moneta — `v1.2.0-rc2`

- Pinned in `pyproject.toml` via direct git URL:
  ```toml
  "moneta @ git+https://github.com/JosephOIbrahim/Moneta@v1.2.0-rc2",
  ```
- Tag points at Moneta commit `76da067` ("Add free-threading guard to AttentionLog").
- Phase 0.5b dimensionality probe + handle benchmark were run against this exact SHA. Performance numbers in `README.md` and `docs/architecture.md` correspond to this version.

### Comfy-Cozy — frozen at scout HEAD `8827772`

- **Not a Python dependency.** The bridge reads Comfy-Cozy state via the filesystem (`sessions/*_outcomes.jsonl` for ingest; writes `sessions/{name}.json` for hydrate). No imports cross.
- Scout HEAD: `8827772692da9b690307a05ef2d820e3dd65df40` ("Add GitHub Sponsors configuration and acknowledgments") on `main` branch of `G:/Comfy-Cozy`.
- The `schema_version=2` translation in `capsule.py` was validated against `G:/Comfy-Cozy/agent/memory/session.py` at this SHA.
- Bridge stays compatible as long as Comfy-Cozy's outcome JSONL contract (`schema_version=1`) and session capsule contract (`schema_version=2`) remain stable.

---

## Quickstart (paste-ready)

```sh
# Install (with all dev deps for tests)
pip install "comfy-moneta-bridge[dev] @ git+https://github.com/JosephOIbrahim/comfy-moneta-bridge.git@v0.1.0"

# Or runtime-only
pip install "comfy-moneta-bridge @ git+https://github.com/JosephOIbrahim/comfy-moneta-bridge.git@v0.1.0"

# Terminal A — JSONL tailer (long-running)
bridge tail

# Terminal B — hydrate a session capsule from current Moneta state
bridge hydrate default

# One-shot hydrate + spawn Comfy-Cozy with AUTO_LOAD_SESSION=default
bridge hydrate default --launch
```

Defaults assume:

| Flag | Default |
|---|---|
| `--comfy-cozy-root` | `G:/Comfy-Cozy` |
| `--moneta-storage` | `~/.comfy-moneta-bridge/moneta/` |
| `--state-dir` | `~/.comfy-moneta-bridge/` |

Override any with the corresponding flag.

### Self-test (after install, from a clone of the repo)

```sh
git clone https://github.com/JosephOIbrahim/comfy-moneta-bridge.git
cd comfy-moneta-bridge
pip install -e ".[dev]"
python -m pytest
# 49 passed
```

---

## Demo arc

The cold-vs-warm two-session demo arc is fully documented in `demo/shot_list.md`. Three-minute target. Outline:

1. **Run 1 (cold)** — fresh Comfy-Cozy process, no `AUTO_LOAD_SESSION`. Prompt: *"abandoned warehouse, dramatic lighting"*. Let the agent iterate; each `record_outcome` writes a JSONL line that the bridge ingests live (visible in TERM B).
2. **`bridge hydrate default`** — write the schema_v2 capsule from accumulated Moneta state.
3. **Run 2 (warm)** — fresh Comfy-Cozy process with `AUTO_LOAD_SESSION=default`. Same prompt. The agent inherits its prior parameter preferences and observation notes from Moneta and reaches good-quality output faster.

The contrast between Run 1 and Run 2 is the demo. Recording cues, narration, and contingency notes (VRAM exhaustion, watch-event latency, default sessions/ layout) all in the shot list.

The placeholder `demo/workflow.json` (SDXL txt2img) needs to be replaced with the actual demo workflow before recording.

---

## v0 limitations (cross-reference)

Full list in `README.md` §"v0 limitations". Each has a v1 remediation:

| # | Limitation | v1 remediation |
|---|---|---|
| 1 | **Survivorship bias** on Comfy-Cozy outcomes | Capture failed runs and abandoned goals |
| 2 | **Cursor-loss → some duplicates** on replay | Bridge-side dedupe key OR Moneta-side idempotency |
| 3 | **Synthetic vectors** only — no semantic similarity | Real embeddings (sentence-transformers or remote API) |
| 4 | **Performance ceiling at WAL ≈ 1000** (Phase 0.5b) | Batched-deposit layer (flush every N or every T seconds) |

---

## Verification (after push)

```sh
git ls-remote https://github.com/JosephOIbrahim/comfy-moneta-bridge.git
#   922bc55326d5f7dcc2fd8bda89566eb01fc2ef33   HEAD
#   922bc55326d5f7dcc2fd8bda89566eb01fc2ef33   refs/heads/master
#   26508b875a6c24c9aaa77e428c39e3aa6efae4b6   refs/tags/v0.1.0
#   922bc55326d5f7dcc2fd8bda89566eb01fc2ef33   refs/tags/v0.1.0^{}
```

`master` and `v0.1.0^{}` both point at `922bc55`. Tag object SHA is `26508b87` (annotated, with the v0.1.0 release notes embedded).

---

## Build provenance

- **Mission spec (active):** `BRIDGE_BUILD_MISSION_v3_1.md`
- **Mission spec (superseded, retained):** `BRIDGE_BUILD_MISSION_v3.md`
- **Pre-flight scout:** `MONETA_API_SCOUT.md` (Phase 0.5a + 0.5b addendum)
- **Constitution:** `AGENT_COMMANDMENTS.md`
- **Probe scripts:** `scripts/probe_dimensionality.py`, `scripts/probe_durability.py`, `scripts/benchmark_handle.py` — all reproducible against any Moneta install on `PYTHONPATH`.
- **Architecture details:** `docs/architecture.md` — including "Why deposit alone doesn't persist" with the empirical durability matrix.

---

## What's still ahead (Joe-only)

- Promote `v0.1.0` to a GitHub Release (Releases UI → Draft → choose tag `v0.1.0`). Optionally attach the `MONETA_API_SCOUT.md` addendum and `docs/architecture.md` as release notes.
- Replace `demo/workflow.json` with the actual demo workflow before recording.
- Consider editing the GitHub repo description (currently "Comfy_Moneta_Bridge wiring for Comfy-Cozy powered by Moneta", set at repo creation): `gh repo edit JosephOIbrahim/comfy-moneta-bridge --description "Wires Comfy-Cozy experience output into Moneta cognitive substrate"`.
