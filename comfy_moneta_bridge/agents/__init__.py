"""Agent-driven workflow manipulation surface (v0.2, opt-in).

Per ``BRIDGE_BUILD_MISSION_v3_2.md``, this subpackage adds a workflow
mutation surface that v3.1 listed as out-of-scope. All code here is
gated by the ``[agents]`` extras install — a default
``pip install comfy-moneta-bridge`` does not pull these dependencies,
and the runtime CLI commands ``bridge orchestrate`` / ``bridge mcp``
fail with a clear install hint if the extras are missing.

Public modules:
  * ``workflow``  — typed graph model + mutation primitives (stdlib only)
  * ``client``    — ComfyUI HTTP+WS client (httpx + websockets)
  * ``tools``     — single source of truth for tool definitions
  * ``constitution`` — runtime constitution loader + refusal hooks
  * ``roles``     — role prompts and turn-taking
  * ``harness``   — long-running async loop with crash-safe checkpoints
  * ``orchestrator`` — single-goal driver
  * ``mcp_server`` — stdio MCP server
  * ``loop``      — internal Anthropic-SDK Claude loop
"""

from __future__ import annotations
