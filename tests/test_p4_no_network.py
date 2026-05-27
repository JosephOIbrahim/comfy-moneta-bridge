"""P4 — no outbound network calls during ingest in default config.

SPEC P4 (Amendment A1): "No outbound network calls during ingest in default
config. Model weights provisioned at install time via deterministic,
verifiable cache." Verified by socket-patch property test: with all socket
egress blocked, a bge-mode ingest against the pre-provisioned cache must
still complete. The BGE singleton is reset so the offline *load* path runs
under the network block, not just encode.

Requires the model already provisioned (vector.provision_model / install
step). Skipped if the embeddings extra is absent.
"""

from __future__ import annotations

import socket
from pathlib import Path

import pytest

pytest.importorskip("sentence_transformers")

from comfy_moneta_bridge import vector as vector_mod
from comfy_moneta_bridge.ingest import ingest_outcome


def _outcome() -> dict:
    return {
        "schema_version": 1,
        "session": "net_test",
        "timestamp": 1.0,
        "workflow_summary": "a calm lake at dawn, soft light",
        "vision_notes": ["low contrast", "pastel palette"],
        "key_params": {"model": "sdxl"},
    }


def test_bge_ingest_makes_no_network_calls(tmp_path: Path, monkeypatch):
    """A full bge-mode ingest completes with zero socket egress."""
    monkeypatch.setenv("BRIDGE_EMBEDDER_MODE", "bge")
    # Force the offline load path to execute under the network block.
    monkeypatch.setattr(vector_mod, "_bge_model", None)

    attempts: list = []

    def _blocked_connect(self, address):  # noqa: ANN001
        attempts.append(("connect", address))
        raise AssertionError(f"P4 violation: socket connect to {address}")

    def _blocked_getaddrinfo(*args, **kwargs):
        attempts.append(("getaddrinfo", args[:2]))
        raise AssertionError(f"P4 violation: DNS lookup {args[:2]}")

    monkeypatch.setattr(socket.socket, "connect", _blocked_connect)
    monkeypatch.setattr(socket, "getaddrinfo", _blocked_getaddrinfo)

    storage = tmp_path / "moneta"
    ingest_outcome(_outcome(), storage)  # must not raise

    assert attempts == [], f"unexpected network egress during ingest: {attempts}"


def test_offline_load_then_encode(tmp_path: Path, monkeypatch):
    """local_files_only load + encode round-trips a 384-dim vector offline."""
    monkeypatch.setenv("BRIDGE_EMBEDDER_MODE", "bge")
    monkeypatch.setattr(vector_mod, "_bge_model", None)
    vec = vector_mod.encode_outcome({"workflow_summary": "neon city at night"})
    assert len(vec) == vector_mod.DIMENSION
