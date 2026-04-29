"""Tests for comfy_moneta_bridge.launch.

Per mission v3.1 Phase 6 test table. ``subprocess.Popen`` is mocked via
monkeypatch so no actual Comfy-Cozy process is spawned.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from comfy_moneta_bridge import launch
from comfy_moneta_bridge.launch import launch_with_session


class _PopenStub:
    instances: list["_PopenStub"] = []

    @classmethod
    def reset(cls) -> None:
        cls.instances = []

    def __init__(self, args, env=None, cwd=None, **kwargs) -> None:
        self.args = list(args)
        self.env = dict(env) if env is not None else None
        self.cwd = cwd
        self.kwargs = kwargs
        self.pid = 4242
        _PopenStub.instances.append(self)


@pytest.fixture
def patched_popen(monkeypatch):
    _PopenStub.reset()
    monkeypatch.setattr(
        "comfy_moneta_bridge.launch.subprocess.Popen", _PopenStub
    )
    return _PopenStub


def _make_capsule(comfy_root: Path, name: str) -> None:
    sessions = comfy_root / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    (sessions / f"{name}.json").write_text("{}", encoding="utf-8")


def test_missing_capsule_raises(tmp_path, patched_popen) -> None:
    # No capsule created.
    with pytest.raises(FileNotFoundError) as excinfo:
        launch_with_session("default", tmp_path)
    assert "default" in str(excinfo.value)
    assert "bridge hydrate" in str(excinfo.value)
    assert patched_popen.instances == []


def test_env_contains_auto_load(tmp_path, patched_popen) -> None:
    _make_capsule(tmp_path, "default")
    handle = launch_with_session("default", tmp_path)
    assert handle is patched_popen.instances[0]
    assert handle.env is not None
    assert handle.env.get("AUTO_LOAD_SESSION") == "default"


def test_default_mode_is_run(tmp_path, patched_popen) -> None:
    _make_capsule(tmp_path, "default")
    launch_with_session("default", tmp_path)
    assert patched_popen.instances[0].args == ["agent", "run"]


def test_mcp_mode(tmp_path, patched_popen) -> None:
    _make_capsule(tmp_path, "default")
    launch_with_session("default", tmp_path, mode="mcp")
    assert patched_popen.instances[0].args == ["agent", "mcp"]
