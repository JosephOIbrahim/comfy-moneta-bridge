"""CLI entry point for the review harness.

Conventions match ``comfy_moneta_bridge/cli.py`` (Typer app, default
paths via constants, docstring-first per command). Invoked as
``python -m review`` with subcommands ``run``, ``resume``, ``status``,
``report``.

The ``review`` package is intentionally NOT a subcommand of the
``bridge`` Typer app — bridge runtime users should not have to install
the ``anthropic`` SDK to run ``bridge tail``.
"""

from __future__ import annotations

import asyncio
import json

import typer

from .artifacts import read_findings, read_state, run_dir
from .constitution import article_count
from .orchestrator import Orchestrator, RunConfig, list_runs

app = typer.Typer(add_completion=False, no_args_is_help=True)


@app.command()
def run(
    iterations: int = typer.Option(
        5, "--iterations", help="Number of iterations to run (default 5)."
    ),
    parallelism: int = typer.Option(
        4, "--parallelism", help="Concurrent expert calls (default 4)."
    ),
    max_cost_usd: float = typer.Option(
        10.00,
        "--max-cost-usd",
        help="Hard cap on total estimated USD cost (default $10).",
    ),
    max_tokens_per_call: int = typer.Option(
        32_000,
        "--max-tokens-per-call",
        help="max_tokens for each Anthropic call (default 32k).",
    ),
) -> None:
    """Run a fresh review and write artifacts to .claude/reviews/runs/<ts>/.

    Requires ANTHROPIC_API_KEY in the environment.
    """
    config = RunConfig(
        iterations=iterations,
        parallelism=parallelism,
        max_cost_usd=max_cost_usd,
        max_tokens_per_call=max_tokens_per_call,
    )
    typer.echo(
        f"loaded REVIEW_CONSTITUTION.md ({article_count()} articles); "
        f"running with model=claude-opus-4-7 iterations={iterations} "
        f"parallelism={parallelism} cost_cap=${max_cost_usd:.2f}"
    )
    orchestrator = Orchestrator(config)
    run_id = asyncio.run(orchestrator.run_new())
    typer.echo(f"\nrun_id: {run_id}")


@app.command()
def resume(
    run_id: str = typer.Argument(..., help="The run id to resume."),
    parallelism: int = typer.Option(4, "--parallelism"),
    max_cost_usd: float = typer.Option(10.00, "--max-cost-usd"),
    max_tokens_per_call: int = typer.Option(32_000, "--max-tokens-per-call"),
) -> None:
    """Resume a partial run from its last checkpoint."""
    state = read_state(run_id)
    if state is None:
        typer.echo(
            f"no state.json for run {run_id} at {run_dir(run_id)}", err=True
        )
        raise typer.Exit(code=2)
    config = RunConfig(
        iterations=state.iterations_planned,
        parallelism=parallelism,
        max_cost_usd=max_cost_usd,
        max_tokens_per_call=max_tokens_per_call,
    )
    orchestrator = Orchestrator(config)
    asyncio.run(orchestrator.resume(run_id))


@app.command()
def status(
    run_id: str = typer.Argument(..., help="The run id to inspect."),
) -> None:
    """Print read-only state for a run."""
    state = read_state(run_id)
    if state is None:
        typer.echo(f"no state.json for run {run_id}", err=True)
        raise typer.Exit(code=2)
    findings = read_findings(run_id)
    by_status: dict[str, int] = {}
    for f in findings:
        by_status[f.status] = by_status.get(f.status, 0) + 1
    typer.echo(
        json.dumps(
            {
                "run_id": state.run_id,
                "model": state.model,
                "iterations_planned": state.iterations_planned,
                "iteration_completed": state.iteration_completed,
                "last_actor": state.last_actor,
                "last_action": state.last_action,
                "findings_total": len(findings),
                "findings_by_status": by_status,
            },
            indent=2,
        )
    )


@app.command()
def report(
    run_id: str = typer.Argument(..., help="The run id whose report to print."),
) -> None:
    """Print FINAL_REPORT.md for a run to stdout."""
    path = run_dir(run_id) / "FINAL_REPORT.md"
    if not path.exists():
        typer.echo(f"no FINAL_REPORT.md at {path}", err=True)
        raise typer.Exit(code=2)
    typer.echo(path.read_text(encoding="utf-8"))


@app.command(name="list")
def list_cmd() -> None:
    """List all known runs."""
    runs = list_runs()
    if not runs:
        typer.echo("(no runs)")
        return
    for r in runs:
        typer.echo(r)


if __name__ == "__main__":
    app()
