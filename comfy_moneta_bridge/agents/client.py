"""ComfyUI HTTP + WebSocket client.

The bridge's first network-talking module. ComfyUI exposes a REST
surface for prompt submission and history lookup, and a WebSocket at
``/ws`` for execution progress events. This client wraps both behind
a small async surface that the agent tools call.

Hard Rule §15: defaults to ``http://127.0.0.1:8188``. A non-localhost
``COMFYUI_URL`` requires ``BRIDGE_ALLOW_REMOTE_COMFY=1``; without it,
construction raises before any network call. The refusal happens at
construction time so a misconfigured environment can never produce a
single outbound packet.

Hard Rule §14: ``/object_info`` is cached per ``ComfyClient`` instance.
Call ``refresh_schema()`` to invalidate when ComfyUI loads or unloads
custom nodes mid-session.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from typing import AsyncIterator
from urllib.parse import urlparse

import httpx
import websockets

_logger = logging.getLogger(__name__)

DEFAULT_COMFYUI_URL = "http://127.0.0.1:8188"
LOCALHOST_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})


class RemoteComfyRefused(RuntimeError):
    """Hard Rule §15: non-localhost without explicit opt-in is refused."""


def _is_localhost(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").strip("[]")
    return host in LOCALHOST_HOSTS


class ComfyClient:
    """Async HTTP+WS client for one ComfyUI instance.

    Construction reads ``COMFYUI_URL`` from the environment if
    ``base_url`` is not supplied; refuses non-localhost without
    ``BRIDGE_ALLOW_REMOTE_COMFY=1``. The client owns one
    ``httpx.AsyncClient`` for the duration of its context-manager
    block; the WS connection is opened lazily per ``stream_progress``
    call.
    """

    def __init__(
        self,
        base_url: str | None = None,
        client_id: str | None = None,
    ) -> None:
        resolved = base_url or os.environ.get(
            "COMFYUI_URL", DEFAULT_COMFYUI_URL
        )
        if not _is_localhost(resolved):
            if os.environ.get("BRIDGE_ALLOW_REMOTE_COMFY") != "1":
                raise RemoteComfyRefused(
                    f"ComfyUI URL {resolved!r} is not localhost. "
                    "Set BRIDGE_ALLOW_REMOTE_COMFY=1 to permit remote "
                    "execution (Hard Rule §15 in "
                    "BRIDGE_BUILD_MISSION_v3_2.md)."
                )
        self.base_url = resolved.rstrip("/")
        self.client_id = client_id or uuid.uuid4().hex
        self._http: httpx.AsyncClient | None = None
        self._object_info_cache: dict | None = None

    async def __aenter__(self) -> "ComfyClient":
        self._http = httpx.AsyncClient(base_url=self.base_url, timeout=30.0)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        if self._http is not None:
            await self._http.aclose()
            self._http = None
        return False

    def _require_http(self) -> httpx.AsyncClient:
        if self._http is None:
            raise RuntimeError(
                "ComfyClient used outside its async with-block; "
                "call `async with ComfyClient() as c:` first."
            )
        return self._http

    def refresh_schema(self) -> None:
        """Invalidate the cached ``/object_info`` map."""
        self._object_info_cache = None

    async def get_object_info(self) -> dict:
        """Fetch (or return cached) ComfyUI node schema map.

        The map is large (multi-MB). Cache invalidation is explicit
        via ``refresh_schema()`` — ComfyUI's custom-node loading can
        mutate the schema between calls, so callers responsible for
        catching that should refresh before the next validation pass.
        """
        if self._object_info_cache is None:
            resp = await self._require_http().get("/object_info")
            resp.raise_for_status()
            self._object_info_cache = resp.json()
        return self._object_info_cache

    async def post_prompt(
        self, workflow_api: dict, client_id: str | None = None
    ) -> str:
        """Submit a workflow to ComfyUI's queue. Returns ``prompt_id``."""
        payload = {
            "prompt": workflow_api,
            "client_id": client_id or self.client_id,
        }
        resp = await self._require_http().post("/prompt", json=payload)
        resp.raise_for_status()
        body = resp.json()
        prompt_id = body.get("prompt_id")
        if not prompt_id:
            raise RuntimeError(
                f"ComfyUI /prompt returned no prompt_id: {body!r}"
            )
        return prompt_id

    async def get_history(self, prompt_id: str) -> dict:
        """Fetch the history record for a prompt id (may be empty
        until the run completes)."""
        resp = await self._require_http().get(f"/history/{prompt_id}")
        resp.raise_for_status()
        return resp.json()

    async def interrupt(self) -> None:
        """Interrupt the currently-executing prompt on the server."""
        resp = await self._require_http().post("/interrupt")
        resp.raise_for_status()

    async def delete_queue_item(self, prompt_id: str) -> None:
        """Remove a queued (not-yet-running) prompt from the queue."""
        resp = await self._require_http().post(
            "/queue", json={"delete": [prompt_id]}
        )
        resp.raise_for_status()

    async def stream_progress(
        self, prompt_id: str
    ) -> AsyncIterator[dict]:
        """Yield parsed progress events for ``prompt_id`` from the WS.

        ComfyUI emits a stream of events (executing, progress, executed,
        execution_cached, execution_error, status). We yield all of them
        and exit when we see ``executing`` with ``node=None`` for our
        prompt id (ComfyUI's "done" signal) or ``execution_error``.
        """
        ws_url = self.base_url.replace("http", "ws", 1) + (
            f"/ws?clientId={self.client_id}"
        )
        async with websockets.connect(ws_url) as ws:
            async for raw in ws:
                if isinstance(raw, (bytes, bytearray)):
                    # ComfyUI also sends binary preview frames; skip.
                    continue
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    _logger.warning(
                        "ComfyClient.stream_progress: non-JSON WS frame"
                    )
                    continue
                yield event
                if (
                    event.get("type") == "executing"
                    and event.get("data", {}).get("node") is None
                    and event.get("data", {}).get("prompt_id") == prompt_id
                ):
                    return
                if event.get("type") == "execution_error" and (
                    event.get("data", {}).get("prompt_id") == prompt_id
                ):
                    return
