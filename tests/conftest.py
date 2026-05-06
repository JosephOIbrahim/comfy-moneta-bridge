"""Project-wide pytest fixtures.

Autouse cleanup for ``BRIDGE_EMBEDDER_MODE`` so a developer with the
env var set in their shell does not silently pivot default-mode tests
to BGE. Tests that opt in to BGE explicitly via ``monkeypatch.setenv``
still work — the per-test setenv runs after this fixture's delenv and
overrides it within the test's function scope.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _clear_bridge_embedder_mode(monkeypatch) -> None:
    monkeypatch.delenv("BRIDGE_EMBEDDER_MODE", raising=False)
