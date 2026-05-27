"""Tests for the workflow-snapshot extraction path in ``capsule.py``.

The contract is two-sided:

  1. **Regression canary**: when no ``_kind=workflow_snapshot`` deposit
     exists for the session, the workflow block is byte-equal to the
     pre-v0.2 ``_empty_workflow_block()`` output. This is asserted both
     here and in the existing ``test_schema_v2_workflow_stub`` test.
  2. **New behavior**: when one or more snapshot deposits exist, the
     latest by timestamp populates the workflow block; snapshots are
     not counted as memories and not surfaced as notes.
"""

from __future__ import annotations

import json

import pytest

from comfy_moneta_bridge.capsule import (
    WORKFLOW_SNAPSHOT_KIND,
    write_capsule,
)
from tests.test_capsule import (
    CapsuleMonetaMock,
    _memory_from,
    _outcome,
)


@pytest.fixture
def patched_moneta(monkeypatch):
    CapsuleMonetaMock.reset()
    # Synthetic-path snapshot unit tests; pin synthetic mode now that bge
    # is the package default (leaf L4).
    monkeypatch.setenv("BRIDGE_EMBEDDER_MODE", "synthetic")
    monkeypatch.setattr(
        "comfy_moneta_bridge.capsule.Moneta", CapsuleMonetaMock
    )
    return CapsuleMonetaMock


def _snapshot(workflow: dict, session: str = "default", timestamp: float = 100.0,
              loaded_path: str | None = None) -> dict:
    """Build a workflow-snapshot deposit payload."""
    base = _outcome(session=session, timestamp=timestamp)
    return {
        **base,
        "_kind": WORKFLOW_SNAPSHOT_KIND,
        "workflow": workflow,
        "loaded_path": loaded_path,
    }


def test_no_snapshots_yields_empty_block(tmp_path, patched_moneta) -> None:
    """Regression: with no snapshot deposits, workflow block must
    match the pre-v0.2 hardcoded null shape byte-for-byte."""
    patched_moneta.canned = [_memory_from(_outcome())]
    out = write_capsule("default", tmp_path, tmp_path / "moneta")
    capsule = json.loads(out.read_text(encoding="utf-8"))
    assert capsule["workflow"] == {
        "loaded_path": None,
        "format": "api",
        "base_workflow": None,
        "current_workflow": None,
        "history_depth": 0,
    }


def test_single_snapshot_populates_block(tmp_path, patched_moneta) -> None:
    wf = {"3": {"class_type": "KSampler", "inputs": {"seed": 99}}}
    patched_moneta.canned = [
        _memory_from(_outcome(timestamp=1.0)),
        _memory_from(_snapshot(wf, timestamp=2.0)),
    ]
    out = write_capsule("default", tmp_path, tmp_path / "moneta")
    capsule = json.loads(out.read_text(encoding="utf-8"))
    assert capsule["workflow"]["base_workflow"] == wf
    assert capsule["workflow"]["current_workflow"] == wf
    assert capsule["workflow"]["history_depth"] == 1
    assert capsule["workflow"]["format"] == "api"


def test_latest_snapshot_wins(tmp_path, patched_moneta) -> None:
    older = {"3": {"class_type": "KSampler", "inputs": {"seed": 1}}}
    newer = {"3": {"class_type": "KSampler", "inputs": {"seed": 99}}}
    patched_moneta.canned = [
        _memory_from(_snapshot(older, timestamp=1.0)),
        _memory_from(_snapshot(newer, timestamp=2.0)),
    ]
    out = write_capsule("default", tmp_path, tmp_path / "moneta")
    capsule = json.loads(out.read_text(encoding="utf-8"))
    assert capsule["workflow"]["current_workflow"] == newer


def test_snapshot_not_counted_as_memory(tmp_path, patched_moneta) -> None:
    """memory_count reflects outcomes only, not snapshot deposits."""
    wf = {"3": {"class_type": "KSampler", "inputs": {}}}
    patched_moneta.canned = [
        _memory_from(_outcome(timestamp=1.0)),
        _memory_from(_outcome(timestamp=2.0)),
        _memory_from(_snapshot(wf, timestamp=3.0)),
    ]
    out = write_capsule("default", tmp_path, tmp_path / "moneta")
    capsule = json.loads(out.read_text(encoding="utf-8"))
    assert capsule["metadata"]["memory_count"] == 2


def test_snapshot_does_not_appear_in_notes(tmp_path, patched_moneta) -> None:
    wf = {"3": {"class_type": "KSampler", "inputs": {}}}
    patched_moneta.canned = [
        _memory_from(_snapshot(wf, timestamp=1.0)),
    ]
    out = write_capsule("default", tmp_path, tmp_path / "moneta")
    capsule = json.loads(out.read_text(encoding="utf-8"))
    # Snapshot deposit has no vision_notes and the synthetic _outcome
    # fixture would add a "preference" note; since the snapshot is
    # excluded from outcomes-for-notes, notes should be empty.
    assert capsule["notes"] == []


def test_snapshot_loaded_path_flows_through(tmp_path, patched_moneta) -> None:
    wf = {"3": {"class_type": "KSampler", "inputs": {}}}
    patched_moneta.canned = [
        _memory_from(_snapshot(wf, timestamp=1.0,
                               loaded_path="/path/to/wf.json")),
    ]
    out = write_capsule("default", tmp_path, tmp_path / "moneta")
    capsule = json.loads(out.read_text(encoding="utf-8"))
    assert capsule["workflow"]["loaded_path"] == "/path/to/wf.json"


def test_snapshot_from_other_session_ignored(tmp_path, patched_moneta) -> None:
    """A snapshot for a different session must not populate this one."""
    wf = {"3": {"class_type": "KSampler", "inputs": {}}}
    patched_moneta.canned = [
        _memory_from(_outcome(session="default", timestamp=1.0)),
        _memory_from(_snapshot(wf, session="other_session", timestamp=2.0)),
    ]
    out = write_capsule("default", tmp_path, tmp_path / "moneta")
    capsule = json.loads(out.read_text(encoding="utf-8"))
    # The cross-session snapshot was filtered out by the session-name
    # filter before snapshot extraction even ran.
    assert capsule["workflow"]["base_workflow"] is None
    assert capsule["workflow"]["history_depth"] == 0
