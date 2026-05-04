"""Iteration-5 synthesizer + final report renderer.

Two responsibilities:

1. ``run_synthesis`` calls Claude as the synthesizer agent (system
   prompt from ``experts.role_prompt('synthesizer')``) with the
   surviving findings and produces the prose final report.
2. ``render_findings_markdown`` is a deterministic, tool-free renderer
   used as a fallback (and as the input to the synthesizer) — applies
   the §R9 priority order and per-severity grouping without an LLM.

Dedupe: collapse findings that share an ``id_window`` value (file +
``line_start // 5 * 5`` + claim hash). When merging, keep the higher
severity, the higher confidence, and the union of
``constitution_articles``.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace

from .schemas import (
    Confidence,
    Finding,
    Severity,
    id_window,
)

# §R9 priority order: lower index = higher priority.
_EXPERT_PRIORITY: dict[str, int] = {
    "reliability": 0,         # correctness / data-loss
    "frozen-boundaries": 1,   # rule-correctness
    "security": 2,
    "performance": 3,
    "architect": 4,           # maintainability
    "testing": 4,             # maintainability
    "docs-vs-code": 5,        # documentation
    "synthesizer": 9,
    "critic": 9,
}

_SEVERITY_RANK: dict[Severity, int] = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
    "nit": 4,
}

_CONFIDENCE_RANK: dict[Confidence, int] = {
    "proven": 0,
    "suspected": 1,
    "speculative": 2,
}


def dedupe_findings(findings: list[Finding]) -> list[Finding]:
    """Collapse near-duplicates by ``id_window`` key.

    Two findings collapse if (file, line_start // 5 * 5, claim hash)
    matches. Merge by keeping the higher severity, higher confidence,
    and union of constitution articles.
    """
    buckets: dict[str, list[Finding]] = defaultdict(list)
    for f in findings:
        key = id_window(f.file, f.line_start, f.claim)
        buckets[key].append(f)
    merged: list[Finding] = []
    for group in buckets.values():
        if len(group) == 1:
            merged.append(group[0])
            continue
        # Sort by (severity, confidence) ascending = best first.
        group.sort(
            key=lambda f: (
                _SEVERITY_RANK[f.severity],
                _CONFIDENCE_RANK[f.confidence],
            )
        )
        winner = group[0]
        articles = set()
        for f in group:
            articles.update(f.constitution_articles)
        merged.append(
            replace(winner, constitution_articles=tuple(sorted(articles)))
        )
    return merged


def rank_findings(findings: list[Finding]) -> list[Finding]:
    """Apply §R9 priority + severity + confidence ranking."""
    def _key(f: Finding) -> tuple[int, int, int]:
        return (
            _SEVERITY_RANK[f.severity],
            _EXPERT_PRIORITY.get(f.expert, 5),
            _CONFIDENCE_RANK[f.confidence],
        )

    return sorted(findings, key=_key)


def render_findings_markdown(
    findings: list[Finding], *, run_id: str
) -> str:
    """Deterministic markdown renderer.

    Used as the synthesizer's input (so the LLM receives a structured
    starting point, not a raw JSON dump) and as the persisted final
    report fallback.
    """
    surviving = [
        f
        for f in findings
        if f.status not in ("dropped", "refuted")
    ]
    deduped = dedupe_findings(surviving)
    ranked = rank_findings(deduped)

    lines: list[str] = [
        f"# Final Review Report — {run_id}",
        "",
        f"Total findings: {len(ranked)}  ",
        f"(deduped from {len(surviving)} surviving / {len(findings)} raw)",
        "",
        "Severity is intra-category. The §R9 priority order ",
        "(correctness > security > reliability > performance > ",
        "maintainability > style) is applied via expert lane within ",
        "each severity bucket.",
        "",
    ]
    by_severity: dict[Severity, list[Finding]] = defaultdict(list)
    for f in ranked:
        by_severity[f.severity].append(f)
    for severity in ("critical", "high", "medium", "low", "nit"):
        bucket = by_severity.get(severity, [])  # type: ignore[arg-type]
        if not bucket:
            continue
        lines.append(f"## {severity.upper()} ({len(bucket)})")
        lines.append("")
        for f in bucket:
            lines.extend(_render_one(f))
            lines.append("")
    if not ranked:
        lines.append("## No surviving findings")
        lines.append("")
        lines.append(
            "Per §R8, an empty findings set is a valid output. "
            "All raw findings were dropped or refuted."
        )
    return "\n".join(lines)


def _render_one(f: Finding) -> list[str]:
    return [
        f"### {f.title}",
        "",
        f"- **id**: `{f.id}`",
        f"- **severity**: `{f.severity}`  **confidence**: `{f.confidence}`",
        f"- **expert**: `{f.expert}`  **status**: `{f.status}`",
        f"- **file**: `{f.file}:{f.line_start}-{f.line_end}`",
        f"- **constitution**: {', '.join(f.constitution_articles)}",
        "",
        f"**Claim.** {f.claim}",
        "",
        "**Evidence.**",
        "",
        "```",
        f.evidence_quote,
        "```",
        "",
        f"**Remediation.** {f.remediation}",
    ]
