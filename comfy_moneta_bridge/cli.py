"""User-facing Typer CLI.

  bridge tail [--comfy-cozy-root PATH] [--moneta-storage PATH]
              [--state-dir PATH]
      Run the JSONL tailer until SIGINT. Logs deposit events at INFO.
      Writes {state_dir}/tail.pid for the §13 mutex with orchestrate/mcp.

  bridge hydrate <session_name> [--comfy-cozy-root PATH]
                                [--moneta-storage PATH] [--launch]
      Build sessions/{name}.json from Moneta state. Print a hot-hydrate
      warning + the AUTO_LOAD_SESSION instruction. With --launch, also
      spawn Comfy-Cozy and print the PID.

  bridge recall <query> [--top-k N] [--moneta-storage PATH]
      Cross-session semantic recall (BGE mode for real text similarity).

  bridge orchestrate <goal> [--session NAME] [--state-dir PATH]
                            [--comfy-cozy-root PATH] [--max-steps N]
                            [--model MODEL] [--interactive]
      Run the internal Claude loop against a local ComfyUI. Requires
      [agents] extras.

  bridge mcp [--state-dir PATH] [--comfy-cozy-root PATH] [--tools-only]
      Run the stdio MCP server exposing the bridge's tool surface.
      Requires [agents] extras.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import typer

from comfy_moneta_bridge import capsule as capsule_mod
from comfy_moneta_bridge import launch as launch_mod
from comfy_moneta_bridge.state import CursorStore
from comfy_moneta_bridge.tail import Tailer

DEFAULT_COMFY_COZY_ROOT = Path("G:/Comfy-Cozy")
DEFAULT_BRIDGE_STATE_DIR = Path.home() / ".comfy-moneta-bridge"
DEFAULT_MONETA_STORAGE = DEFAULT_BRIDGE_STATE_DIR / "moneta"

app = typer.Typer(add_completion=False, no_args_is_help=True)


def _ensure_extras(component: str) -> None:
    """Import-check the [agents] extras and raise a clear error if missing."""
    missing: list[str] = []
    for mod in ("anthropic", "mcp", "httpx", "websockets"):
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    if missing:
        typer.echo(
            f"bridge {component} requires the [agents] extras. "
            f"Missing: {', '.join(missing)}.\n"
            "Install with: pip install 'comfy-moneta-bridge[agents]'",
            err=True,
        )
        raise typer.Exit(code=3)


@app.command()
def tail(
    comfy_cozy_root: Path = typer.Option(
        DEFAULT_COMFY_COZY_ROOT,
        "--comfy-cozy-root",
        help="Root of the Comfy-Cozy checkout whose sessions/ to watch.",
    ),
    moneta_storage: Path = typer.Option(
        DEFAULT_MONETA_STORAGE,
        "--moneta-storage",
        help="Directory for Moneta WAL+snapshot files.",
    ),
    state_dir: Path = typer.Option(
        DEFAULT_BRIDGE_STATE_DIR,
        "--state-dir",
        help="Directory for the bridge cursor file.",
    ),
    batch_size: int = typer.Option(
        1,
        "--batch-size",
        help=(
            "Coalesce up to N outcomes into one Moneta snapshot "
            "(>1 enables cross-event buffering). Default 1 = flush "
            "every event. Larger N amortizes the run_sleep_pass cost "
            "but widens the crash-replay window."
        ),
    ),
    batch_max_delay: float = typer.Option(
        0.0,
        "--batch-max-delay",
        help=(
            "Max seconds an outcome may sit buffered before a forced "
            "flush (bounds durability latency when --batch-size>1). "
            "Default 0 = no time-based flush."
        ),
    ),
) -> None:
    """Watch sessions/*_outcomes.jsonl and ingest each new line into Moneta."""
    logging.basicConfig(level=logging.INFO)

    state_dir.mkdir(parents=True, exist_ok=True)
    # Hard Rule §13: tail and orchestrate/mcp are mutually exclusive.
    from comfy_moneta_bridge.agents.harness import (
        PidFileGuard,
        TAIL_PID_FILE,
        assert_orchestrate_not_running,
    )

    assert_orchestrate_not_running(state_dir)

    cursor_path = state_dir / "cursor.json"
    cursor_store = CursorStore(cursor_path)
    sessions_dir = comfy_cozy_root / "sessions"
    if not sessions_dir.exists():
        typer.echo(
            f"sessions dir {sessions_dir} does not exist; "
            "is --comfy-cozy-root correct?",
            err=True,
        )
        raise typer.Exit(code=2)

    tailer = Tailer(
        sessions_dir, cursor_store, moneta_storage,
        batch_size=batch_size, batch_max_delay_s=batch_max_delay,
    )
    typer.echo(f"bridge tail watching {sessions_dir}")
    with PidFileGuard(state_dir / TAIL_PID_FILE):
        asyncio.run(tailer.run())


@app.command()
def hydrate(
    session_name: str = typer.Argument(
        ..., help="Name of the session to hydrate."
    ),
    comfy_cozy_root: Path = typer.Option(
        DEFAULT_COMFY_COZY_ROOT,
        "--comfy-cozy-root",
        help="Root of the Comfy-Cozy checkout whose sessions/ to write into.",
    ),
    moneta_storage: Path = typer.Option(
        DEFAULT_MONETA_STORAGE,
        "--moneta-storage",
        help="Directory containing Moneta WAL+snapshot files.",
    ),
    launch: bool = typer.Option(
        False,
        "--launch",
        help="Spawn Comfy-Cozy with AUTO_LOAD_SESSION after writing the capsule.",
    ),
) -> None:
    """Hydrate a Comfy-Cozy session from Moneta state."""
    out_path = capsule_mod.write_capsule(
        session_name, comfy_cozy_root, moneta_storage
    )
    rel = out_path.relative_to(comfy_cozy_root) if out_path.is_relative_to(
        comfy_cozy_root
    ) else out_path
    typer.echo(f"✓ Wrote {rel}")
    typer.echo(
        "Note: Comfy-Cozy must be (re)started for this to take effect. "
        "Running instances will not auto-load."
    )
    typer.echo(f"Run with: AUTO_LOAD_SESSION={session_name} agent run")

    if launch:
        proc = launch_mod.launch_with_session(session_name, comfy_cozy_root)
        typer.echo(f"Spawned Comfy-Cozy pid={proc.pid}")


@app.command()
def recall(
    query: str = typer.Argument(
        ..., help="Natural-language query text. Real semantic match requires BRIDGE_EMBEDDER_MODE=bge."
    ),
    top_k: int = typer.Option(
        10, "--top-k", "-k", help="Number of matches to return."
    ),
    moneta_storage: Path = typer.Option(
        DEFAULT_MONETA_STORAGE,
        "--moneta-storage",
        help="Directory containing Moneta WAL+snapshot files.",
    ),
) -> None:
    """Semantic recall across all stored outcomes (newline-delimited JSON)."""
    import json
    from comfy_moneta_bridge import recall as recall_mod

    results = recall_mod.recall(query, moneta_storage, top_k=top_k)
    for r in results:
        typer.echo(json.dumps(r, ensure_ascii=False, sort_keys=True))
    if not results:
        typer.echo(
            "(no matches — check BRIDGE_EMBEDDER_MODE matches the deposit mode)",
            err=True,
        )


@app.command()
def orchestrate(
    goal: str = typer.Argument(
        ..., help="Free-text goal description for the agent."
    ),
    session: str = typer.Option(
        "default", "--session", help="Session name to anchor memory under."
    ),
    state_dir: Path = typer.Option(
        DEFAULT_BRIDGE_STATE_DIR,
        "--state-dir",
        help="Directory for the bridge cursor file and orchestrate.pid.",
    ),
    moneta_storage: Path = typer.Option(
        DEFAULT_MONETA_STORAGE,
        "--moneta-storage",
        help="Directory for Moneta WAL+snapshot files.",
    ),
    comfy_cozy_root: Path | None = typer.Option(
        None,
        "--comfy-cozy-root",
        help="Optional Comfy-Cozy root for capsule_write tool calls.",
    ),
    max_steps: int = typer.Option(
        12, "--max-steps", help="Maximum role-machine turns."
    ),
    model: str = typer.Option(
        "claude-opus-4-7", "--model",
        help="Anthropic model id for the internal Claude loop.",
    ),
    interactive: bool = typer.Option(
        False, "--interactive",
        help="Require human ack after PLANNER (Hard Rule §16).",
    ),
) -> None:
    """Run the internal Claude agent loop against a local ComfyUI."""
    _ensure_extras("orchestrate")
    logging.basicConfig(level=logging.INFO)

    # Lazy import — keeps `bridge --help` and other commands working
    # without the [agents] extras installed.
    from comfy_moneta_bridge.agents.loop import (
        orchestrate as orchestrate_fn,
    )

    asyncio.run(
        orchestrate_fn(
            goal=goal,
            session=session,
            moneta_storage_path=moneta_storage,
            state_dir=state_dir,
            cozy_root=comfy_cozy_root,
            model=model,
            max_steps=max_steps,
            interactive=interactive,
        )
    )


@app.command()
def mcp(
    state_dir: Path = typer.Option(
        DEFAULT_BRIDGE_STATE_DIR,
        "--state-dir",
        help="Directory for the bridge state and orchestrate.pid.",
    ),
    moneta_storage: Path = typer.Option(
        DEFAULT_MONETA_STORAGE,
        "--moneta-storage",
        help="Directory for Moneta WAL+snapshot files.",
    ),
    comfy_cozy_root: Path | None = typer.Option(
        None,
        "--comfy-cozy-root",
        help="Optional Comfy-Cozy root for capsule_write tool calls.",
    ),
    session: str = typer.Option(
        "mcp", "--session", help="Session label for deposits."
    ),
    tools_only: bool = typer.Option(
        False, "--tools-only",
        help="Advertise tools but refuse execution (dry-run mode).",
    ),
) -> None:
    """Run the stdio MCP server exposing the bridge's tool surface."""
    _ensure_extras("mcp")
    logging.basicConfig(level=logging.INFO)

    from comfy_moneta_bridge.agents.mcp_server import run as run_mcp

    run_mcp(
        moneta_storage_path=moneta_storage,
        state_dir=state_dir,
        cozy_root=comfy_cozy_root,
        session=session,
        tools_only=tools_only,
    )
