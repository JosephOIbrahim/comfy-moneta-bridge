"""Iteration-4 adversarial critic.

Per the constitution's routing logic, the critic in iter 4 scores
every surviving finding against every §R article. Findings that no
article supports are dropped. Findings whose remediation requires a
Hard Rule breach are re-filed as ``status=v1-candidate``.

The critic uses the same ``run_agent_turn`` infrastructure as the
experts, but its system prompt forbids it from inventing new findings
— it only mutates status of existing ones via ``record_finding``.
"""

from __future__ import annotations

from .experts import role_prompt
from .schemas import Finding


def critic_role() -> str:
    return "critic"


def critic_system() -> str:
    return role_prompt("critic")


def render_findings_for_critic(findings: list[Finding]) -> str:
    """Render findings as the critic's input."""
    if not findings:
        return "(no findings to score)"
    out: list[str] = ["# Findings to score against the constitution\n"]
    for f in findings:
        out.append(f"## {f.id}: {f.title}")
        out.append(f"- expert: `{f.expert}`")
        out.append(f"- severity: `{f.severity}`")
        out.append(f"- confidence: `{f.confidence}`")
        out.append(f"- status: `{f.status}`")
        out.append(f"- file: `{f.file}:{f.line_start}-{f.line_end}`")
        out.append(
            f"- constitution_articles: {', '.join(f.constitution_articles)}"
        )
        out.append("")
        out.append(f"**Claim:** {f.claim}")
        out.append("")
        out.append("**Evidence:**")
        out.append("```")
        out.append(f.evidence_quote)
        out.append("```")
        out.append("")
        out.append(f"**Remediation:** {f.remediation}")
        out.append("\n---\n")
    return "\n".join(out)
