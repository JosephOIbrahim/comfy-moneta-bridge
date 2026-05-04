"""Tests for the deterministic synthesizer dedupe + ranker."""

from __future__ import annotations

from review.schemas import Finding, HistoryEntry
from review.synthesizer import (
    dedupe_findings,
    rank_findings,
    render_findings_markdown,
)


def _f(
    *,
    fid: str = "abc",
    expert: str = "architect",
    severity: str = "medium",
    confidence: str = "suspected",
    file: str = "comfy_moneta_bridge/state.py",
    line_start: int = 10,
    claim: str = "claim text",
    articles: tuple[str, ...] = ("§R1",),
    status: str = "confirmed",
) -> Finding:
    return Finding(
        id=fid,
        iter_introduced=1,
        expert=expert,
        severity=severity,  # type: ignore[arg-type]
        confidence=confidence,  # type: ignore[arg-type]
        file=file,
        line_start=line_start,
        line_end=line_start + 2,
        title=f"finding {fid}",
        claim=claim,
        evidence_quote="evidence",
        remediation="do thing",
        constitution_articles=articles,
        status=status,  # type: ignore[arg-type]
        history=(
            HistoryEntry(
                iteration=1,
                kind="discovery",
                actor=expert,
                action="record",
                note="introduced",
            ),
        ),
    )


def test_dedupe_collapses_close_lines_same_claim():
    a = _f(fid="a", line_start=10, claim="same claim")
    b = _f(fid="b", line_start=12, claim="same claim")  # within 5
    out = dedupe_findings([a, b])
    assert len(out) == 1


def test_dedupe_keeps_distant_lines_separate():
    a = _f(fid="a", line_start=10, claim="same claim")
    b = _f(fid="b", line_start=80, claim="same claim")
    out = dedupe_findings([a, b])
    assert len(out) == 2


def test_dedupe_keeps_higher_severity():
    a = _f(fid="a", severity="low")
    b = _f(fid="b", severity="critical")
    out = dedupe_findings([a, b])
    assert len(out) == 1
    assert out[0].severity == "critical"


def test_dedupe_unions_constitution_articles():
    a = _f(fid="a", articles=("§R1",))
    b = _f(fid="b", articles=("§R2", "§R3"))
    out = dedupe_findings([a, b])
    assert set(out[0].constitution_articles) == {"§R1", "§R2", "§R3"}


def test_rank_orders_critical_before_low():
    low = _f(fid="a", severity="low")
    crit = _f(fid="b", severity="critical")
    out = rank_findings([low, crit])
    assert out[0].severity == "critical"
    assert out[1].severity == "low"


def test_rank_within_severity_uses_priority_order():
    style = _f(fid="a", severity="medium", expert="docs-vs-code")
    reliability = _f(fid="b", severity="medium", expert="reliability")
    out = rank_findings([style, reliability])
    assert out[0].expert == "reliability"


def test_render_empty_findings_includes_R8_clause():
    md = render_findings_markdown([], run_id="test")
    assert "No surviving findings" in md
    assert "§R8" in md


def test_render_drops_dropped_status():
    kept = _f(fid="kept", status="confirmed")
    dropped = _f(fid="dropped", status="dropped")
    md = render_findings_markdown([kept, dropped], run_id="test")
    assert "kept" in md
    assert "finding dropped" not in md


def test_render_groups_by_severity():
    md = render_findings_markdown(
        [
            _f(fid="a", severity="critical", claim="critical claim", line_start=10),
            _f(fid="b", severity="low", claim="low claim", line_start=80),
        ],
        run_id="test",
    )
    assert "## CRITICAL" in md
    assert "## LOW" in md
