"""Validator tests for review/tools.py.

Per the plan's verification section: ``record_finding`` with a
nonexistent file raises; ``read_file`` with ``..`` raises;
``run_check`` with ``&&`` raises.

These tests do not call Anthropic. They exercise the validators in
isolation against the real repository on disk.
"""

from __future__ import annotations

from review.artifacts import repo_root
from review.tools import ToolBudget, ToolRunner, tool_specs


def _runner(tmp_run_id: str = "test-run") -> ToolRunner:
    return ToolRunner(
        run_id=tmp_run_id,
        iteration=0,
        iteration_kind="discovery",
        actor="architect",
        budget=ToolBudget(max_calls=99, max_read_bytes=1_000_000),
    )


# --- tool_specs basic shape ------------------------------------------------


def test_tool_specs_includes_all_six():
    names = {t["name"] for t in tool_specs()}
    assert names == {
        "read_file",
        "list_files",
        "grep",
        "run_check",
        "pytest_run",
        "record_finding",
    }


def test_tool_specs_record_finding_requires_constitution_articles():
    spec = next(t for t in tool_specs() if t["name"] == "record_finding")
    required = set(spec["input_schema"]["required"])
    assert "constitution_articles" in required
    assert "evidence_quote" in required
    assert "remediation" in required


# --- read_file -------------------------------------------------------------


def test_read_file_repo_relative_succeeds():
    runner = _runner()
    out = runner.dispatch("read_file", {"path": "pyproject.toml"})
    assert out["is_error"] is False
    assert "[project]" in out["content"]


def test_read_file_outside_repo_root_rejected():
    runner = _runner()
    out = runner.dispatch("read_file", {"path": "../../etc/passwd"})
    assert out["is_error"] is True
    assert "outside repo root" in out["content"]


def test_read_file_dotdot_traversal_rejected():
    runner = _runner()
    out = runner.dispatch("read_file", {"path": "comfy_moneta_bridge/../../"})
    # Resolves under repo_root().parent.parent, which is outside.
    assert out["is_error"] is True


def test_read_file_missing_file_rejected():
    runner = _runner()
    out = runner.dispatch("read_file", {"path": "no_such_file.txt"})
    assert out["is_error"] is True
    assert "not a file" in out["content"]


def test_read_file_missing_path_arg_rejected():
    runner = _runner()
    out = runner.dispatch("read_file", {})
    assert out["is_error"] is True


# --- list_files ------------------------------------------------------------


def test_list_files_lists_package():
    runner = _runner()
    out = runner.dispatch(
        "list_files", {"dir": "comfy_moneta_bridge", "glob": "*.py"}
    )
    assert out["is_error"] is False
    assert "comfy_moneta_bridge/state.py" in out["content"]


def test_list_files_outside_repo_rejected():
    runner = _runner()
    out = runner.dispatch("list_files", {"dir": "../"})
    assert out["is_error"] is True


# --- grep ------------------------------------------------------------------


def test_grep_finds_pattern():
    runner = _runner()
    out = runner.dispatch(
        "grep",
        {"pattern": "run_sleep_pass", "glob": "comfy_moneta_bridge"},
    )
    assert out["is_error"] is False
    assert "run_sleep_pass" in out["content"]


def test_grep_empty_pattern_rejected():
    runner = _runner()
    out = runner.dispatch("grep", {"pattern": ""})
    assert out["is_error"] is True


def test_grep_pattern_too_long_rejected():
    runner = _runner()
    out = runner.dispatch("grep", {"pattern": "x" * 201})
    assert out["is_error"] is True


# --- run_check -------------------------------------------------------------


def test_run_check_ls_succeeds():
    runner = _runner()
    out = runner.dispatch("run_check", {"cmd": "ls"})
    assert out["is_error"] is False
    assert "pyproject.toml" in out["content"]


def test_run_check_rejects_pipe():
    runner = _runner()
    out = runner.dispatch("run_check", {"cmd": "ls | grep py"})
    assert out["is_error"] is True
    assert "forbidden shell token" in out["content"]


def test_run_check_rejects_and_chain():
    runner = _runner()
    out = runner.dispatch("run_check", {"cmd": "ls && rm -rf /"})
    assert out["is_error"] is True
    assert "forbidden shell token" in out["content"]


def test_run_check_rejects_redirect():
    runner = _runner()
    out = runner.dispatch("run_check", {"cmd": "ls > out.txt"})
    assert out["is_error"] is True


def test_run_check_rejects_command_substitution():
    runner = _runner()
    out = runner.dispatch("run_check", {"cmd": "ls $(whoami)"})
    assert out["is_error"] is True


def test_run_check_rejects_non_allowlisted_first_token():
    runner = _runner()
    out = runner.dispatch("run_check", {"cmd": "rm -rf /"})
    assert out["is_error"] is True
    assert "not in allowlist" in out["content"]


def test_run_check_python_only_py_compile_allowed():
    runner = _runner()
    out = runner.dispatch("run_check", {"cmd": "python -c 'print(1)'"})
    assert out["is_error"] is True
    assert "py_compile" in out["content"]


def test_run_check_git_subcommand_restricted():
    runner = _runner()
    out = runner.dispatch("run_check", {"cmd": "git push origin main"})
    assert out["is_error"] is True
    assert "git subcommand must be one of" in out["content"]


def test_run_check_git_log_allowed():
    runner = _runner()
    out = runner.dispatch("run_check", {"cmd": "git log --oneline -1"})
    assert out["is_error"] is False


# --- record_finding --------------------------------------------------------


def _record_finding_args(**overrides):
    base = {
        "severity": "medium",
        "confidence": "suspected",
        "file": "comfy_moneta_bridge/state.py",
        "line_start": 1,
        "line_end": 5,
        "title": "test finding",
        "claim": "test claim",
        "evidence_quote": "some code here",
        "remediation": "do the thing",
        "constitution_articles": ["§R1"],
    }
    base.update(overrides)
    return base


def test_record_finding_rejects_missing_file():
    runner = _runner()
    out = runner.dispatch(
        "record_finding",
        _record_finding_args(file="no_such_file.py"),
    )
    assert out["is_error"] is True
    assert "file does not exist" in out["content"]


def test_record_finding_rejects_line_out_of_bounds():
    runner = _runner()
    out = runner.dispatch(
        "record_finding",
        _record_finding_args(line_start=99999, line_end=99999),
    )
    assert out["is_error"] is True
    assert "exceeds file length" in out["content"]


def test_record_finding_rejects_invalid_line_range():
    runner = _runner()
    out = runner.dispatch(
        "record_finding",
        _record_finding_args(line_start=10, line_end=5),
    )
    assert out["is_error"] is True
    assert "invalid line range" in out["content"]


def test_record_finding_rejects_invalid_severity():
    runner = _runner()
    out = runner.dispatch(
        "record_finding", _record_finding_args(severity="catastrophic")
    )
    assert out["is_error"] is True
    assert "severity must be one of" in out["content"]


def test_record_finding_rejects_invalid_confidence():
    runner = _runner()
    out = runner.dispatch(
        "record_finding", _record_finding_args(confidence="vibes")
    )
    assert out["is_error"] is True


def test_record_finding_rejects_unknown_constitution_article():
    runner = _runner()
    out = runner.dispatch(
        "record_finding",
        _record_finding_args(constitution_articles=["§R99"]),
    )
    assert out["is_error"] is True
    assert "invalid constitution article" in out["content"]


def test_record_finding_rejects_empty_constitution_articles():
    runner = _runner()
    out = runner.dispatch(
        "record_finding", _record_finding_args(constitution_articles=[])
    )
    assert out["is_error"] is True
    assert "non-empty list" in out["content"]


def test_record_finding_rejects_non_list_constitution_articles():
    runner = _runner()
    out = runner.dispatch(
        "record_finding",
        _record_finding_args(constitution_articles="§R1"),
    )
    assert out["is_error"] is True


def test_record_finding_rejects_path_outside_repo():
    runner = _runner()
    out = runner.dispatch(
        "record_finding", _record_finding_args(file="../../etc/passwd")
    )
    assert out["is_error"] is True


def test_record_finding_succeeds_and_returns_id(tmp_path, monkeypatch):
    # Direct findings.jsonl writes into a tmp dir to keep the test
    # hermetic from the real .claude/reviews/.
    import review.artifacts as artifacts_mod

    monkeypatch.setattr(
        artifacts_mod, "runs_root", lambda: tmp_path / "runs"
    )
    runner = _runner(tmp_run_id="hermetic-test")
    out = runner.dispatch("record_finding", _record_finding_args())
    assert out["is_error"] is False, out["content"]
    import json

    parsed = json.loads(out["content"])
    assert "recorded_id" in parsed
    assert parsed["status"] == "open"


# --- budget enforcement ----------------------------------------------------


def test_call_budget_enforced():
    runner = ToolRunner(
        run_id="x",
        iteration=0,
        iteration_kind="discovery",
        actor="architect",
        budget=ToolBudget(max_calls=2, max_read_bytes=1_000_000),
    )
    a = runner.dispatch("read_file", {"path": "pyproject.toml"})
    b = runner.dispatch("read_file", {"path": "pyproject.toml"})
    c = runner.dispatch("read_file", {"path": "pyproject.toml"})
    assert a["is_error"] is False
    assert b["is_error"] is False
    assert c["is_error"] is True
    assert "tool budget exhausted" in c["content"]


def test_read_byte_budget_enforced():
    runner = ToolRunner(
        run_id="x",
        iteration=0,
        iteration_kind="discovery",
        actor="architect",
        budget=ToolBudget(max_calls=99, max_read_bytes=10),
    )
    out = runner.dispatch("read_file", {"path": "pyproject.toml"})
    assert out["is_error"] is True
    assert "read budget exhausted" in out["content"]


# --- repo_root sanity ------------------------------------------------------


def test_repo_root_resolves_to_project():
    root = repo_root()
    assert (root / "pyproject.toml").is_file()
    assert (root / "comfy_moneta_bridge").is_dir()
    assert (root / "REVIEW_CONSTITUTION.md").is_file()
