"""Tests for ``comfy_moneta_bridge.agents.client``.

Skipped cleanly if the ``[agents]`` extras (httpx + websockets) are
not installed.

CRUCIBLE bias: the remote-refusal test is the canary for Hard Rule
§15 — the construction must fail before any network attempt. The
object_info caching test asserts at most one outbound request for
two consecutive ``get_object_info()`` calls.
"""

from __future__ import annotations

import pytest

pytest.importorskip("httpx")
pytest.importorskip("websockets")

from comfy_moneta_bridge.agents.client import (  # noqa: E402
    DEFAULT_COMFYUI_URL,
    ComfyClient,
    RemoteComfyRefused,
    _is_localhost,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test starts with no ComfyUI env vars set."""
    monkeypatch.delenv("COMFYUI_URL", raising=False)
    monkeypatch.delenv("BRIDGE_ALLOW_REMOTE_COMFY", raising=False)


def test_localhost_default_no_env() -> None:
    c = ComfyClient()
    assert c.base_url == DEFAULT_COMFYUI_URL.rstrip("/")


def test_localhost_env_url() -> None:
    c = ComfyClient(base_url="http://localhost:8188")
    assert c.base_url == "http://localhost:8188"


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8188",
        "http://localhost:8188",
        "http://[::1]:8188",
    ],
)
def test_is_localhost_accepts_loopback(url: str) -> None:
    assert _is_localhost(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "http://192.0.2.1:8188",
        "http://example.com",
        "http://10.0.0.5:8188",
    ],
)
def test_is_localhost_rejects_remote(url: str) -> None:
    assert _is_localhost(url) is False


def test_remote_refused_without_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COMFYUI_URL", "http://192.0.2.1:8188")
    with pytest.raises(RemoteComfyRefused) as excinfo:
        ComfyClient()
    msg = str(excinfo.value)
    assert "BRIDGE_ALLOW_REMOTE_COMFY" in msg
    assert "§15" in msg or "v3_2" in msg


def test_remote_allowed_with_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COMFYUI_URL", "http://192.0.2.1:8188")
    monkeypatch.setenv("BRIDGE_ALLOW_REMOTE_COMFY", "1")
    c = ComfyClient()
    assert c.base_url == "http://192.0.2.1:8188"


def test_explicit_base_url_overrides_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COMFYUI_URL", "http://192.0.2.1:8188")
    monkeypatch.setenv("BRIDGE_ALLOW_REMOTE_COMFY", "1")
    c = ComfyClient(base_url="http://127.0.0.1:9999")
    assert c.base_url == "http://127.0.0.1:9999"


def test_client_id_generated() -> None:
    a = ComfyClient()
    b = ComfyClient()
    assert a.client_id != b.client_id


def test_client_id_explicit() -> None:
    c = ComfyClient(client_id="fixed-id")
    assert c.client_id == "fixed-id"


def test_require_http_outside_context_raises() -> None:
    c = ComfyClient()
    with pytest.raises(RuntimeError) as excinfo:
        c._require_http()
    assert "with-block" in str(excinfo.value)


@pytest.mark.asyncio
async def test_get_object_info_caches(httpx_mock) -> None:
    httpx_mock.add_response(
        url="http://127.0.0.1:8188/object_info",
        json={"KSampler": {"input": {"required": {}}}},
    )
    async with ComfyClient() as c:
        first = await c.get_object_info()
        second = await c.get_object_info()
    assert first == second
    # Only one network call despite two get_object_info() calls.
    assert len(httpx_mock.get_requests()) == 1


@pytest.mark.asyncio
async def test_refresh_schema_invalidates_cache(httpx_mock) -> None:
    httpx_mock.add_response(
        url="http://127.0.0.1:8188/object_info",
        json={"KSampler": {}},
    )
    httpx_mock.add_response(
        url="http://127.0.0.1:8188/object_info",
        json={"KSampler": {}, "NewNode": {}},
    )
    async with ComfyClient() as c:
        await c.get_object_info()
        c.refresh_schema()
        second = await c.get_object_info()
    assert "NewNode" in second
    assert len(httpx_mock.get_requests()) == 2


@pytest.mark.asyncio
async def test_post_prompt_returns_prompt_id(httpx_mock) -> None:
    httpx_mock.add_response(
        url="http://127.0.0.1:8188/prompt",
        json={"prompt_id": "abc-123", "number": 1, "node_errors": {}},
    )
    async with ComfyClient(client_id="cid") as c:
        prompt_id = await c.post_prompt({"3": {"class_type": "KSampler"}})
    assert prompt_id == "abc-123"


@pytest.mark.asyncio
async def test_post_prompt_missing_id_raises(httpx_mock) -> None:
    httpx_mock.add_response(
        url="http://127.0.0.1:8188/prompt",
        json={"node_errors": {"some": "error"}},
    )
    async with ComfyClient() as c:
        with pytest.raises(RuntimeError) as excinfo:
            await c.post_prompt({})
    assert "prompt_id" in str(excinfo.value)


@pytest.mark.asyncio
async def test_get_history(httpx_mock) -> None:
    httpx_mock.add_response(
        url="http://127.0.0.1:8188/history/abc",
        json={"abc": {"status": "success"}},
    )
    async with ComfyClient() as c:
        h = await c.get_history("abc")
    assert h == {"abc": {"status": "success"}}


@pytest.mark.asyncio
async def test_interrupt(httpx_mock) -> None:
    httpx_mock.add_response(
        url="http://127.0.0.1:8188/interrupt",
        method="POST",
        json={},
    )
    async with ComfyClient() as c:
        await c.interrupt()
    reqs = httpx_mock.get_requests()
    assert len(reqs) == 1
    assert reqs[0].method == "POST"


@pytest.mark.asyncio
async def test_delete_queue_item(httpx_mock) -> None:
    httpx_mock.add_response(
        url="http://127.0.0.1:8188/queue",
        method="POST",
        json={},
    )
    async with ComfyClient() as c:
        await c.delete_queue_item("abc-123")
    reqs = httpx_mock.get_requests()
    assert len(reqs) == 1
    body = reqs[0].read()
    assert b"abc-123" in body


# ─── stream_progress (WS path) + await_result ─────────────────────────

import json  # noqa: E402

from comfy_moneta_bridge.agents import client as client_mod  # noqa: E402


class _FakeWS:
    """Async-context-manager + async-iterator standing in for a
    websockets connection. Yields the canned frames then stops."""

    def __init__(self, frames: list[str]) -> None:
        self._frames = list(frames)

    async def __aenter__(self) -> "_FakeWS":
        return self

    async def __aexit__(self, *a) -> bool:
        return False

    def __aiter__(self) -> "_FakeWS":
        return self

    async def __anext__(self) -> str:
        if not self._frames:
            raise StopAsyncIteration
        return self._frames.pop(0)


def _fake_connect(frames: list[str]):
    def _connect(url, *a, **kw):
        return _FakeWS(frames)
    return _connect


@pytest.mark.asyncio
async def test_stream_progress_terminates_on_executing_done(
    monkeypatch,
) -> None:
    frames = [
        json.dumps({"type": "status", "data": {}}),
        json.dumps({"type": "executing",
                    "data": {"node": "3", "prompt_id": "p1"}}),
        json.dumps({"type": "executing",
                    "data": {"node": None, "prompt_id": "p1"}}),
        json.dumps({"type": "should_not_be_reached", "data": {}}),
    ]
    monkeypatch.setattr(client_mod.websockets, "connect",
                        _fake_connect(frames))
    async with ComfyClient() as c:
        events = [e async for e in c.stream_progress("p1")]
    # Stops at the terminal node=None event; the trailing frame is
    # never yielded.
    assert len(events) == 3
    assert events[-1]["data"]["node"] is None


@pytest.mark.asyncio
async def test_stream_progress_terminates_on_execution_error(
    monkeypatch,
) -> None:
    frames = [
        json.dumps({"type": "executing",
                    "data": {"node": "3", "prompt_id": "p1"}}),
        json.dumps({"type": "execution_error",
                    "data": {"prompt_id": "p1"}}),
        json.dumps({"type": "should_not_be_reached", "data": {}}),
    ]
    monkeypatch.setattr(client_mod.websockets, "connect",
                        _fake_connect(frames))
    async with ComfyClient() as c:
        events = [e async for e in c.stream_progress("p1")]
    assert events[-1]["type"] == "execution_error"
    assert len(events) == 2


@pytest.mark.asyncio
async def test_stream_progress_skips_binary_and_bad_json(
    monkeypatch,
) -> None:
    frames = [
        b"\x00\x01binary-preview-frame",
        "not json at all",
        json.dumps({"type": "executing",
                    "data": {"node": None, "prompt_id": "p1"}}),
    ]
    monkeypatch.setattr(client_mod.websockets, "connect",
                        _fake_connect(frames))
    async with ComfyClient() as c:
        events = [e async for e in c.stream_progress("p1")]
    # Binary + bad-json frames are skipped; only the terminal event
    # is yielded.
    assert events == [{"type": "executing",
                       "data": {"node": None, "prompt_id": "p1"}}]


@pytest.mark.asyncio
async def test_await_result_short_circuit_when_already_complete(
    httpx_mock,
) -> None:
    httpx_mock.add_response(
        url="http://127.0.0.1:8188/history/p1",
        json={"p1": {"status": {"status_str": "success", "completed": True}}},
    )
    async with ComfyClient() as c:
        result = await c.await_result("p1")
    assert result["status"] == "success"
    assert result["prompt_id"] == "p1"
    # Only the one history call — no WS connection needed.
    assert len(httpx_mock.get_requests()) == 1


@pytest.mark.asyncio
async def test_await_result_streams_then_success(
    httpx_mock, monkeypatch,
) -> None:
    httpx_mock.add_response(
        url="http://127.0.0.1:8188/history/p1", json={},
    )
    httpx_mock.add_response(
        url="http://127.0.0.1:8188/history/p1",
        json={"p1": {"status": {"status_str": "success"}}},
    )
    frames = [json.dumps({"type": "executing",
                          "data": {"node": None, "prompt_id": "p1"}})]
    monkeypatch.setattr(client_mod.websockets, "connect",
                        _fake_connect(frames))
    async with ComfyClient() as c:
        result = await c.await_result("p1", timeout_s=5)
    assert result["status"] == "success"


@pytest.mark.asyncio
async def test_await_result_streams_then_error(
    httpx_mock, monkeypatch,
) -> None:
    httpx_mock.add_response(
        url="http://127.0.0.1:8188/history/p1", json={},
    )
    httpx_mock.add_response(
        url="http://127.0.0.1:8188/history/p1",
        json={"p1": {"status": {"status_str": "error"}}},
    )
    frames = [json.dumps({"type": "execution_error",
                          "data": {"prompt_id": "p1"}})]
    monkeypatch.setattr(client_mod.websockets, "connect",
                        _fake_connect(frames))
    async with ComfyClient() as c:
        result = await c.await_result("p1", timeout_s=5)
    assert result["status"] == "error"


@pytest.mark.asyncio
async def test_await_result_timeout(httpx_mock, monkeypatch) -> None:
    import asyncio

    httpx_mock.add_response(
        url="http://127.0.0.1:8188/history/p1", json={},
    )
    httpx_mock.add_response(
        url="http://127.0.0.1:8188/history/p1", json={},
    )

    async def _hang(prompt_id):
        await asyncio.sleep(10)
        yield {}

    async with ComfyClient() as c:
        monkeypatch.setattr(c, "stream_progress", _hang)
        result = await c.await_result("p1", timeout_s=0.05)
    assert result["status"] == "timeout"
