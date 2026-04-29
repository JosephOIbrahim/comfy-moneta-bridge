"""User-facing Typer CLI: ``bridge tail`` and ``bridge hydrate``.

  bridge tail [--comfy-cozy-root PATH] [--moneta-storage PATH]
              [--state-dir PATH]
      Run the JSONL tailer until SIGINT. Logs deposit events at INFO.

  bridge hydrate <session_name> [--comfy-cozy-root PATH]
                                [--moneta-storage PATH] [--launch]
      Build sessions/{name}.json from Moneta state. Print a hot-hydrate
      warning + the AUTO_LOAD_SESSION instruction. With --launch, also
      spawn Comfy-Cozy and print the PID.
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
) -> None:
    """Watch sessions/*_outcomes.jsonl and ingest each new line into Moneta."""
    logging.basicConfig(level=logging.INFO)

    state_dir.mkdir(parents=True, exist_ok=True)
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

    tailer = Tailer(sessions_dir, cursor_store, moneta_storage)
    typer.echo(f"bridge tail watching {sessions_dir}")
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
