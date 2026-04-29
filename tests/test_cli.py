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
