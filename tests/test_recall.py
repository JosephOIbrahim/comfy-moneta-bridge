"""Tests for comfy_moneta_bridge.recall."""

from __future__ import annotations

import json
import logging

import pytest

from comfy_moneta_bridge import recall as recall_mod
from comfy_moneta_bridge.recall import recall
from comfy_moneta_bridge.vector import (
    EMBEDDER_VERSION_BGE,
    EMBEDDER_VERSION_SYNTHETIC,
)


class _MemoryStub:
    def __init__(self, payload: str) -> None:
        self.payload = payload


def _outcome(**overrides) -> dict:
    base = {
        "schema_version": 1,
        "session": "default",
        "timestamp": 1.0,
        "workflow_summary": "x",
        "vision_notes": [],
        "key_params": {},
        "_embedder": EMBEDDER_VERSION_SYNTHETIC,
    }
    base.update(overrides)
    return base


class RecallMonetaMock:
    canned: list[_MemoryStub] = []
    last_limit: int = 0

    @classmethod
    def reset(cls) -> None:
        cls.canned = []
        cls.last_limit = 0

    def __init__(self, config) -> None:
        self.config = config

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def query(self, embedding, limit):
        RecallMonetaMock.last_limit = limit
        return list(RecallMonetaMock.canned[:limit])


@pytest.fixture
def patched_moneta(monkeypatch):
    RecallMonetaMock.reset()
    # These exercise recall mechanics with synthetic-tagged canned data;
    # pin synthetic mode now that bge is the package default (leaf L4).
    monkeypatch.setenv("BRIDGE_EMBEDDER_MODE", "synthetic")
    monkeypatch.setattr(
        "comfy_moneta_bridge.recall.Moneta", RecallMonetaMock
    )
    return RecallMonetaMock


def test_returns_payloads_in_cosine_rank_order(tmp_path, patched_moneta):
    patched_moneta.canned = [
        _MemoryStub(json.dumps(_outcome(session="a", timestamp=1.0))),
        _MemoryStub(json.dumps(_outcome(session="b", timestamp=2.0))),
        _MemoryStub(json.dumps(_outcome(session="c", timestamp=3.0))),
    ]
    out = recall("anything", tmp_path / "moneta", top_k=10)
    assert [r["session"] for r in out] == ["a", "b", "c"]


def test_truncates_to_top_k(tmp_path, patched_moneta):
    patched_moneta.canned = [
        _MemoryStub(json.dumps(_outcome(timestamp=float(i)))) for i in range(20)
    ]
    out = recall("q", tmp_path / "moneta", top_k=5)
    assert len(out) == 5


def test_over_fetches_for_version_filter(tmp_path, patched_moneta):
    recall("q", tmp_path / "moneta", top_k=10)
    assert patched_moneta.last_limit >= 40


def test_drops_wrong_embedder_version(tmp_path, patched_moneta, caplog):
    patched_moneta.canned = [
        _MemoryStub(json.dumps(_outcome(session="a", _embedder=EMBEDDER_VERSION_SYNTHETIC))),
        _MemoryStub(json.dumps(_outcome(session="b", _embedder=EMBEDDER_VERSION_BGE))),
        _MemoryStub(json.dumps(_outcome(session="c", _embedder=EMBEDDER_VERSION_SYNTHETIC))),
    ]
    caplog.set_level(logging.INFO, logger="comfy_moneta_bridge.recall")
    out = recall("q", tmp_path / "moneta", top_k=10)
    assert [r["session"] for r in out] == ["a", "c"]
    assert any("skipped 1" in r.message for r in caplog.records)


def test_untagged_payload_treated_as_synthetic(tmp_path, patched_moneta):
    untagged = _outcome(session="legacy")
    untagged.pop("_embedder")
    patched_moneta.canned = [_MemoryStub(json.dumps(untagged))]
    out = recall("q", tmp_path / "moneta", top_k=10)
    assert len(out) == 1
    assert out[0]["session"] == "legacy"


def test_unparseable_payload_skipped(tmp_path, patched_moneta):
    patched_moneta.canned = [
        _MemoryStub("not-json"),
        _MemoryStub(json.dumps(_outcome(session="ok"))),
    ]
    out = recall("q", tmp_path / "moneta", top_k=10)
    assert [r["session"] for r in out] == ["ok"]


def test_empty_storage_returns_empty(tmp_path, patched_moneta):
    out = recall("q", tmp_path / "moneta", top_k=10)
    assert out == []
