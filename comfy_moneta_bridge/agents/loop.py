"""Internal Anthropic-SDK Claude loop — one ``RoleDriver`` implementation.

Implements ``orchestrator.RoleDriver`` against the Anthropic Messages
API. Each ``step()`` call:

  1. Sends the role's system prompt + transcript-derived messages.
  2. Receives a ``Message`` containing zero-or-more ``tool_use`` blocks.
  3. Translates those into ``ToolCall`` objects.
  4. Extracts the assistant's text and (for CRITIC) the verdict.

The orchestrator dispatches the tool calls and feeds back the
results on the next turn. Prompt caching is enabled on the system
prompt and the constitution so we don't repay tokens for the
identical constitutional preamble on every turn.

Model defaults to ``claude-opus-4-7`` per AGENTS.md §5.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

from comfy_moneta_bridge.agents.orchestrator import (
    RoleTurnOutput,
    ToolCall,
)
from comfy_moneta_bridge.agents.tools import as_anthropic_tools

_logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-opus-4-7"


def _extract_verdict(text: str) -> str | None:
    """Pull ACCEPT / REJECT / ABORT from the CRITIC's free text."""
    upper = text.strip().upper()
    for token in ("ACCEPT", "REJECT", "ABORT"):
        if upper.startswith(token):
            return text.strip().split("\n", 1)[0]
        if f"\n{token}" in upper or f". {token}" in upper:
            return token
    return None


@dataclass
class ClaudeRoleDriver:
    """Anthropic-SDK-backed RoleDriver."""

    model: str = DEFAULT_MODEL
    max_tokens: int = 4096
    api_key: str | None = None

    def __post_init__(self) -> None:
        try:
            from anthropic import AsyncAnthropic
        except ImportError as e:
            raise RuntimeError(
                "bridge orchestrate requires the 'anthropic' extra. "
                "Install with: pip install 'comfy-moneta-bridge[agents]'"
            ) from e
        self._client = AsyncAnthropic(
            api_key=self.api_key or os.environ.get("ANTHROPIC_API_KEY")
        )
        self._tools = as_anthropic_tools()

    async def step(
        self,
        role: str,
        system_prompt: str,
        goal: str,
        transcript: list[dict],
    ) -> RoleTurnOutput:
        messages = self._messages_for(role, goal, transcript)
        # Enable prompt caching on the (long, stable) system prompt.
        # Cache breakpoints land on the constitution + role charter so
        # we don't repay tokens on every turn.
        resp = await self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=[
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                },
            ],
            tools=self._tools,
            messages=messages,
        )

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in resp.content:
            btype = getattr(block, "type", None)
            if btype == "text":
                text_parts.append(getattr(block, "text", ""))
            elif btype == "tool_use":
                tool_calls.append(
                    ToolCall(
                        id=getattr(block, "id", ""),
                        name=getattr(block, "name", ""),
                        args=dict(getattr(block, "input", {}) or {}),
                    )
                )

        text = "\n".join(text_parts).strip()
        verdict = _extract_verdict(text) if role == "CRITIC" else None
        return RoleTurnOutput(
            text=text, tool_calls=tool_calls, verdict=verdict
        )

    def _messages_for(
        self, role: str, goal: str, transcript: list[dict]
    ) -> list[dict]:
        """Build the messages payload from prior transcript entries.

        Keeps it simple for v0.2: the goal is the first user message;
        each subsequent transcript entry becomes one user-message
        summary so the model sees the cross-role context. This is
        intentionally not a full tool-use loop (we don't replay
        ``tool_use`` / ``tool_result`` blocks back to Claude) because
        each role is a one-shot turn — the orchestrator drives the
        sequence externally.
        """
        history = [{"role": "user", "content": f"Goal: {goal}"}]
        for entry in transcript[-20:]:
            text = self._format_transcript_entry(entry)
            if text:
                history.append({"role": "user", "content": text})
        history.append(
            {
                "role": "user",
                "content": (
                    f"You are the {role}. Produce your turn now per "
                    "your role charter."
                ),
            }
        )
        return history

    @staticmethod
    def _format_transcript_entry(entry: dict) -> str:
        if "tool" in entry and "result" in entry:
            return (
                f"[{entry.get('role')}] called {entry['tool']!r} → "
                f"{entry['result']!r}"
            )
        if "tool_error" in entry:
            return (
                f"[{entry.get('role')}] tool {entry.get('tool')!r} "
                f"refused: {entry['tool_error']}"
            )
        if "output" in entry:
            return f"[{entry.get('role')}] {entry['output']}"
        return ""


async def orchestrate(
    goal: str,
    session: str,
    moneta_storage_path,
    state_dir,
    cozy_root=None,
    *,
    model: str = DEFAULT_MODEL,
    max_steps: int = 12,
    interactive: bool = False,
) -> None:
    """CLI entry point: drives one orchestration with the Claude driver.

    Imports the ComfyClient lazily and only constructs it if
    ``COMFYUI_URL`` resolves cleanly (Hard Rule §15 raises here if
    the env points at a non-localhost without opt-in).
    """
    from comfy_moneta_bridge.agents.client import ComfyClient
    from comfy_moneta_bridge.agents.orchestrator import Orchestrator

    driver = ClaudeRoleDriver(model=model)

    async def _ack(plan_text: str) -> bool:
        # Interactive ack is CLI-driven; the CLI command wraps this
        # with a prompt(). For the library-level API, default to True
        # unless the operator overrides ack_planner.
        print("--- PLANNER plan ---")
        print(plan_text)
        try:
            ans = input(
                "Proceed to MUTATOR/EXECUTOR? [y/N] "
            ).strip().lower()
        except EOFError:
            return False
        return ans in ("y", "yes")

    async def _client_factory():
        return ComfyClient()

    orch = Orchestrator(
        driver=driver,
        moneta_storage_path=moneta_storage_path,
        state_dir=state_dir,
        cozy_root=cozy_root,
        max_turns=max_steps,
        client_factory=_client_factory,
        interactive=interactive,
        ack_planner=_ack if interactive else None,
    )
    result = await orch.run(goal, session=session)
    print(
        f"\n--- orchestration complete ---\n"
        f"goal_id={result.goal_id}\n"
        f"final_role={result.final_role}\n"
        f"verdict={result.critic_verdict}\n"
        f"prompt_id={result.prompt_id}\n"
        f"completed={result.completed}"
    )
