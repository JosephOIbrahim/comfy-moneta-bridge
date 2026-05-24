"""Unit tests for the Claude loop driver.

We don't hit the real Anthropic API. Instead we mock the response
shape and assert that ``ClaudeRoleDriver.step()`` parses content
blocks, extracts text + tool_use → ``ToolCall``, and surfaces CRITIC
verdicts via ``_extract_verdict``.
"""

from __future__ import annotations

import pytest

pytest.importorskip("anthropic")

from comfy_moneta_bridge.agents.loop import (  # noqa: E402
    ClaudeRoleDriver,
    DEFAULT_MODEL,
    _extract_verdict,
)


def test_default_model() -> None:
    assert DEFAULT_MODEL == "claude-opus-4-7"


def test_extract_verdict_accept() -> None:
    assert _extract_verdict("ACCEPT") == "ACCEPT"
    assert _extract_verdict("ACCEPT: looks great") == "ACCEPT: looks great"


def test_extract_verdict_reject() -> None:
    assert _extract_verdict("REJECT: too blurry") == "REJECT: too blurry"


def test_extract_verdict_abort() -> None:
    assert _extract_verdict("ABORT") == "ABORT"


def test_extract_verdict_in_body() -> None:
    text = "Considered the result.\nABORT: hardware error"
    assert _extract_verdict(text) == "ABORT"


def test_extract_verdict_none() -> None:
    assert _extract_verdict("hmm, hard to say") is None


def test_extract_verdict_empty() -> None:
    assert _extract_verdict("") is None


@pytest.mark.asyncio
async def test_step_parses_text_and_tool_use(monkeypatch) -> None:
    """Mock the Anthropic client to return a canned response."""

    class _Block:
        def __init__(self, **kw):
            for k, v in kw.items():
                setattr(self, k, v)

    class _Resp:
        def __init__(self, content):
            self.content = content

    class _Messages:
        async def create(self, **kw):
            return _Resp(
                content=[
                    _Block(type="text", text="The plan is:"),
                    _Block(
                        type="tool_use", id="call_1",
                        name="workflow_load",
                        input={"workflow": {"1": {"class_type": "K"}}},
                    ),
                ]
            )

    class _Client:
        messages = _Messages()

    driver = ClaudeRoleDriver(api_key="dummy")
    driver._client = _Client()  # type: ignore[assignment]

    out = await driver.step("PLANNER", "system", "goal", [])
    assert "plan" in out.text.lower()
    assert len(out.tool_calls) == 1
    assert out.tool_calls[0].name == "workflow_load"
    assert out.tool_calls[0].args == {
        "workflow": {"1": {"class_type": "K"}}
    }


@pytest.mark.asyncio
async def test_step_extracts_critic_verdict(monkeypatch) -> None:
    class _Block:
        def __init__(self, **kw):
            for k, v in kw.items():
                setattr(self, k, v)

    class _Resp:
        def __init__(self, content):
            self.content = content

    class _Messages:
        async def create(self, **kw):
            return _Resp(content=[_Block(type="text", text="ACCEPT: ship it")])

    class _Client:
        messages = _Messages()

    driver = ClaudeRoleDriver(api_key="dummy")
    driver._client = _Client()  # type: ignore[assignment]

    out = await driver.step("CRITIC", "system", "goal", [])
    assert out.verdict and "ACCEPT" in out.verdict


def test_format_transcript_entry_tool_result() -> None:
    text = ClaudeRoleDriver._format_transcript_entry(
        {"role": "MUTATOR", "tool": "workflow_mutate_node", "result": {"ok": True}}
    )
    assert "MUTATOR" in text and "workflow_mutate_node" in text


def test_format_transcript_entry_tool_error() -> None:
    text = ClaudeRoleDriver._format_transcript_entry(
        {
            "role": "EXECUTOR",
            "tool": "workflow_submit",
            "tool_error": "validation failed",
        }
    )
    assert "refused" in text and "validation" in text


def test_format_transcript_entry_text_output() -> None:
    text = ClaudeRoleDriver._format_transcript_entry(
        {"role": "PLANNER", "output": "the plan"}
    )
    assert "PLANNER" in text and "plan" in text


def test_format_transcript_entry_unknown_returns_empty() -> None:
    assert ClaudeRoleDriver._format_transcript_entry({}) == ""
