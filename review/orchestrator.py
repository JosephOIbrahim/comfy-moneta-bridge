"""5-iteration review loop with Mixture-of-Experts parallelism.

Iteration contract (per the constitution's routing logic):

  ITER 1  DISCOVERY        — all 7 experts in parallel, no prior context
  ITER 2  CROSS-EXAMINATION — each expert scores OTHERS' findings
  ITER 3  DEEP-DIVE         — confirming experts produce repro + patch
  ITER 4  ADVERSARIAL       — critic scores against every §R article
  ITER 5  RANK & REPORT     — synthesizer renders FINAL_REPORT.md

Concurrency: per the plan, ``asyncio.gather`` with a semaphore of 4
for the parallel-expert phase. Each expert call is a ``run_agent_turn``
to completion (manual agentic loop with tool use).

State durability: ``state.json`` is written *before* each Anthropic
call (mirrors ``state.py``'s pattern), so a crash mid-call resumes
idempotently — the next run picks up at the same iteration without
double-spending.

Cost control: ``RunConfig.max_cost_usd`` is checked before every
Anthropic call. If the cumulative est. cost would exceed the cap, the
orchestrator halts cleanly and writes a partial state.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path

import anthropic

from . import _anthropic as ant
from . import critic as critic_mod
from . import experts as experts_mod
from .artifacts import (
    append_trace,
    iter_dir,
    new_run_id,
    read_findings,
    read_state,
    repo_root,
    run_dir,
    runs_root,
    write_final_report,
    write_iter_artifact,
    write_state,
)
from .constitution import load as load_constitution
from .schemas import (
    EXPERT_ROLES,
    ITERATION_ORDER,
    Finding,
    IterationKind,
    RunState,
    TraceEntry,
)
from .synthesizer import (
    dedupe_findings,
    rank_findings,
    render_findings_markdown,
)
from .tools import ToolRunner, tool_specs


@dataclass
class RunConfig:
    iterations: int = 5
    parallelism: int = 4
    max_cost_usd: float = 10.00
    max_tokens_per_call: int = 32_000


@dataclass
class RunStats:
    total_calls: int = 0
    total_cost_usd: float = 0.0
    aborted_for_cost: bool = False
    aborted_messages: list[str] = field(default_factory=list)


class Orchestrator:
    def __init__(self, config: RunConfig) -> None:
        self.config = config
        self.client = ant._client()
        self.constitution = load_constitution()
        self.repo_block = _build_repo_block()
        self.semaphore = asyncio.Semaphore(config.parallelism)
        self.stats = RunStats()

    async def run_new(self) -> str:
        run_id = new_run_id()
        _print(f"\n=== review run {run_id} ===")
        state = RunState(
            run_id=run_id,
            model=ant.MODEL,
            iterations_planned=self.config.iterations,
            iteration_completed=0,
            last_actor="(none)",
            last_action="created",
        )
        write_state(run_id, state)
        await self._run_iterations(run_id, start_iteration=1)
        _print(f"=== run {run_id} complete ===\n")
        return run_id

    async def resume(self, run_id: str) -> str:
        state = read_state(run_id)
        if state is None:
            raise FileNotFoundError(
                f"no state.json for run {run_id} at {run_dir(run_id)}"
            )
        start = state.iteration_completed + 1
        _print(
            f"\n=== resuming run {run_id} at iter {start} ===\n"
            f"(model={state.model}, last_actor={state.last_actor})\n"
        )
        await self._run_iterations(run_id, start_iteration=start)
        return run_id

    async def _run_iterations(self, run_id: str, *, start_iteration: int) -> None:
        for iteration in range(start_iteration, self.config.iterations + 1):
            kind = ITERATION_ORDER[iteration - 1]
            _print(f"\n--- iter {iteration}: {kind} ---")
            if self.stats.aborted_for_cost:
                _print("aborted for cost; skipping further iterations")
                break
            await self._run_iteration(run_id, iteration, kind)
            self._update_state(
                run_id,
                iteration_completed=iteration,
                actor="orchestrator",
                action=f"iter-{iteration}-{kind}-complete",
            )
        if not self.stats.aborted_for_cost:
            await self._finalize(run_id)

    async def _run_iteration(
        self, run_id: str, iteration: int, kind: IterationKind
    ) -> None:
        if kind == "discovery":
            await self._iter_discovery(run_id, iteration)
            return
        if kind == "cross-examination":
            await self._iter_cross_examination(run_id, iteration)
            return
        if kind == "deep-dive":
            await self._iter_deep_dive(run_id, iteration)
            return
        if kind == "adversarial":
            await self._iter_adversarial(run_id, iteration)
            return
        if kind == "rank-and-report":
            await self._iter_rank_and_report(run_id, iteration)
            return
        raise AssertionError(f"unknown iteration kind: {kind}")

    # ----- iter 1: discovery -------------------------------------------------

    async def _iter_discovery(self, run_id: str, iteration: int) -> None:
        prompt = (
            "ITER 1 / DISCOVERY. Broad sweep. Examine the repository, "
            "identify defects in your domain lane, and record each one "
            "via the `record_finding` tool. Cite `path:line` for every "
            "claim (§R1). Empty findings is a valid output (§R8). Use "
            "`pytest_run` only when reproduction matters (§R10 confidence "
            "uplift)."
        )
        await self._run_experts_parallel(run_id, iteration, prompt)

    # ----- iter 2: cross-examination ----------------------------------------

    async def _iter_cross_examination(
        self, run_id: str, iteration: int
    ) -> None:
        all_findings = read_findings(run_id)
        if not all_findings:
            _print("no findings from iter 1; halting per early-exit rule")
            self.stats.aborted_messages.append("no findings after iter 1")
            self.stats.aborted_for_cost = True  # halts loop
            return

        async def per_expert(role: str) -> None:
            others = [f for f in all_findings if f.expert != role]
            if not others:
                _print(f"  [{role}] no other-expert findings; skipping")
                return
            prompt = (
                f"ITER 2 / CROSS-EXAMINATION. You are `{role}`. Below "
                "are findings filed by OTHER experts in iter 1. For each, "
                "decide: confirm | refute | refine | out-of-scope. Use "
                "`record_finding` to file an updated snapshot for any "
                "finding you wish to mutate (use the SAME file/line/claim "
                "to keep the id stable; set `status` to "
                "`confirmed`/`refuted`/`refined`/`dropped`). §R7 — "
                "challenge prior findings; 'I was wrong' is valid.\n\n"
                + _render_findings_for_review(others)
            )
            await self._run_one_expert(run_id, iteration, role, prompt)

        await self._gather([per_expert(r) for r in EXPERT_ROLES])

    # ----- iter 3: deep-dive ------------------------------------------------

    async def _iter_deep_dive(self, run_id: str, iteration: int) -> None:
        findings = read_findings(run_id)
        confirmed = [
            f
            for f in findings
            if f.status in ("open", "confirmed", "refined")
        ]
        # Group surviving findings by their introducing expert.
        by_expert: dict[str, list[Finding]] = {}
        for f in confirmed:
            by_expert.setdefault(f.expert, []).append(f)
        if not by_expert:
            _print("no surviving findings to deep-dive")
            return

        async def per_expert(role: str) -> None:
            mine = by_expert.get(role, [])
            if not mine:
                return
            prompt = (
                f"ITER 3 / DEEP-DIVE. You are `{role}`. Below are YOUR "
                "surviving findings from prior iterations. For each, "
                "produce a reproduction step + a minimal patch sketch. "
                "Use `pytest_run` or `run_check` to uplift confidence "
                "from `suspected` to `proven` where possible (§R10). "
                "Re-file via `record_finding` with the SAME file/line/"
                "claim and the enriched `remediation` string. Set "
                "`confidence` accurately.\n\n"
                + _render_findings_for_review(mine)
            )
            await self._run_one_expert(run_id, iteration, role, prompt)

        await self._gather([per_expert(r) for r in by_expert])

    # ----- iter 4: adversarial ---------------------------------------------

    async def _iter_adversarial(self, run_id: str, iteration: int) -> None:
        findings = read_findings(run_id)
        surviving = [
            f for f in findings if f.status in ("open", "confirmed", "refined")
        ]
        if not surviving:
            _print("no surviving findings; nothing for the critic to score")
            return
        prompt = (
            "ITER 4 / ADVERSARIAL. You are the CRITIC. Score every "
            "surviving finding below against EVERY §R article. Drop "
            "(`status=dropped`) any finding no article supports. Re-file "
            "(`status=v1-candidate`) any finding whose remediation "
            "requires a Hard Rule §1..§12 breach. Confirm survivors "
            "with `status=confirmed`. Your default vote is `drop`. Re-"
            "file each finding via `record_finding` (SAME file/line/"
            "claim) with the new status; do not invent new findings.\n\n"
            + critic_mod.render_findings_for_critic(surviving)
        )
        await self._run_one_expert(run_id, iteration, "critic", prompt)

    # ----- iter 5: rank & report -------------------------------------------

    async def _iter_rank_and_report(
        self, run_id: str, iteration: int
    ) -> None:
        findings = read_findings(run_id)
        survivors = [
            f
            for f in findings
            if f.status in ("open", "confirmed", "refined", "v1-candidate")
        ]
        baseline = render_findings_markdown(survivors, run_id=run_id)
        # Persist the deterministic baseline first so the artifact
        # exists even if the synthesizer call fails for cost/network
        # reasons.
        write_iter_artifact(run_id, iteration, "synthesizer-baseline", baseline)

        if not survivors:
            _print("no survivors to synthesize; using deterministic baseline")
            write_final_report(run_id, baseline)
            return

        prompt = (
            "ITER 5 / RANK & REPORT. You are the SYNTHESIZER. Below is "
            "a deterministic baseline rendering of the surviving "
            "findings, already deduped and ranked per §R9. Read it and "
            "produce the final markdown report. You may reorder within "
            "severity buckets, sharpen wording, and note cross-cutting "
            "themes — but you may NOT invent new findings, drop "
            "findings without explanation, or weaken the constitution "
            "citations. Output ONLY the final markdown.\n\n"
            "===== BASELINE =====\n\n"
            + baseline
        )
        result = await self._run_one_expert(
            run_id, iteration, "synthesizer", prompt
        )
        report = result.text.strip() if result else baseline
        write_final_report(run_id, report or baseline)

    # ----- shared call paths ------------------------------------------------

    async def _run_experts_parallel(
        self, run_id: str, iteration: int, base_prompt: str
    ) -> None:
        await self._gather(
            [
                self._run_one_expert(run_id, iteration, role, base_prompt)
                for role in EXPERT_ROLES
            ]
        )

    async def _gather(self, coros: list) -> None:
        async def _bounded(coro):
            async with self.semaphore:
                return await coro

        await asyncio.gather(*[_bounded(c) for c in coros])

    async def _run_one_expert(
        self, run_id: str, iteration: int, role: str, prompt: str
    ) -> ant.CallResult | None:
        if self.stats.aborted_for_cost:
            return None
        if self.stats.total_cost_usd >= self.config.max_cost_usd:
            self.stats.aborted_for_cost = True
            self.stats.aborted_messages.append(
                f"cost cap hit before {role} iter {iteration}: "
                f"${self.stats.total_cost_usd:.2f} >= "
                f"${self.config.max_cost_usd:.2f}"
            )
            return None

        kind = ITERATION_ORDER[iteration - 1]
        # Persist state BEFORE the call so a crash resumes correctly.
        self._update_state(
            run_id,
            iteration_completed=iteration - 1,
            actor=role,
            action=f"iter-{iteration}-{kind}-start",
        )

        runner = ToolRunner(
            run_id=run_id,
            iteration=iteration,
            iteration_kind=kind,
            actor=role,
        )
        system = self.constitution + "\n\n---\n\n" + experts_mod.role_prompt(role)
        user_blocks = [
            ant.user_block_with_cache(self.repo_block),
            ant.user_block(prompt),
        ]

        _print(f"  [{role}] starting…")
        try:
            result = await ant.run_agent_turn(
                self.client,
                system=system,
                user_blocks=user_blocks,
                tools=tool_specs(),
                tool_dispatch=runner.dispatch,
                max_tokens=self.config.max_tokens_per_call,
            )
        except anthropic.APIError as e:
            _print(f"  [{role}] APIError: {e}")
            self.stats.aborted_messages.append(f"{role} iter {iteration}: {e}")
            return None

        self.stats.total_calls += 1
        cost = result.usage.est_cost_usd()
        self.stats.total_cost_usd += cost
        _print(
            f"  [{role}] done in {result.duration_seconds:.1f}s "
            f"(tools={result.tool_calls_made} "
            f"in={result.usage.total_input_tokens} "
            f"cache_read={result.usage.cache_read_input_tokens} "
            f"out={result.usage.output_tokens} ${cost:.4f})"
        )

        # Persist artifacts.
        write_iter_artifact(run_id, iteration, role, result.text)
        append_trace(
            run_id,
            TraceEntry(
                iteration=iteration,
                kind=kind,
                actor=role,
                request_id=result.request_id,
                input_tokens=result.usage.input_tokens,
                output_tokens=result.usage.output_tokens,
                cache_read_tokens=result.usage.cache_read_input_tokens,
                cache_creation_tokens=result.usage.cache_creation_input_tokens,
                est_cost_usd=cost,
                duration_seconds=result.duration_seconds,
            ),
        )
        return result

    # ----- finalization -----------------------------------------------------

    async def _finalize(self, run_id: str) -> None:
        # If iter 5 already wrote FINAL_REPORT.md, leave it. Otherwise
        # write the deterministic baseline as a fallback.
        path = run_dir(run_id) / "FINAL_REPORT.md"
        if not path.exists():
            findings = read_findings(run_id)
            survivors = [
                f
                for f in findings
                if f.status
                in ("open", "confirmed", "refined", "v1-candidate")
            ]
            ranked = rank_findings(dedupe_findings(survivors))
            write_final_report(
                run_id, render_findings_markdown(ranked, run_id=run_id)
            )
        _print(
            f"final report: {path}\n"
            f"calls: {self.stats.total_calls}  "
            f"est cost: ${self.stats.total_cost_usd:.4f}"
        )

    def _update_state(
        self, run_id: str, *, iteration_completed: int, actor: str, action: str
    ) -> None:
        state = read_state(run_id) or RunState(
            run_id=run_id,
            model=ant.MODEL,
            iterations_planned=self.config.iterations,
            iteration_completed=0,
            last_actor=actor,
            last_action=action,
        )
        new_state = RunState(
            run_id=state.run_id,
            model=state.model,
            iterations_planned=state.iterations_planned,
            iteration_completed=iteration_completed,
            last_actor=actor,
            last_action=action,
        )
        write_state(run_id, new_state)


# --- helpers ---------------------------------------------------------------


def _print(msg: str) -> None:
    """Stamped stdout print so long runs are followable."""
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def _build_repo_block() -> str:
    """Concatenate the repository's reviewable files as a single block.

    The bridge is small (~2400 LOC code + tests + docs). Bundling it
    once and caching it via ``cache_control: ephemeral`` is cheaper
    than 35+ rounds of ``read_file`` tool calls — and identical bytes
    across calls means cache hits.

    We include: ``comfy_moneta_bridge/`` source, ``tests/`` source,
    ``docs/`` markdown, the top-level constitution / mission /
    architecture markdown, ``pyproject.toml``, and ``README.md``.
    """
    root = repo_root()
    parts: list[str] = ["# Repository contents (review target)\n"]
    paths: list[Path] = []
    for sub in ("comfy_moneta_bridge", "tests", "scripts"):
        d = root / sub
        if d.is_dir():
            paths.extend(sorted(p for p in d.rglob("*.py") if p.is_file()))
    docs_dir = root / "docs"
    if docs_dir.is_dir():
        paths.extend(sorted(p for p in docs_dir.rglob("*.md") if p.is_file()))
    for top_md in (
        "README.md",
        "AGENT_COMMANDMENTS.md",
        "MONETA_API_SCOUT.md",
        "BRIDGE_BUILD_MISSION_v3_1.md",
    ):
        p = root / top_md
        if p.is_file():
            paths.append(p)
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        paths.append(pyproject)
    for path in paths:
        rel = path.relative_to(root)
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        parts.append(f"\n----- {rel} -----\n```\n{text}\n```\n")
    return "".join(parts)


def list_runs() -> list[str]:
    root = runs_root()
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir())


def _render_findings_for_review(findings: list[Finding]) -> str:
    """Render a structured findings list for an expert to review."""
    if not findings:
        return "(no findings)"
    out: list[str] = []
    for f in findings:
        out.append(
            f"- id=`{f.id}`  expert=`{f.expert}`  severity=`{f.severity}`  "
            f"confidence=`{f.confidence}`  status=`{f.status}`"
        )
        out.append(f"  file: `{f.file}:{f.line_start}-{f.line_end}`")
        out.append(f"  title: {f.title}")
        out.append(f"  claim: {f.claim}")
        out.append(
            f"  remediation: {f.remediation[:300]}"
            + ("…" if len(f.remediation) > 300 else "")
        )
        out.append("")
    return "\n".join(out)
