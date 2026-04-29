# SCOUT: ComfyUI Extension API for the comfy-moneta-bridge Panel

**Mission:** Phase 0.5 — design-input scout for a panel-based ComfyUI extension that surfaces bridge + Comfy-Cozy status, lets the user select a session and trigger hydrate, integrates with ComfyUI's existing design language, and is installable via ComfyUI Manager.
**Mode:** Read-only inventory pass — no modifications.
**Repo state captured:** 2026-04-29.
**ComfyUI source HEAD:** `/c/ComfyUI/` at `comfyanonymous/ComfyUI` `361b9a82` (live install at `/g/COMFY/ComfyUI/`; live install carries a `web/extensions/` shim, the upstream source clone does not — modern ComfyUI delegates the frontend to a separately-pip-installed `comfyui-frontend-package`).
**Reference custom_nodes inventoried:** `/g/COMFYUI_Database/Custom_Nodes/` (60 installed extensions); deep-dives on Joe's `comfy-cozy-panel`, `comfy-cozy-ui`, plus `comfyui-3d-viewport-bridge` and `ComfyUI-Manager`.
**Scope:** ComfyUI's extension API and packaging surface only. Moneta and Comfy-Cozy unmodified; bridge code unmodified.

---

## [1/8] Where ComfyUI extensions live

**On disk:**

- **One directory per extension.** Path: `<comfyui_install>/custom_nodes/<extension_name>/`. ComfyUI startup walks this directory and imports each subdirectory as a Python package.
- **Live install on this machine:** `G:/COMFY/ComfyUI/custom_nodes/`.
- **Custom-node store on this machine:** `G:/COMFYUI_Database/Custom_Nodes/` — the canonical install root for this user (60 installed nodes; mix of node packs and UI-only extensions).
- **Disabled extensions** are conventionally renamed with a trailing `.disabled` (e.g. `ComfyUI-3D-Pack.disabled`); ComfyUI skips directories whose name matches that pattern.

**Required structure for a UI-only extension** (the shape the bridge panel will use):

```
custom_nodes/<extension_name>/
  __init__.py             # exports NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS, WEB_DIRECTORY
  server/                 # optional: aiohttp routes, lazy-imported brain wiring
    __init__.py
    routes.py
  web/                    # the WEB_DIRECTORY
    js/                   # ES modules, served at /extensions/<extension_name>/js/*
    css/                  # static stylesheets
  pyproject.toml          # required for ComfyUI Registry publishing (see §7)
  README.md               # Manager listing reads this
  requirements.txt        # optional, ComfyUI installs at extension load
  example_workflows/      # optional, autodiscovered by /workflow_templates endpoint
  locales/<lang>/main.json # optional, autodiscovered for i18n (commands.json, settings.json)
```

**Install methods, ranked by current ecosystem prevalence:**

1. `git clone <repo> custom_nodes/<name>/` — primary method, what ComfyUI Manager performs under "git-clone" install_type.
2. `comfy node install <name>` (`comfy-cli` against the official ComfyUI Registry).
3. ComfyUI Manager UI → Install via the right-rail panel (driven by the JSON registry described in §7).
4. Manual zip extraction — fallback, fragile.

---

## [2/8] Extension registration mechanism

### Python side — `__init__.py` exports

ComfyUI imports `<custom_nodes>/<name>/__init__.py` and reads three module-level names:

```python
NODE_CLASS_MAPPINGS = {}        # mapping[str, NodeClass] — graph nodes; empty for UI-only
NODE_DISPLAY_NAME_MAPPINGS = {} # mapping[str, str]       — UI labels for graph nodes
WEB_DIRECTORY = "./web"         # relative path; served at /extensions/<name>/...
```

Confirmed pattern from Joe's `comfy-cozy-panel/__init__.py` (lines 7–10):

```python
NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}
WEB_DIRECTORY = "./web"
__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
```

When `WEB_DIRECTORY` is set, ComfyUI scans every `*.js` file (recursively) inside that directory and emits a `<script type="module" src="...">` tag per file at frontend load time. The files are served from `/extensions/<extension_name>/<relative_path>`.

### Python side — server routes (aiohttp)

ComfyUI's HTTP layer is `server.py`'s `PromptServer` (singleton on `aiohttp.web.Application`). Custom nodes attach routes by importing the singleton and decorating with the standard `routes.get/post/...` API. Registration must happen **before** `PromptServer.start()` runs, which means at extension import time. Confirmed pattern from `comfy-cozy-panel/server/routes.py`:

```python
def setup_routes():
    try:
        from server import PromptServer
        routes = PromptServer.instance.routes
    except Exception:
        log.debug("PromptServer not available — routes not mounted")
        return

    @routes.get("/comfy-cozy/health")
    async def health(request):
        ...

    @routes.post("/comfy-cozy/some-action")
    async def some_action(request):
        ...
```

**WebSocket pattern**, also from comfy-cozy-ui (`server/routes.py` + `server/ws_bridge.py`): `routes.get("/superduper/ws")` returns a `web.WebSocketResponse()`; the handler awaits messages, calls into the agent, streams events back via `ws.send_json(...)`. Same aiohttp surface as the rest of ComfyUI.

`setup_routes()` is invoked from `__init__.py` at import time, wrapped in a `try/except` so a missing dependency (e.g., `agent` package not on `sys.path`) does not abort ComfyUI startup — the extension just skips its server side and logs.

### JavaScript side — `app.registerExtension` and `app.extensionManager`

The frontend is loaded by `comfyui-frontend-package` (a PyPI package versioned in ComfyUI's `requirements.txt` as `comfyui-frontend-package==<x.y.z>`; `app/frontend_management.py:38` resolves the installed version). The frontend exposes a global `app` object imported by extensions:

```js
import { app } from "../../../scripts/app.js";
// path is fixed: extensions are served from /extensions/<name>/js/...
// scripts/ is at /scripts/, hence three ".." segments
```

Two registration APIs coexist:

- **Legacy** — `app.registerExtension({ name, init?, setup?, beforeRegisterNodeDef?, nodeCreated?, ... })` — for canvas/node hooks (custom widgets, node-graph behaviors). Confirmed at comfy-cozy-ui's `web/js/node_fx.js:100`. Still fully supported.
- **Modern (PrimeVue era)** — `app.extensionManager.register*({...})` — for *UI surfaces* outside the canvas (sidebar tabs, top-bar menus, command palette entries, settings sections, bottom panels). Confirmed at comfy-cozy-ui's `web/js/sidebar.js:840`:

  ```js
  app.extensionManager.registerSidebarTab({
    id: "superduper",
    icon: "pi pi-comments",       // PrimeIcons class string
    title: "Comfy Cozy",
    tooltip: "Comfy Cozy AI Co-pilot",
    type: "custom",
    render(el) {
      // build DOM into el; el is a plain HTMLElement
    },
  });
  ```

The bridge panel will use **`registerSidebarTab`** for its primary UI and **`registerExtension`** for any optional canvas overlays (e.g., highlighting nodes that produced a recent outcome).

---

## [3/8] UI extension surfaces

ComfyUI's modern frontend is a Vue 3 app. The extension manager exposes several discrete surfaces; each is a different DOM region the user can reach:

| Surface | API | Persistence | Use for the bridge |
|---|---|---|---|
| **Left sidebar tab** | `app.extensionManager.registerSidebarTab({id, icon, title, tooltip, type, render})` | Per-user tab order, can be hidden via Settings | **Primary** — bridge status + session selector + hydrate trigger lives here |
| **Right sidebar tab** | Same API; ComfyUI may default new tabs to one rail or the other | Same as left | Secondary; not needed for v0 |
| **Bottom panel** | `app.extensionManager.registerBottomPanel({...})` (per usage in other extensions; not personally call-site verified in this scout) | Same | Optional: dense status / log readout if sidebar gets crowded |
| **Top menu / command palette** | `app.extensionManager.registerCommand({id, label, function})` | Discoverable via `Ctrl+~` (?) command palette | Secondary entry point: "Hydrate session…" command |
| **Settings panel section** | `app.registerExtension({ settings: [{id, name, type, defaultValue, ...}] })` (legacy API still hosts settings) | Persisted in user settings JSON | Bridge config: `--moneta-storage`, `--comfy-cozy-root`, `--state-dir` |
| **Canvas (legacy)** | `app.registerExtension({ beforeRegisterNodeDef, nodeCreated, ... })` | n/a | Optional: visual cue on nodes that produced a recently-ingested outcome |

**Why sidebar over inline-on-canvas:** the bridge's primary UI is *modal-ish work* (pick session, click hydrate, watch status) that competes with the user's workflow if mounted on the canvas. comfy-cozy-ui already establishes "sidebar tab is the right home for cognitive co-pilot UIs" in this codebase. Convention match: same pattern.

**Sidebar tab `type`:** observed `type: "custom"` in `comfy-cozy-ui`. The frontend supports several types (custom, list, command); custom gives full control over the rendered DOM (the function's job is to build into `el`). Other types are higher-level: pre-shaped panels with built-in search, filters, etc.

**Icon library:** PrimeIcons (`pi pi-<name>` class strings). See https://primefaces.org/primeicons/ for the full set. comfy-cozy-ui uses `pi pi-comments`. For the bridge panel a fitting candidate is `pi pi-link`, `pi pi-database`, `pi pi-bolt`, or `pi pi-history` — final pick is a design call, not a scout finding.

---

## [4/8] JS/TS API for adding UI elements

The `app` object's full extension API is documented in the comfyui-frontend-package source (installed under the active venv's `site-packages/comfyui_frontend_package/...`). For this scout I anchored on the call sites used by the two Joe-authored reference extensions.

### Mounting into a sidebar tab

The `render(el)` function gets a plain `HTMLElement`; you populate it with whatever DOM structure you want. comfy-cozy-ui builds a header/messages/readbar/input shell with vanilla DOM and event listeners (no Vue/React):

```js
function buildSidebar(el) {
  el.style.height = "100%";
  el.style.display = "flex";
  el.style.flexDirection = "column";
  el.innerHTML = `
    <div class="sd-header">...</div>
    <div class="sd-messages" id="sd-messages" role="log" aria-live="polite">...</div>
    ...
  `;
  // attach event handlers, open WebSocket, etc.
}
```

You can also mount a Vue / React / Solid app into `el` if you bring your own framework — ComfyUI's frontend doesn't constrain that — but the reference extensions stay vanilla.

### Talking to the canvas

The `app` object exposes the live ComfyUI canvas:

| Operation | API |
|---|---|
| Read graph state | `app.graph.serialize()` (returns the UI-format graph JSON) |
| Read API-format workflow | `await app.graphToPrompt()` → `{ workflow, output }`; `output` is the API JSON |
| Get a node by id | `app.graph.getNodeById(nodeId)` |
| Listen to graph mutations | `app.graph.onAfterChange = function() {...}` (chain-call previous handler) |
| Listen to executor events | `import { api } from "../../../scripts/api.js"; api.addEventListener("executed", ...)`. Other useful events include `"executing"`, `"execution_start"`, `"execution_error"`, `"progress"` |
| Center / select a node | `app.canvas.deselectAllNodes()`, `app.canvas.selectNode(node)`, `app.canvas.centerOnNode(node)` |

For the bridge, the canvas API is mostly **not needed** — the panel reads from bridge state (cursor, recent outcomes) and Moneta state (session capsule preview), neither of which is canvas-derived. Canvas integration is an optional v1 polish: e.g., when the user hovers a recent outcome in the panel, briefly highlight the workflow nodes that produced it (the `nodes_touched` pattern from comfy-cozy-ui).

### Talking to the bridge backend

Two channels, both already established in the reference extensions:

- **HTTP fetch** to bridge-mounted aiohttp routes:
  ```js
  const r = await fetch("/comfy-moneta-bridge/status");
  const data = await r.json();
  ```
- **WebSocket** for status streaming:
  ```js
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}/comfy-moneta-bridge/ws`);
  ws.onmessage = (evt) => { const data = JSON.parse(evt.data); /* ... */ };
  ```

For the bridge panel's v0, **HTTP is sufficient** — status updates are coarse (cursor position, last ingest timestamp, session list) and don't need streaming. WebSocket is a v1 candidate when live ingest tail ↔ panel becomes desirable.

---

## [5/8] ComfyUI's existing design tokens

ComfyUI exposes its theme as **CSS custom properties on `:root`**, set by the frontend at boot and switched on theme change. Referencing these instead of hardcoding hex values means the bridge panel automatically follows light/dark mode and respects user theme overrides.

Confirmed token set (from comfy-cozy-ui's `web/css/superduper.css`, which aliases native vars to namespaced names — the `var(--<native>)` references are the canonical token list):

### Surface / chrome

| Native CSS var | Role |
|---|---|
| `--bg-color` | Outermost background (canvas viewport behind everything) |
| `--comfy-menu-bg` | Sidebar / panel surface (the bridge's panel background) |
| `--button-surface` | Default button / chip surface |
| `--button-active-surface` | Pressed / selected button surface |
| `--comfy-input-bg` | Input field / textarea fill |

### Text

| Native CSS var | Role |
|---|---|
| `--input-text` | Primary readable text |
| `--descrip-text` | Secondary / muted text (descriptions, labels) |
| `color-mix(in srgb, var(--descrip-text) 70%, transparent)` | Tertiary / faded text (idiom from comfy-cozy-ui) |

### Borders

| Native CSS var | Role |
|---|---|
| `--border-color` | Standard border / divider |
| `--border-subtle` | Lighter border for nested separations |

### Accent

| Native CSS var | Role |
|---|---|
| `--accent-primary` | Primary brand accent — the "ComfyUI blue" the user already sees |
| `--accent-background` | Accent surface (selected tab, focused input outline) |

### Semantic colors

These are domain-specific and **not** themed-by-default — they're the slot/port colors users associate with data types:

```
CLIP             #FFD500      CONDITIONING     #FFA931
CLIP_VISION      #A8DADC      CONTROL_NET      #6EE7B7
IMAGE            #64B5F6      LATENT           #FF9CF9
MASK             #81C784      MODEL            #B39DDB
STYLE_MODEL      #C2FFAE      VAE              #FF6E6E
NOISE            #B0B0B0      GUIDER           #66FFFF
SAMPLER          #ECB4B4      SIGMAS           #CDFFCD
```

Source: `comfy-cozy-ui/web/js/tokens.js`. Use the helper `slotColorForNode(classType)` to map a node's `class_type` string to its color. Useful for the bridge if it ever shows per-node lineage.

### Typography & spacing (Joe's house style, not ComfyUI core)

The bridge panel should match Joe's existing co-pilot extensions for visual continuity:

```
Type scale (px):  11 / 12 / 13 / 14 / 16 / 18 / 20
Spacing base:     4px (var(--space-1) = 4 ... --space-8 = 32)
Sans font:        Inter, weights 400/500/600
Mono font:        JetBrains Mono, weights 400/500/600
Motion:           cubic-bezier(0.16, 1, 0.3, 1) "ease-out-expo", durations 150ms / 300ms
```

Both font families load via `<link rel="stylesheet" href="https://fonts.googleapis.com/css2?...">` injected by the sidebar.js entry — convention is to load fonts from JS (deferred) rather than CSS (blocking). External font CDN: acceptable for v0; ship-with-extension is a v1 candidate.

**Convention match:** the bridge panel's CSS should namespace its classes (e.g., `cmb-` prefix for "comfy-moneta-bridge"), define its tokens by aliasing the same native vars (`--cmb-surface-1: var(--comfy-menu-bg)`), and adopt the same type scale + spacing base. This gives users the same visual language as comfy-cozy-ui.

---

## [6/8] Reference extensions — conventions to match

Three picks, in decreasing order of relevance to the bridge panel:

### 1. `comfy-cozy-ui` — the primary reference

**Path:** `G:/COMFYUI_Database/Custom_Nodes/comfy-cozy-ui/` (symlinked into `G:/Comfy-Cozy/ui/`).
**Why it's the reference:** Same author, same target audience (Joe's autonomous-agent co-pilot), same architectural style as what the bridge panel will be. Establishes:

- Sidebar-tab registration pattern (`registerSidebarTab` with `type: "custom"`, render-into-element).
- WebSocket-on-PromptServer pattern (`/superduper/ws`, lazy `_ensure_brain()` import for cold load).
- Native CSS-var aliasing pattern (theme reactivity without hardcoding hex).
- Vanilla DOM construction inside `render(el)` — no framework dependency.
- Event-on-document for cross-module canvas interaction (`document.dispatchEvent(new CustomEvent("superduper:node_touch", {detail: {...}}))`).
- Per-user readability prefs persisted to `localStorage` (font size / alignment / mode).
- Empty `NODE_CLASS_MAPPINGS` + `WEB_DIRECTORY = "./web"` shape.

**What to copy verbatim:** the `__init__.py` shape, the `setup_routes()` aiohttp pattern, the CSS-var aliasing scheme, the vanilla-DOM "build into `el`" idiom, the `localStorage`-backed prefs.

**What to NOT copy:** the WebSocket complexity (overkill for v0 bridge — HTTP is enough), the PrimeIcon `pi pi-comments` (pick a different icon so the two tabs are distinguishable in the same install), the `_ensure_brain()` lazy-import (the bridge already lives at install time; no late binding needed).

### 2. `comfy-cozy-panel` — the headless companion pattern

**Path:** `G:/COMFYUI_Database/Custom_Nodes/comfy-cozy-panel/` (symlinked into `G:/Comfy-Cozy/panel/`).
**Why it's relevant:** Demonstrates a **headless** custom node — no DOM mounted, but a JS module that runs on every page load and mediates between the canvas and a backend. Serves as a model for *if* the bridge needs canvas-side wiring without a sidebar (e.g., a small badge on the executor toolbar showing "ingest active"). Probably **not** needed for v0; the sidebar carries everything.

### 3. `comfyui-3d-viewport-bridge` — third-party canvas-and-panel mix

**Path:** `G:/COMFYUI_Database/Custom_Nodes/comfyui-3d-viewport-bridge/`.
**Why it's relevant:** Third-party (not Joe-authored), mixes graph nodes with a viewport extension. Useful as a sanity-check that the patterns above generalize beyond Joe's house style. Confirms `registerSidebarTab` is in active use in the broader ecosystem.

**Optional fourth:** `comfyui-easy-use` (Vue 3 frontend). Useful if the bridge panel ever wants Vue components — easy-use ships an actual `App.vue` and demonstrates how to bring a Vue mini-app inside a custom node. Out of scope for v0 (vanilla DOM is enough); v1 candidate.

---

## [7/8] ComfyUI Manager packaging requirements

There are **two listings** to think about, complementary not redundant:

### A. ComfyUI Manager's static JSON registry

ComfyUI Manager (`ltdrdata/ComfyUI-Manager`) reads `custom-node-list.json` from its own repo. Each entry is a dict; confirmed schema from `/g/COMFYUI_Database/Custom_Nodes/ComfyUI-Manager/custom-node-list.json`:

```json
{
  "author": "<author or org name>",
  "title": "<display title>",
  "id": "<short slug, optional but recommended>",
  "reference": "<canonical github URL>",
  "files": [
    "<git-clone-able URL>"
  ],
  "install_type": "git-clone",
  "description": "<one-paragraph description>"
}
```

**To list the bridge panel here:** open a PR against `ltdrdata/ComfyUI-Manager` adding an entry. PR review is human-driven; turnaround is days, not hours.

### B. ComfyUI Registry — the modern path

ComfyUI Registry (`https://registry.comfy.org/`) is the official package registry that comfy-cli publishes into. The schema is **`pyproject.toml`-based** and typed by `comfy_config/types.py` in the upstream ComfyUI repo:

```toml
[project]
name = "comfy-moneta-bridge-ui"
version = "0.1.0"
description = "Bridge status + session hydration panel for comfy-moneta-bridge"
requires-python = ">=3.9"
dependencies = []
license = { text = "Proprietary" }

[project.urls]
Homepage      = "https://github.com/JosephOIbrahim/comfy-moneta-bridge"
Documentation = "https://github.com/JosephOIbrahim/comfy-moneta-bridge#readme"
Repository    = "https://github.com/JosephOIbrahim/comfy-moneta-bridge"
Issues        = "https://github.com/JosephOIbrahim/comfy-moneta-bridge/issues"

[tool.comfy]
PublisherId = "<josephoibrahim or whatever publisher slug Joe owns on the registry>"
DisplayName = "Comfy-Moneta Bridge"
Icon        = "<URL to a small square icon, optional>"
includes    = []                    # extra files to package
web         = "web"                 # the WEB_DIRECTORY
banner_url  = ""                    # optional
```

Required fields per the `ComfyConfig` Pydantic model: `PublisherId`, `DisplayName`. Optional: `Icon`, `Models` (list of `{location, model_url}` for shipped model assets — n/a for the bridge panel), `includes` (extra non-Python files to bundle), `web` (the served frontend dir), `banner_url`.

Additional `ProjectConfig` fields the registry consumes:

- `supported_os` — list of `linux`, `macos`, `windows`
- `supported_accelerators` — list of `cuda`, `rocm`, `mps`, `cpu`
- `supported_comfyui_version` — version constraint string
- `supported_comfyui_frontend_version` — version constraint string

**Publishing flow:** `comfy node publish` from the extension's repo root. Requires the publisher account to exist on `registry.comfy.org`.

### Practical recommendation for the bridge panel

Do **both**:

1. Publish to ComfyUI Registry first (canonical, persistent, version-managed).
2. PR to ComfyUI-Manager's `custom-node-list.json` for legacy / non-Registry-aware Manager users.

For v0 launch, the Registry path is the higher-leverage path; Manager listing is a follow-on.

---

## [8/8] Open questions for extension architecture

These are surfaced by the inventory, not solved by it.

- **Repo split or in-tree extension?** Should the panel ship as `comfy-moneta-bridge-ui` (separate repo, separate Manager listing, separate version cycle) or live inside `comfy-moneta-bridge/extensions/comfyui-panel/` (single repo, single version)? Joe's `comfy-cozy-panel` and `comfy-cozy-ui` are *separately-installed custom nodes that symlink into a shared Comfy-Cozy source tree* — one repo, two install points. The bridge could mirror that: one repo, one install point (`extensions/comfyui-panel/` symlinked into `custom_nodes/comfy-moneta-bridge-ui/`), or it could split. Tradeoffs:

  | Approach | Pro | Con |
  |---|---|---|
  | Single repo, in-tree extension | Atomic versioning; bridge release == panel release; one PR to both Manager and Registry | More complex packaging metadata in pyproject (the bridge package shouldn't pollute the panel's `[tool.comfy]` table) |
  | Separate repo (`comfy-moneta-bridge-ui`) | Clean separation; panel can ship before bridge core if needed | Two repos, two release cycles, one referencing the other by URL |

- **In-process vs. out-of-process bridge access from the panel.** The panel's server-side aiohttp routes need to read bridge state (cursor file, recent outcomes, session list, last hydrate). Two architectural shapes:

  | | Shape | Concern |
  |---|---|---|
  | **In-process** | Panel imports `comfy_moneta_bridge` and calls `Tailer`/`CursorStore`/`write_capsule` directly | Honors Hard Rule §6 (ephemeral Moneta handles only) — the panel becomes another caller of `ingest`/`capsule`. Coupling: panel pinned to an exact bridge version |
  | **Out-of-process** | Panel reads `~/.comfy-moneta-bridge/cursor.json` directly + invokes `bridge` CLI via subprocess | No code-level coupling; panel can run alongside any bridge ≥0.1.0. But: subprocess for hydrate adds ~handle-cycle latency; CLI is the only contract |

  In-process is cleaner for v0 demo. Out-of-process is more durable. Joe's call.

- **Sidebar coexistence with comfy-cozy-ui.** If both extensions register a sidebar tab in the same install, ComfyUI shows both tabs side-by-side. Names + icons must differentiate. The bridge cannot hijack the `superduper` namespace — it belongs to comfy-cozy-ui. Bridge panel id candidates: `comfy-moneta-bridge`, `cmb`, `bridge-status`. Final pick is a UX decision.

- **Live status push or pull?** comfy-cozy-ui uses a WebSocket to stream chat events; the bridge panel could do the same for live ingest feedback (cursor advances, every successful deposit, every rotation event), or it could poll an HTTP endpoint every N seconds. Polling is simpler; WebSocket is nicer-feeling. v0: poll. v1 candidate: WebSocket.

- **Settings panel integration.** ComfyUI's settings UI accepts contributions via `app.registerExtension({ settings: [...] })`. The bridge has three runtime configs (`--comfy-cozy-root`, `--moneta-storage`, `--state-dir`) currently set only via CLI flags. Should the panel surface them as ComfyUI settings, or read a config file that the CLI also reads? Settings panel is more discoverable; config file is more consistent with the CLI. Could do both (panel reads env / file fallback, settings UI is just a pretty editor for the same file).

- **Hydrate UX flow.** The hydrate command takes a session name and produces `sessions/{name}.json`. Panel UX choices:

  - Free-text input — user types session name, clicks "Hydrate". Mirrors CLI exactly.
  - Dropdown of known sessions — panel reads `~/.comfy-moneta-bridge/cursor.json` and the Moneta storage to enumerate distinct `session` values that have been ingested. More discoverable; requires bridge-side or panel-side enumeration.
  - Recent-sessions list — last 5 hydrated, last 5 ingested, "+" to type a new one. Compact and discoverable. v1 candidate.

  v0: free-text + a "browse known sessions" button. v1: full enumeration with recency.

- **Restart-required warning.** The CLI's `bridge hydrate` already prints "Comfy-Cozy must be (re)started for this to take effect." The panel needs the same warning at the same moment, ideally as a non-modal toast that explains *why* (Comfy-Cozy reads `AUTO_LOAD_SESSION` once at startup) and offers "Restart Comfy-Cozy now" if the panel can drive that. ComfyUI itself doesn't restart Comfy-Cozy (separate process); the panel would have to invoke `launch.launch_with_session` or the CLI equivalent.

- **Manager listing scope.** Some Manager-listed nodes are pure node packs (graph nodes only); some are pure UI; some are mixed. The Registry / Manager metadata doesn't strongly distinguish. The bridge panel should clearly self-describe in its README and `description` field as **"UI extension, no graph nodes"** so users hunting for nodes don't install it expecting to see new node types.

- **Frontend version constraint.** `app.extensionManager.registerSidebarTab` is the modern PrimeVue API. It is **not** present in pre-PrimeVue ComfyUI builds. The bridge needs a minimum `comfyui-frontend-package` version; reading `comfy-cozy-ui`'s production behavior, that minimum is "whatever's currently shipped in 2026-04 builds" — but a precise version pin requires reading the frontend package's changelog. v0: declare `supported_comfyui_frontend_version >= 1.5.0` (a guess; pin precisely before publishing).

- **Server-side dependency on the bridge package.** The panel's aiohttp routes (in `extensions/comfyui-panel/server/routes.py`) will `import comfy_moneta_bridge`. That means the panel's install must arrange for the bridge package to be on `sys.path`. Options:

  1. Panel's `pyproject.toml` declares `comfy-moneta-bridge` as a dependency. ComfyUI's startup pip-installs it.
  2. Panel ships symlinked into a checkout of the bridge repo (Joe's pattern with comfy-cozy-ui).
  3. Panel adds the bridge repo's path to `sys.path` at import (the comfy-cozy-panel pattern at `__init__.py:25-27`).

  Option 1 is the cleanest for distribution. Option 2 is the cleanest for development. Option 3 is fragile but matches Joe's existing pattern.

- **The "trigger hydrate" button needs a session.** If no session is selected, the panel should disable the button and show a hint. If a session is typed but doesn't exist in the bridge's Moneta storage, the panel should still allow the click — `write_capsule` will produce a capsule with `notes: []` and `metadata.memory_count: 0`. That's useful (creates a stub capsule) but might surprise the user. UX choice: warn before producing a zero-memory capsule, or produce it silently? v0: warn.

- **Status surface.** What does "bridge + Comfy-Cozy status" mean for the panel display? Inventory:

  | Signal | Source | Update cadence |
  |---|---|---|
  | Bridge tail running | Process discovery on the bridge's pid file (the bridge currently has no pidfile — would need to add or scan ps for `bridge tail`) | n/a (out of scope) or seconds |
  | Cursor position per watched file | `~/.comfy-moneta-bridge/cursor.json` | event-driven on bridge ingest |
  | Last ingest timestamp | Cursor file mtime, or a timestamp in cursor JSON (not currently stored) | event-driven |
  | Moneta storage size | `~/.comfy-moneta-bridge/moneta/snapshot.json` size | event-driven |
  | Comfy-Cozy outcomes file size + last-write | `G:/Comfy-Cozy/sessions/*_outcomes.jsonl` stat | seconds |
  | Comfy-Cozy process running | ps scan for `agent run` | seconds |

  Panel can show a coarse summary: "Tail: running / stopped", "Cursor: line N of session X", "Moneta: K memories", "Comfy-Cozy: running, Y outcomes since last hydrate". Each of these has a clear data source; none are blocked.

- **Panel writes to bridge state — is that allowed?** The panel will trigger hydrate. That writes to Comfy-Cozy's `sessions/{name}.json`, which is *Comfy-Cozy's* state directory (Hard Rule §3 of the bridge mission says "no modifications to Comfy-Cozy source"). Writing to `sessions/` is *not* a source modification — it's the standard hydrate contract. So this is fine. But it's worth re-reading Hard Rule §3 in context: it forbids touching Comfy-Cozy *code*, not its session capsule directory.

---

## Bottom line

A panel-based ComfyUI extension for the bridge is straightforward in shape and well-supported by the existing API. It will be a single custom_nodes directory with `__init__.py` exporting `WEB_DIRECTORY = "./web"`, a `server/routes.py` mounting aiohttp routes on `PromptServer`, and a `web/js/sidebar.js` calling `app.extensionManager.registerSidebarTab(...)` to mount the panel UI into the left rail. The visual language follows ComfyUI's CSS custom properties (theme-reactive: `var(--bg-color)`, `var(--accent-primary)`, etc.), matches Joe's existing house style (Inter + JetBrains Mono, 4px spacing base, 11–20px type scale, slot-color palette for any per-node coloring), and avoids hardcoded hex outside the slot/agent palette. The extension does not require Vue or React; vanilla DOM in the `render(el)` callback is sufficient and mirrors comfy-cozy-ui exactly.

For packaging, the bridge panel publishes to ComfyUI Registry via `pyproject.toml`'s `[tool.comfy]` table (`PublisherId`, `DisplayName`, `web = "web"`) and adds an entry to ComfyUI-Manager's `custom-node-list.json` for legacy users. Server-side, the panel either imports the bridge package directly (in-process; needs version pinning) or invokes the `bridge` CLI (out-of-process; survives bridge upgrades). v0 recommendation: in-process import, declare `comfy-moneta-bridge >= 0.1.0` as a dependency.

The 11 open questions in §8 are the design discussion that should precede a Phase 1 build mission. The largest ones — repo split vs. in-tree, in-process vs. out-of-process, single sidebar slot or multiple — shape the package boundaries and want a Joe ruling before code starts. The smaller ones (icon choice, polling vs. WebSocket, free-text vs. dropdown session selector) can ride on the build mission's ARCHITECT pass per AGENT_COMMANDMENTS §5.
