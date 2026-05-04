"""Tool surface exposed to review-agent calls.

Six tools, all hermetic, all validated:

- ``read_file(path)``           — file content; path must be inside repo root
- ``list_files(dir, glob)``     — directory enumeration
- ``grep(pattern, glob)``       — ripgrep-style search
- ``run_check(cmd)``            — strict allowlist; no shell, no pipes
- ``pytest_run(test_expr)``     — structured pytest result
- ``record_finding(...)``       — only state-mutating tool; validates path/lines

Validators raise ``ToolValidationError``; the orchestrator translates
that to an Anthropic ``tool_result`` with ``is_error=True``. The agent
sees a structured rejection, not a Python traceback.

Hard caps per expert per iteration are enforced by the orchestrator's
``ToolRunner`` (25 calls, 50 KB cumulative read).
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .artifacts import append_finding, repo_root
from .schemas import (
    Confidence,
    Finding,
    FindingStatus,
    HistoryEntry,
    IterationKind,
    Severity,
    make_finding_id,
)

VALID_SEVERITIES: frozenset[str] = frozenset(
    ["critical", "high", "medium", "low", "nit"]
)
VALID_CONFIDENCES: frozenset[str] = frozenset(
    ["proven", "suspected", "speculative"]
)
VALID_STATUSES: frozenset[str] = frozenset(
    ["open", "confirmed", "refuted", "refined", "dropped", "v1-candidate", "final"]
)
VALID_ARTICLES: frozenset[str] = frozenset(
    f"§R{i}" for i in range(1, 14)
)

# run_check allowlist: each entry is the required first token.
# Subsequent tokens validated by per-command guards below.
_RUN_CHECK_ALLOWLIST: frozenset[str] = frozenset(
    ["pytest", "python", "ls", "wc", "git"]
)
_FORBIDDEN_TOKENS: frozenset[str] = frozenset(
    ["&&", "||", ";", "|", ">", "<", "`", "$(", "&"]
)


class ToolValidationError(ValueError):
    """Raised when a tool input fails validation.

    The orchestrator catches this and surfaces the message to the agent
    via an ``is_error=True`` tool_result. The agent learns the rule and
    self-corrects on the next attempt.
    """


@dataclass(frozen=True)
class ToolBudget:
    """Per-expert per-iteration budget."""

    max_calls: int = 25
    max_read_bytes: int = 50_000


@dataclass
class ToolRunner:
    """Stateful tool dispatcher with budget tracking."""

    run_id: str
    iteration: int
    iteration_kind: IterationKind
    actor: str
    budget: ToolBudget = ToolBudget()
    calls: int = 0
    bytes_read: int = 0

    def dispatch(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Run one tool call. Returns ``{content, is_error}``."""
        self.calls += 1
        if self.calls > self.budget.max_calls:
            return _err(
                f"tool budget exhausted: {self.calls} > {self.budget.max_calls} "
                "calls per expert per iteration"
            )
        try:
            if name == "read_file":
                return self._read_file(args)
            if name == "list_files":
                return self._list_files(args)
            if name == "grep":
                return self._grep(args)
            if name == "run_check":
                return self._run_check(args)
            if name == "pytest_run":
                return self._pytest_run(args)
            if name == "record_finding":
                return self._record_finding(args)
            return _err(f"unknown tool: {name}")
        except ToolValidationError as e:
            return _err(str(e))

    def _read_file(self, args: dict[str, Any]) -> dict[str, Any]:
        path = _resolved_repo_path(args.get("path", ""))
        if not path.is_file():
            raise ToolValidationError(f"not a file: {path}")
        data = path.read_bytes()
        self.bytes_read += len(data)
        if self.bytes_read > self.budget.max_read_bytes:
            return _err(
                f"read budget exhausted: {self.bytes_read} > "
                f"{self.budget.max_read_bytes} bytes per expert per iteration"
            )
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as e:
            raise ToolValidationError(f"not utf-8: {e}")
        return _ok(text)

    def _list_files(self, args: dict[str, Any]) -> dict[str, Any]:
        directory = _resolved_repo_path(args.get("dir", "."))
        if not directory.is_dir():
            raise ToolValidationError(f"not a directory: {directory}")
        glob = args.get("glob") or "*"
        if not isinstance(glob, str) or len(glob) > 200:
            raise ToolValidationError("glob must be str <= 200 chars")
        entries = sorted(str(p.relative_to(repo_root())) for p in directory.glob(glob))
        return _ok("\n".join(entries) if entries else "(no matches)")

    def _grep(self, args: dict[str, Any]) -> dict[str, Any]:
        pattern = args.get("pattern", "")
        if not isinstance(pattern, str) or not pattern or len(pattern) > 200:
            raise ToolValidationError("pattern must be str of length 1..200")
        glob = args.get("glob") or "."
        if not isinstance(glob, str) or len(glob) > 200:
            raise ToolValidationError("glob must be str <= 200 chars")
        target = _resolved_repo_path(glob)
        proc = subprocess.run(
            ["grep", "-rn", "--", pattern, str(target)],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if proc.returncode not in (0, 1):
            raise ToolValidationError(
                f"grep failed: rc={proc.returncode} stderr={proc.stderr[:200]}"
            )
        return _ok(proc.stdout or "(no matches)")

    def _run_check(self, args: dict[str, Any]) -> dict[str, Any]:
        cmd = args.get("cmd", "")
        if not isinstance(cmd, str) or not cmd:
            raise ToolValidationError("cmd must be non-empty str")
        if len(cmd) > 500:
            raise ToolValidationError("cmd too long (>500 chars)")
        for forbidden in _FORBIDDEN_TOKENS:
            if forbidden in cmd:
                raise ToolValidationError(
                    f"forbidden shell token in cmd: {forbidden!r}"
                )
        tokens = cmd.split()
        if tokens[0] not in _RUN_CHECK_ALLOWLIST:
            raise ToolValidationError(
                f"first token {tokens[0]!r} not in allowlist "
                f"{sorted(_RUN_CHECK_ALLOWLIST)}"
            )
        # Per-command guards
        if tokens[0] == "python" and (
            len(tokens) < 2 or tokens[1] != "-m" or len(tokens) < 3
            or tokens[2] != "py_compile"
        ):
            raise ToolValidationError(
                "only `python -m py_compile <file>` is allowed under `python`"
            )
        if tokens[0] == "git" and tokens[1:2] and tokens[1] not in (
            "log", "blame", "diff", "status"
        ):
            raise ToolValidationError(
                "git subcommand must be one of log/blame/diff/status"
            )
        proc = subprocess.run(
            tokens,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
            cwd=str(repo_root()),
        )
        return _ok(
            f"$ {cmd}\n--- stdout ---\n{proc.stdout}"
            f"\n--- stderr ---\n{proc.stderr}\n--- rc={proc.returncode} ---"
        )

    def _pytest_run(self, args: dict[str, Any]) -> dict[str, Any]:
        expr = args.get("test_expr") or ""
        if expr and (not isinstance(expr, str) or len(expr) > 200):
            raise ToolValidationError("test_expr must be str <= 200 chars")
        cmd = ["pytest", "--no-header", "-q"]
        if expr:
            cmd += ["-k", expr]
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
            cwd=str(repo_root()),
        )
        # pytest exit codes: 0 ok, 1 failures, 2 interrupted, 3 internal,
        # 4 usage, 5 no tests collected
        result = {
            "rc": proc.returncode,
            "passed": proc.returncode == 0,
            "stdout": proc.stdout[-4000:],
            "stderr": proc.stderr[-2000:],
        }
        return _ok(json.dumps(result, ensure_ascii=False, indent=2))

    def _record_finding(self, args: dict[str, Any]) -> dict[str, Any]:
        # Required fields
        for required in (
            "severity", "confidence", "file", "line_start", "line_end",
            "title", "claim", "evidence_quote", "remediation",
            "constitution_articles",
        ):
            if required not in args:
                raise ToolValidationError(f"missing required arg: {required}")
        severity = _validate_severity(args["severity"])
        confidence = _validate_confidence(args["confidence"])
        status: FindingStatus = args.get("status") or "open"
        if status not in VALID_STATUSES:
            raise ToolValidationError(
                f"status must be one of {sorted(VALID_STATUSES)}; got {status!r}"
            )
        file_path = _resolved_repo_path(args["file"])
        if not file_path.is_file():
            raise ToolValidationError(f"file does not exist: {args['file']}")
        rel_file = str(file_path.relative_to(repo_root()))
        line_start = int(args["line_start"])
        line_end = int(args["line_end"])
        if line_start < 1 or line_end < line_start:
            raise ToolValidationError(
                f"invalid line range: start={line_start} end={line_end}"
            )
        try:
            num_lines = sum(1 for _ in open(file_path, "r", encoding="utf-8"))
        except UnicodeDecodeError as e:
            raise ToolValidationError(f"file not utf-8: {e}")
        if line_end > num_lines:
            raise ToolValidationError(
                f"line_end {line_end} exceeds file length {num_lines}"
            )
        articles = args["constitution_articles"]
        if not isinstance(articles, list) or not articles:
            raise ToolValidationError(
                "constitution_articles must be a non-empty list"
            )
        for article in articles:
            if article not in VALID_ARTICLES:
                raise ToolValidationError(
                    f"invalid constitution article {article!r}; "
                    f"must be one of {sorted(VALID_ARTICLES)}"
                )
        title = _validate_str(args["title"], "title", 1, 200)
        claim = _validate_str(args["claim"], "claim", 1, 2000)
        evidence_quote = _validate_str(
            args["evidence_quote"], "evidence_quote", 1, 4000
        )
        remediation = _validate_str(
            args["remediation"], "remediation", 1, 4000
        )

        finding_id = make_finding_id(rel_file, line_start, claim)
        history = (
            HistoryEntry(
                iteration=self.iteration,
                kind=self.iteration_kind,
                actor=self.actor,
                action="record",
                note=f"introduced as {status} ({confidence})",
            ),
        )
        finding = Finding(
            id=finding_id,
            iter_introduced=self.iteration,
            expert=self.actor,
            severity=severity,
            confidence=confidence,
            file=rel_file,
            line_start=line_start,
            line_end=line_end,
            title=title,
            claim=claim,
            evidence_quote=evidence_quote,
            remediation=remediation,
            constitution_articles=tuple(articles),
            status=status,
            history=history,
        )
        append_finding(self.run_id, finding)
        return _ok(
            json.dumps(
                {"recorded_id": finding_id, "status": status},
                ensure_ascii=False,
            )
        )


def tool_specs() -> list[dict[str, Any]]:
    """Anthropic tool definitions for review agents."""
    return [
        {
            "name": "read_file",
            "description": (
                "Read a UTF-8 file inside the repository. Path must be "
                "absolute or relative to the repo root."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute or repo-relative path",
                    }
                },
                "required": ["path"],
            },
        },
        {
            "name": "list_files",
            "description": "List files in a directory (optional glob).",
            "input_schema": {
                "type": "object",
                "properties": {
                    "dir": {"type": "string", "description": "Repo-relative dir"},
                    "glob": {
                        "type": "string",
                        "description": "Optional glob pattern; default '*'",
                    },
                },
                "required": ["dir"],
            },
        },
        {
            "name": "grep",
            "description": (
                "Recursive grep -rn for a pattern under a directory or file."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "glob": {
                        "type": "string",
                        "description": "Repo-relative dir or file; default '.'",
                    },
                },
                "required": ["pattern"],
            },
        },
        {
            "name": "run_check",
            "description": (
                "Run an allowlisted command inside the repo root. "
                "Allowlist: pytest, python -m py_compile, ls, wc, "
                "git log/blame/diff/status. No shell, no pipes."
            ),
            "input_schema": {
                "type": "object",
                "properties": {"cmd": {"type": "string"}},
                "required": ["cmd"],
            },
        },
        {
            "name": "pytest_run",
            "description": (
                "Run pytest (optionally narrowed by -k expression) and "
                "return structured result {rc, passed, stdout, stderr}."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "test_expr": {
                        "type": "string",
                        "description": "Optional pytest -k expression",
                    }
                },
            },
        },
        {
            "name": "record_finding",
            "description": (
                "Record a single review finding. Validates that the file "
                "exists, line range is in bounds, and constitution_articles "
                "are real §R numbers. This is the only state-mutating tool."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "severity": {
                        "type": "string",
                        "enum": sorted(VALID_SEVERITIES),
                    },
                    "confidence": {
                        "type": "string",
                        "enum": sorted(VALID_CONFIDENCES),
                    },
                    "status": {
                        "type": "string",
                        "enum": sorted(VALID_STATUSES),
                        "description": "Default 'open'",
                    },
                    "file": {"type": "string"},
                    "line_start": {"type": "integer", "minimum": 1},
                    "line_end": {"type": "integer", "minimum": 1},
                    "title": {"type": "string"},
                    "claim": {"type": "string"},
                    "evidence_quote": {"type": "string"},
                    "remediation": {"type": "string"},
                    "constitution_articles": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of §R1..§R13 supporting this finding",
                    },
                },
                "required": [
                    "severity",
                    "confidence",
                    "file",
                    "line_start",
                    "line_end",
                    "title",
                    "claim",
                    "evidence_quote",
                    "remediation",
                    "constitution_articles",
                ],
            },
        },
    ]


# --- helpers ---------------------------------------------------------------


def _ok(content: str) -> dict[str, Any]:
    return {"content": content, "is_error": False}


def _err(msg: str) -> dict[str, Any]:
    return {"content": f"ToolValidationError: {msg}", "is_error": True}


def _resolved_repo_path(raw: str) -> Path:
    """Resolve ``raw`` against repo root and reject escapes."""
    if not isinstance(raw, str) or not raw:
        raise ToolValidationError("path must be non-empty str")
    p = Path(raw)
    if not p.is_absolute():
        p = repo_root() / p
    p = p.resolve()
    root = repo_root().resolve()
    try:
        p.relative_to(root)
    except ValueError:
        raise ToolValidationError(
            f"path {raw!r} resolves outside repo root {root}"
        )
    return p


def _validate_severity(value: object) -> Severity:
    if value not in VALID_SEVERITIES:
        raise ToolValidationError(
            f"severity must be one of {sorted(VALID_SEVERITIES)}; got {value!r}"
        )
    return value  # type: ignore[return-value]


def _validate_confidence(value: object) -> Confidence:
    if value not in VALID_CONFIDENCES:
        raise ToolValidationError(
            f"confidence must be one of {sorted(VALID_CONFIDENCES)}; "
            f"got {value!r}"
        )
    return value  # type: ignore[return-value]


def _validate_str(value: object, name: str, lo: int, hi: int) -> str:
    if not isinstance(value, str):
        raise ToolValidationError(f"{name} must be str; got {type(value).__name__}")
    if len(value) < lo or len(value) > hi:
        raise ToolValidationError(
            f"{name} length {len(value)} not in [{lo}, {hi}]"
        )
    return value

