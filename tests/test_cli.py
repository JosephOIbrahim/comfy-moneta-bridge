"""Tests for comfy_moneta_bridge.cli.

Per mission v3.1 Phase 7 test table. Uses ``typer.testing.CliRunner``;
``Tailer.run``, ``write_capsule``, and ``launch_with_session`` are
monkeypatched so no real I/O happens.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from comfy_moneta_bridge import cli


runner = CliRunner()


@pytest.fixture
def patched(monkeypatch, tmp_path):
    write_calls: list[tuple] = []
    launch_calls: list[tuple] = []

    def fake_write(session_name, comfy_cozy_root, moneta_storage_path,
                   query_limit=1000):
        write_calls.append((session_name, comfy_cozy_root,
                            moneta_storage_path))
        sessions = Path(comfy_cozy_root) / "sessions"
        sessions.mkdir(parents=True, exist_ok=True)
        out = sessions / f"{session_name}.json"
        out.write_text("{}", encoding="utf-8")
        return out

    class FakePopen:
        def __init__(self, *a, **kw):
            self.pid = 7777

    def fake_launch(session_name, comfy_cozy_root, mode="run"):
        launch_calls.append((session_name, comfy_cozy_root, mode))
        return FakePopen()

    async def fake_tailer_run(self, stop_event=None):
        return  # exit immediately

    monkeypatch.setattr(
        "comfy_moneta_bridge.cli.capsule_mod.write_capsule", fake_write
    )
    monkeypatch.setattr(
        "comfy_moneta_bridge.cli.launch_mod.launch_with_session",
        fake_launch,
    )
    monkeypatch.setattr(
        "comfy_moneta_bridge.cli.Tailer.run", fake_tailer_run
    )
    return {"write": write_calls, "launch": launch_calls}


def test_hydrate_writes_capsule(tmp_path, patched) -> None:
    result = runner.invoke(
        cli.app,
        [
            "hydrate", "default",
            "--comfy-cozy-root", str(tmp_path),
            "--moneta-storage", str(tmp_path / "moneta"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "default.json" in result.output
    assert len(patched["write"]) == 1


def test_hydrate_prints_warning(tmp_path, patched) -> None:
    result = runner.invoke(
        cli.app,
        [
            "hydrate", "default",
            "--comfy-cozy-root", str(tmp_path),
            "--moneta-storage", str(tmp_path / "moneta"),
        ],
    )
    assert "must be (re)started" in result.output
    assert "AUTO_LOAD_SESSION=default" in result.output


def test_hydrate_with_launch_spawns(tmp_path, patched) -> None:
    result = runner.invoke(
        cli.app,
        [
            "hydrate", "default",
            "--comfy-cozy-root", str(tmp_path),
            "--moneta-storage", str(tmp_path / "moneta"),
            "--launch",
        ],
    )
    assert result.exit_code == 0
    assert len(patched["launch"]) == 1
    assert "pid=7777" in result.output


def test_tail_kicks_off(tmp_path, patched) -> None:
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    result = runner.invoke(
        cli.app,
        [
            "tail",
            "--comfy-cozy-root", str(tmp_path),
            "--moneta-storage", str(tmp_path / "moneta"),
            "--state-dir", str(tmp_path / "state"),
        ],
    )
    # Tailer.run is mocked to return immediately, so the command exits 0.
    assert result.exit_code == 0, result.output
    assert "bridge tail watching" in result.output


def test_tail_writes_and_cleans_pid_file(tmp_path, patched) -> None:
    """Hard Rule §13: tail writes tail.pid and removes it on exit."""
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    state_dir = tmp_path / "state"
    result = runner.invoke(
        cli.app,
        [
            "tail",
            "--comfy-cozy-root", str(tmp_path),
            "--moneta-storage", str(tmp_path / "moneta"),
            "--state-dir", str(state_dir),
        ],
    )
    assert result.exit_code == 0
    # After clean exit the pid file must be gone.
    assert not (state_dir / "tail.pid").exists()


def test_tail_refuses_when_orchestrate_pid_present(
    tmp_path, patched
) -> None:
    """Hard Rule §13 symmetric guard: tail refuses when orchestrate
    or mcp is running."""
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "orchestrate.pid").write_text("88", encoding="utf-8")
    result = runner.invoke(
        cli.app,
        [
            "tail",
            "--comfy-cozy-root", str(tmp_path),
            "--moneta-storage", str(tmp_path / "moneta"),
            "--state-dir", str(state_dir),
        ],
    )
    # Refusal raises OrchestrationLockedError which Typer surfaces as
    # a non-zero exit with the exception in result.exception.
    assert result.exit_code != 0
    assert "Hard Rule §13" in str(result.exception)


def test_orchestrate_missing_extras_clear_error(
    tmp_path, monkeypatch
) -> None:
    """If anthropic/mcp can't be imported, give the install hint
    instead of a stack trace."""
    real_import = __builtins__["__import__"] if isinstance(
        __builtins__, dict
    ) else __import__

    def fake_import(name, *args, **kw):
        if name in ("anthropic", "mcp"):
            raise ImportError(f"forced missing: {name}")
        return real_import(name, *args, **kw)

    monkeypatch.setattr("builtins.__import__", fake_import)
    result = runner.invoke(
        cli.app,
        [
            "orchestrate", "do a thing",
            "--state-dir", str(tmp_path / "state"),
        ],
    )
    assert result.exit_code == 3
    assert "agents" in result.output


def test_mcp_missing_extras_clear_error(tmp_path, monkeypatch) -> None:
    real_import = __builtins__["__import__"] if isinstance(
        __builtins__, dict
    ) else __import__

    def fake_import(name, *args, **kw):
        if name in ("anthropic", "mcp"):
            raise ImportError(f"forced missing: {name}")
        return real_import(name, *args, **kw)

    monkeypatch.setattr("builtins.__import__", fake_import)
    result = runner.invoke(
        cli.app,
        ["mcp", "--state-dir", str(tmp_path / "state")],
    )
    assert result.exit_code == 3
    assert "agents" in result.output


def test_orchestrate_invokes_loop(tmp_path, monkeypatch) -> None:
    """When extras are installed, orchestrate routes to loop.orchestrate."""
    called = {}

    async def fake_orchestrate(**kw):
        called.update(kw)

    monkeypatch.setattr(
        "comfy_moneta_bridge.agents.loop.orchestrate", fake_orchestrate
    )
    result = runner.invoke(
        cli.app,
        [
            "orchestrate", "render a teapot",
            "--session", "tea",
            "--state-dir", str(tmp_path / "state"),
            "--moneta-storage", str(tmp_path / "moneta"),
            "--max-steps", "3",
        ],
    )
    assert result.exit_code == 0, result.output
    assert called["goal"] == "render a teapot"
    assert called["session"] == "tea"
    assert called["max_steps"] == 3


def test_mcp_invokes_server(tmp_path, monkeypatch) -> None:
    """When extras are installed, mcp routes to mcp_server.run."""
    called = {}

    def fake_run(**kw):
        called.update(kw)

    monkeypatch.setattr(
        "comfy_moneta_bridge.agents.mcp_server.run", fake_run
    )
    result = runner.invoke(
        cli.app,
        [
            "mcp",
            "--state-dir", str(tmp_path / "state"),
            "--moneta-storage", str(tmp_path / "moneta"),
            "--tools-only",
        ],
    )
    assert result.exit_code == 0, result.output
    assert called["tools_only"] is True
