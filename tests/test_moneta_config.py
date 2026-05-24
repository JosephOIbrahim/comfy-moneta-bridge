"""Tests for the shared MonetaConfig builder.

Verifies the extracted ``build_config`` returns the same shape the three
old per-module duplicates returned. Behavior parity is the contract:
``ingest``, ``capsule``, ``recall`` (and any new ``agents/*`` caller)
must see an identical config so they all bind to the same storage URI
and vector dimension.
"""

from __future__ import annotations

from pathlib import Path

from comfy_moneta_bridge.moneta_config import build_config
from comfy_moneta_bridge.vector import DIMENSION


def test_build_config_creates_storage_dir(tmp_path: Path) -> None:
    storage = tmp_path / "fresh_moneta"
    assert not storage.exists()
    build_config(storage)
    assert storage.exists() and storage.is_dir()


def test_build_config_returns_expected_shape(tmp_path: Path) -> None:
    storage = tmp_path / "moneta"
    config = build_config(storage)
    assert config.storage_uri == f"moneta-bridge://{storage.as_posix()}"
    assert config.snapshot_path == storage / "snapshot.json"
    assert config.wal_path == storage / "wal.jsonl"
    assert config.mock_target_log_path == storage / "usd_authorings.jsonl"
    assert config.embedding_dim == DIMENSION == 384


def test_build_config_idempotent(tmp_path: Path) -> None:
    storage = tmp_path / "moneta"
    a = build_config(storage)
    b = build_config(storage)
    assert a.storage_uri == b.storage_uri
    assert a.snapshot_path == b.snapshot_path
    assert a.wal_path == b.wal_path
    assert a.embedding_dim == b.embedding_dim


def test_build_config_accepts_str_or_path(tmp_path: Path) -> None:
    """Path-or-str polymorphism mirrors the old per-module behavior."""
    as_str = build_config(str(tmp_path / "as_str"))
    as_path = build_config(tmp_path / "as_path")
    assert as_str.embedding_dim == as_path.embedding_dim == DIMENSION
