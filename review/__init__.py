"""Multi-agent code review harness for comfy-moneta-bridge.

Loads ``REVIEW_CONSTITUTION.md`` as system-prompt fragment, runs a team
of expert review agents on Claude Opus 4.7 across 5 typed iterations
(DISCOVERY -> CROSS-EXAMINATION -> DEEP-DIVE -> ADVERSARIAL ->
RANK&REPORT), and produces a ranked, evidence-cited, severity-calibrated
final report at ``.claude/reviews/runs/<ts>/FINAL_REPORT.md``.

Not shipped in the wheel; the project's ``packages = ["comfy_moneta_bridge"]``
declaration in ``pyproject.toml`` excludes this package by design.
"""

from __future__ import annotations

__version__ = "0.1.0"
