"""Spawn Comfy-Cozy with ``AUTO_LOAD_SESSION`` set.

One public function: ``launch_with_session(session_name, comfy_cozy_root,
mode="run") -> subprocess.Popen``. The bridge does not own the
Comfy-Cozy process lifecycle past spawn — caller manages.

Pre-spawn check: the capsule must already exist (``bridge hydrate``
should have run first). Spawning Comfy-Cozy with ``AUTO_LOAD_SESSION``
pointing at a missing capsule would surface as a Comfy-Cozy startup
error far from this call site; raising ``FileNotFoundError`` here
keeps the failure local.
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Literal

_logger = logging.getLogger(__name__)


def launch_with_session(
    session_name: str,
    comfy_cozy_root: Path,
    mode: Literal["run", "mcp"] = "run",
) -> subprocess.Popen:
    """Spawn ``agent <mode>`` from ``comfy_cozy_root`` with the env set.

    Raises ``FileNotFoundError`` with a clear message if the expected
    capsule does not yet exist.
    """
    comfy_root = Path(comfy_cozy_root)
    capsule_path = comfy_root / "sessions" / f"{session_name}.json"
    if not capsule_path.exists():
        raise FileNotFoundError(
            f"capsule for session {session_name!r} not found at "
            f"{capsule_path}; run `bridge hydrate {session_name}` first"
        )

    env = os.environ.copy()
    env["AUTO_LOAD_SESSION"] = session_name

    _logger.info(
        "launch_with_session session=%s mode=%s cwd=%s",
        session_name, mode, comfy_root,
    )
    return subprocess.Popen(
        ["agent", mode],
        env=env,
        cwd=str(comfy_root),
    )
