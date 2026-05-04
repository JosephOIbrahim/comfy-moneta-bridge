"""Thin async Anthropic SDK wrapper for the review harness.

Targets ``claude-opus-4-7`` exclusively, with adaptive thinking and
``effort=xhigh`` (the recommended setting for coding / agentic use cases
on Opus 4.7).

Caching strategy — three breakpoints, prefix-stable, in render order
(``tools`` -> ``system`` -> ``messages``):

  1. **system prompt** — ``REVIEW_CONSTITUTION.md`` + expert role
     definition. Cached per role; reused across all 5 iterations.
  2. **user-turn repo block** — the full repository contents
     (~80 KB) injected as the first user-turn block. Cached once per
     run; every expert call in every iteration reads it.
  3. **prior-iteration findings snapshot** — passed as a second
     user-turn block on iterations 2-5; cached at the iteration
     boundary.

Opus 4.7 specifics (per ``claude-api`` skill 2026-04 cache):
  - No ``temperature`` / ``top_p`` / ``top_k`` (400 if sent).
  - No ``budget_tokens`` (400 if sent); use ``thinking={"type":
    "adaptive"}``.
  - ``thinking.display="summarized"`` to surface reasoning to logs.
  - Streaming required for ``max_tokens >= 16000``; we stream every
    call.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

import anthropic

MODEL = "claude-opus-4-7"

# Opus 4.7 pricing per 1M tokens (cached 2026-04 in claude-api skill).
_INPUT_PRICE_PER_MTOK = 5.00
_OUTPUT_PRICE_PER_MTOK = 25.00
_CACHE_WRITE_MULTIPLIER = 1.25  # 5-minute TTL
_CACHE_READ_MULTIPLIER = 0.10


@dataclass(frozen=True)
class CallUsage:
    """Per-Anthropic-call usage + cost summary."""

    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int

    @property
    def total_input_tokens(self) -> int:
        return (
            self.input_tokens
            + self.cache_creation_input_tokens
            + self.cache_read_input_tokens
        )

    def est_cost_usd(self) -> float:
        cost = (
            self.input_tokens * _INPUT_PRICE_PER_MTOK
            + self.cache_creation_input_tokens
            * _INPUT_PRICE_PER_MTOK
            * _CACHE_WRITE_MULTIPLIER
            + self.cache_read_input_tokens
            * _INPUT_PRICE_PER_MTOK
            * _CACHE_READ_MULTIPLIER
            + self.output_tokens * _OUTPUT_PRICE_PER_MTOK
        ) / 1_000_000
        return cost


@dataclass
class CallResult:
    """Outcome of a single ``run_agent_turn`` call."""

    text: str
    stop_reason: str
    usage: CallUsage
    duration_seconds: float
    request_id: str
    tool_calls_made: int


def _client() -> anthropic.AsyncAnthropic:
    """Construct an ``AsyncAnthropic`` client.

    Reads ``ANTHROPIC_API_KEY`` from the environment. The client is
    cheap to construct; the orchestrator builds one per run and reuses
    it across all expert calls.
    """
    return anthropic.AsyncAnthropic()


def system_blocks_with_cache(text: str) -> list[dict[str, Any]]:
    """System content with a cache breakpoint on the last block.

    ``REVIEW_CONSTITUTION.md`` + the role definition is large and
    stable for the whole run; mark it ephemeral.
    """
    return [
        {
            "type": "text",
            "text": text,
            "cache_control": {"type": "ephemeral"},
        }
    ]


def user_block_with_cache(text: str) -> dict[str, Any]:
    """User content block with an ephemeral cache breakpoint."""
    return {
        "type": "text",
        "text": text,
        "cache_control": {"type": "ephemeral"},
    }


def user_block(text: str) -> dict[str, Any]:
    """User content block with no cache breakpoint."""
    return {"type": "text", "text": text}


async def run_agent_turn(
    client: anthropic.AsyncAnthropic,
    *,
    system: str,
    user_blocks: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    tool_dispatch: Callable[[str, dict[str, Any]], dict[str, Any]],
    max_tokens: int = 16000,
    max_tool_iterations: int = 30,
) -> CallResult:
    """Run one agent turn (one expert, one iteration) to completion.

    Manual agentic loop. Loops until ``stop_reason == "end_turn"`` or
    ``max_tool_iterations`` is reached. Each iteration:
      1. Stream a Messages API call.
      2. Persist ``response.content`` (preserves tool_use blocks).
      3. Dispatch every ``tool_use`` block to ``tool_dispatch`` and
         append ``tool_result`` blocks back as a ``user`` turn.
      4. Repeat.

    ``tool_dispatch`` is the only side-channel: ``record_finding``
    persists findings; everything else is read-only.

    Streaming is mandatory at this ``max_tokens`` per the SDK timeout
    guard. We use ``messages.stream()`` and pull the final message at
    the end.
    """
    started = time.monotonic()
    system_content = system_blocks_with_cache(system)
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": user_blocks}
    ]

    last_message = None
    request_id = ""
    total_usage = CallUsage(0, 0, 0, 0)
    tool_calls_made = 0
    final_text = ""

    for _ in range(max_tool_iterations):
        async with client.messages.stream(
            model=MODEL,
            max_tokens=max_tokens,
            system=system_content,
            tools=tools,
            messages=messages,
            thinking={"type": "adaptive", "display": "summarized"},
            output_config={"effort": "xhigh"},
        ) as stream:
            response = await stream.get_final_message()

        last_message = response
        request_id = getattr(response, "_request_id", "") or ""
        total_usage = _accumulate_usage(total_usage, response.usage)

        # Persist assistant turn (preserves tool_use blocks).
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason != "tool_use":
            final_text = _extract_text(response.content)
            break

        # Dispatch tool calls and append tool_results as a user turn.
        tool_results: list[dict[str, Any]] = []
        for block in response.content:
            if getattr(block, "type", None) != "tool_use":
                continue
            tool_calls_made += 1
            result = tool_dispatch(block.name, dict(block.input))
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result["content"],
                    "is_error": result.get("is_error", False),
                }
            )
        if not tool_results:
            # Defensive: stop_reason said tool_use but we got none.
            final_text = _extract_text(response.content)
            break
        messages.append({"role": "user", "content": tool_results})

    duration = time.monotonic() - started
    stop_reason = getattr(last_message, "stop_reason", "unknown") or "unknown"
    return CallResult(
        text=final_text,
        stop_reason=stop_reason,
        usage=total_usage,
        duration_seconds=duration,
        request_id=request_id,
        tool_calls_made=tool_calls_made,
    )


def _accumulate_usage(prev: CallUsage, raw: object) -> CallUsage:
    return CallUsage(
        input_tokens=prev.input_tokens + (getattr(raw, "input_tokens", 0) or 0),
        output_tokens=prev.output_tokens
        + (getattr(raw, "output_tokens", 0) or 0),
        cache_creation_input_tokens=prev.cache_creation_input_tokens
        + (getattr(raw, "cache_creation_input_tokens", 0) or 0),
        cache_read_input_tokens=prev.cache_read_input_tokens
        + (getattr(raw, "cache_read_input_tokens", 0) or 0),
    )


def _extract_text(content: list[Any]) -> str:
    parts: list[str] = []
    for block in content:
        if getattr(block, "type", None) == "text":
            parts.append(block.text)
    return "\n\n".join(parts).strip()
