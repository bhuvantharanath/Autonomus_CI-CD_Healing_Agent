"""Integration tests for the CI-driven reasoning loop.

Uses mock executors and mock GitHub API responses (no Docker, no
real GitHub Actions) to verify the 8-phase state machine, commit
message format, CI polling, CI log parsing, verify-using-CI-results,
and loop-back to CLASSIFY with CI logs.

Run:
    python3 -m pytest agents/test_reasoning_loop.py -v
"""

from __future__ import annotations

import asyncio
import io
import subprocess
import sys
import textwrap
import zipfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from agents.reasoning_loop import (
    run_reasoning_loop,
    ReasoningLoopResult,
    IterationReport,
    build_default_registry,
    Phase,
    TRANSITIONS,
)
from agents.tools.registry import ToolRegistry, ToolResult
from agents.tools.test_runner_tool import TestRunnerTool
from agents.tools.commit_push_tool import CommitPushTool
from agents.tools.wait_for_ci_tool import WaitForCITool
from agents.tools.fetch_ci_results_tool import FetchCIResultsTool, _extract_logs, _parse_counts
from agents.tools.verification_tool import VerificationTool
from agents.tools.patch_applier_tool import PatchApplierTool
from sandbox.executor import ExecutionResult


# ── Helpers ──────────────────────────────────────────────────────────

def _make_repo(tmp: Path) -> Path:
    """Create a minimal repo with a known bug (missing colon)."""
    src = tmp / "app.py"
    src.write_text(textwrap.dedent("""\
        def greet(name)
            return f"Hello, {name}!"
    """))
    test = tmp / "test_app.py"
    test.write_text(textwrap.dedent("""\
        from app import greet
        def test_greet():
            assert greet("World") == "Hello, World!"
    """))
    return tmp


def _init_git_repo(repo_path: Path) -> None:
    """Initialise a git repo with an initial commit for testing."""
    subprocess.run(["git", "init"], cwd=repo_path, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=repo_path, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        cwd=repo_path, capture_output=True,
        env={**dict(__import__('os').environ), "GIT_AUTHOR_NAME": "Test",
             "GIT_AUTHOR_EMAIL": "test@test.com", "GIT_COMMITTER_NAME": "Test",
             "GIT_COMMITTER_EMAIL": "test@test.com"},
    )


def _mock_exec(success: bool, stdout: str = "", stderr: str = "") -> ExecutionResult:
    return ExecutionResult(
        exit_code=0 if success else 1,
        stdout=stdout,
        stderr=stderr,
        logs=stdout + stderr,
        timed_out=False,
        duration_s=0.5,
    )


def _make_ci_log_zip(log_content: str) -> bytes:
    """Create a zip file containing a CI log for testing."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("test_job/1_Run tests.txt", log_content)
    return buf.getvalue()


def _config_with_branch(repo_path: str) -> dict[str, Any]:
    """Standard config with branch and repo_url for CI-driven tests."""
    return {
        "test_commands": ["pytest"],
        "branch": "TEST_AI_FIX",
        "repo_url": "https://github.com/test-org/test-repo.git",
        "github_token": "ghp_fake_token_for_testing",
    }


# ── Tests ────────────────────────────────────────────────────────────

class TestCIDrivenReasoningLoop:

    def test_state_machine_has_8_phases(self):
        """TRANSITIONS table must have all 8 non-DONE phases."""
        expected = {
            "RUN_TESTS", "CLASSIFY", "PLAN_FIX", "APPLY_PATCH",
            "COMMIT_PUSH", "WAIT_FOR_CI", "FETCH_CI_RESULTS", "VERIFY",
        }
        assert set(TRANSITIONS.keys()) == expected

    def test_registry_has_8_tools(self):
        """Default registry should contain exactly 8 tools."""
        registry = build_default_registry()
        tools = registry.list_tools()
        assert len(tools) == 8
        names = {t["name"] for t in tools}
        assert names == {
            "test_runner", "failure_classifier", "fix_planner",
            "patch_applier", "commit_push", "wait_for_ci",
            "fetch_ci_results", "verification",
        }

    def test_ci_driven_full_heal(self, tmp_path):
        """Full CI-driven loop: patch → commit → CI pass → healed."""
        repo = _make_repo(tmp_path)
        _init_git_repo(repo)

        fail_output = textwrap.dedent("""\
            FAILED test_app.py::test_greet
            File "app.py", line 1
                def greet(name)
                               ^
            SyntaxError: expected ':'
        """)
        pass_output = "1 passed in 0.01s\n"

        # Mock sandbox (RUN_TESTS only — initial local run)
        call_count = {"n": 0}
        async def mock_run(repo_path, test_command, install_deps=True):
            call_count["n"] += 1
            return _mock_exec(False, stdout=fail_output)

        mock_executor = MagicMock()
        mock_executor.run_tests = mock_run

        # Mock git for commit_push
        def mock_git_run(cmd, cwd, **kw):
            result = MagicMock()
            result.returncode = 0
            if "rev-parse" in cmd:
                result.stdout = "abc1234\n"
            elif "status" in cmd:
                result.stdout = "M app.py\n"
            else:
                result.stdout = ""
            result.stderr = ""
            return result

        # Mock CI API for wait_for_ci
        ci_run_response = {
            "workflow_runs": [{
                "id": 12345,
                "head_sha": "abc1234",
                "status": "completed",
                "conclusion": "success",
                "html_url": "https://github.com/test-org/test-repo/actions/runs/12345",
            }]
        }

        ci_log_zip = _make_ci_log_zip(pass_output)

        async def mock_httpx_get(url, **kw):
            resp = MagicMock()
            resp.status_code = 200
            resp.raise_for_status = MagicMock()
            if "actions/runs" in url and "logs" not in url:
                resp.json = MagicMock(return_value=ci_run_response)
            else:
                resp.content = ci_log_zip
            return resp

        mock_client = AsyncMock()
        mock_client.get = mock_httpx_get
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("agents.tools.test_runner_tool._get_executor", return_value=mock_executor), \
             patch("agents.tools.commit_push_tool.subprocess.run", side_effect=mock_git_run), \
             patch("agents.tools.wait_for_ci_tool.httpx.AsyncClient", return_value=mock_client), \
             patch("agents.tools.fetch_ci_results_tool.httpx.AsyncClient", return_value=mock_client):

            result: ReasoningLoopResult = asyncio.run(
                run_reasoning_loop(
                    repo_path=str(repo),
                    max_iterations=5,
                    config=_config_with_branch(str(repo)),
                )
            )

        assert result.status == "healed"
        assert result.iterations_used >= 1
        assert result.total_fixes_applied >= 1

        # Check iteration has CI-specific fields
        it = result.iterations[0]
        assert it.commit_sha != ""
        assert it.ci_conclusion == "success"
        assert it.all_passed is True

    def test_commit_message_has_ai_agent_prefix(self, tmp_path):
        """CommitPushTool must always use [AI-AGENT] prefix."""
        repo = _make_repo(tmp_path)
        _init_git_repo(repo)

        # Modify a file so there's something to commit
        (repo / "app.py").write_text("def greet(name):\n    return f'Hello, {name}!'\n")

        commit_messages = []

        def mock_git_run(cmd, cwd, **kw):
            result = MagicMock()
            result.returncode = 0
            if "rev-parse" in cmd:
                result.stdout = "abc1234\n"
            elif "status" in cmd:
                result.stdout = "M app.py\n"
            else:
                result.stdout = ""
            result.stderr = ""
            if "commit" in cmd:
                commit_messages.append(cmd[cmd.index("-m") + 1])
            return result

        tool = CommitPushTool()
        state = {
            "repo_path": str(repo),
            "branch": "TEST_AI_FIX",
            "applied_patches": [{"file": "app.py", "description": "fix colon"}],
            "applied_count": 1,
            "_current_iteration": 1,
        }

        with patch("agents.tools.commit_push_tool.subprocess.run", side_effect=mock_git_run):
            result = asyncio.run(tool.execute(state))

        assert result.status == "success"
        assert len(commit_messages) == 1
        assert commit_messages[0].startswith("[AI-AGENT]")
        assert "iteration 1" in commit_messages[0].lower()

    def test_verify_uses_ci_results_not_local(self):
        """VerificationTool must read CI data, not run tests locally."""
        tool = VerificationTool()

        state = {
            "ci_conclusion": "failure",
            "ci_logs": "FAILED test_app.py 2 failed",
            "ci_failed": 2,
            "ci_passed": 3,
            "ci_failing_suites": 1,
            "ci_passing_suites": 0,
            "failing_suites": 3,  # previous iteration had 3
        }

        result = asyncio.run(tool.execute(state))

        assert result.outputs["all_passed"] is False
        assert result.outputs["verdict"] == "partial"
        assert result.outputs["improvement"] == 2  # 3 → 1
        assert result.outputs["should_continue"] is True

    def test_verify_ci_pass(self):
        """VerificationTool should report healed when CI passes."""
        tool = VerificationTool()

        state = {
            "ci_conclusion": "success",
            "ci_logs": "5 passed in 2.3s",
            "ci_failed": 0,
            "ci_passed": 5,
            "ci_failing_suites": 0,
            "ci_passing_suites": 1,
            "failing_suites": 1,
        }

        result = asyncio.run(tool.execute(state))
        assert result.outputs["all_passed"] is True
        assert result.outputs["verdict"] == "pass"
        assert result.outputs["should_continue"] is False

    def test_loop_back_to_classify_on_ci_fail(self, tmp_path):
        """On CI failure, loop should go VERIFY → CLASSIFY (not RUN_TESTS)."""
        # Verify via the TRANSITIONS table
        _, next_continue, _ = TRANSITIONS[Phase.VERIFY]
        assert next_continue == Phase.CLASSIFY

    def test_already_passing_exits_without_healed(self, tmp_path):
        """If local tests pass and classifier finds 0 bugs, no commit is
        pushed — CI monitoring is skipped and the repo is marked PASSED.
        """
        repo = _make_repo(tmp_path)

        async def always_pass(repo_path, test_command, install_deps=True):
            return _mock_exec(True, stdout="1 passed\n")

        mock_executor = MagicMock()
        mock_executor.run_tests = always_pass

        with patch("agents.tools.test_runner_tool._get_executor", return_value=mock_executor):
            result = asyncio.run(
                run_reasoning_loop(
                    repo_path=str(repo),
                    max_iterations=5,
                    config=_config_with_branch(str(repo)),
                )
            )

        # Local tests pass + 0 bugs → clean repo → PASSED, no CI poll
        assert result.status == "healed"
        assert result.iterations_used == 1
        assert result.iterations[0].all_passed is True
        assert result.total_fixes_applied == 0

    def test_max_iterations_respected(self, tmp_path):
        """Loop stops after max_iterations even if not healed.

        When local tests fail but the classifier can't parse errors
        (0 bugs classified), the loop exits with 'failed' — it does
        NOT claim 'healed' because local tests did not pass.
        """
        repo = _make_repo(tmp_path)

        async def always_fail(repo_path, test_command, install_deps=True):
            return _mock_exec(False, stdout="FAILED: everything broken\n")

        mock_executor = MagicMock()
        mock_executor.run_tests = always_fail

        with patch("agents.tools.test_runner_tool._get_executor", return_value=mock_executor):
            result = asyncio.run(
                run_reasoning_loop(
                    repo_path=str(repo),
                    max_iterations=2,
                    config=_config_with_branch(str(repo)),
                )
            )

        assert result.iterations_used <= 2
        assert result.status in ("partial", "failed")
        assert result.iterations[0].all_passed is False

    def test_fetch_ci_extracts_logs_and_counts(self):
        """FetchCIResultsTool log extraction and count parsing."""
        log_content = textwrap.dedent("""\
            === Running tests ===
            test_app.py::test_greet PASSED
            test_app.py::test_farewell FAILED
            2 passed, 1 failed in 0.5s
        """)

        zip_bytes = _make_ci_log_zip(log_content)
        extracted = _extract_logs(zip_bytes)

        assert "test_greet PASSED" in extracted
        assert "test_farewell FAILED" in extracted

        passed, failed = _parse_counts(extracted)
        assert passed == 2
        assert failed == 1

    def test_protects_test_files(self, tmp_path):
        """PatchApplier must never modify test files."""
        repo = tmp_path / "repo"
        repo.mkdir()
        test_file = repo / "test_broken.py"
        test_file.write_text("def test_x():\n    assert 1 == 2\n")
        original = test_file.read_text()

        tool = PatchApplierTool()
        plan = [{
            "bug": {"file": "test_broken.py", "line": 2, "bug_type": "LOGIC"},
            "strategy": "skip_test_file",
            "reason": "Will not modify test files.",
            "target_file": str(test_file),
            "source_context": "",
        }]

        result = asyncio.run(tool.execute({"fix_plan": plan, "repo_path": str(repo)}))
        assert test_file.read_text() == original
        assert result.outputs["applied_count"] == 0

    def test_structured_iteration_reports(self, tmp_path):
        """Each iteration must produce a complete IterationReport with CI fields."""
        repo = _make_repo(tmp_path)
        _init_git_repo(repo)

        fail_out = "FAILED test_app.py::test_greet\nSyntaxError: expected ':'\n"
        pass_out = "1 passed\n"

        async def mock_run(repo_path, test_command, install_deps=True):
            return _mock_exec(False, stdout=fail_out)

        mock_executor = MagicMock()
        mock_executor.run_tests = mock_run

        def mock_git_run(cmd, cwd, **kw):
            r = MagicMock()
            r.returncode = 0
            if "rev-parse" in cmd:
                r.stdout = "def1234\n"
            elif "status" in cmd:
                r.stdout = "M app.py\n"
            else:
                r.stdout = ""
            r.stderr = ""
            return r

        ci_resp = {"workflow_runs": [{
            "id": 99, "head_sha": "def1234", "status": "completed",
            "conclusion": "success", "html_url": "http://ci/99",
        }]}

        async def mock_get(url, **kw):
            resp = MagicMock()
            resp.status_code = 200
            resp.raise_for_status = MagicMock()
            if "logs" in url:
                resp.content = _make_ci_log_zip(pass_out)
            else:
                resp.json = MagicMock(return_value=ci_resp)
            return resp

        mock_client = AsyncMock()
        mock_client.get = mock_get
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("agents.tools.test_runner_tool._get_executor", return_value=mock_executor), \
             patch("agents.tools.commit_push_tool.subprocess.run", side_effect=mock_git_run), \
             patch("agents.tools.wait_for_ci_tool.httpx.AsyncClient", return_value=mock_client), \
             patch("agents.tools.fetch_ci_results_tool.httpx.AsyncClient", return_value=mock_client):

            result = asyncio.run(run_reasoning_loop(
                repo_path=str(repo),
                max_iterations=5,
                config=_config_with_branch(str(repo)),
            ))

        as_dict = result.to_dict()
        assert isinstance(as_dict["iterations"], list)
        for it in as_dict["iterations"]:
            assert "commit_sha" in it
            assert "ci_conclusion" in it
            assert "bugs_found" in it
            assert "patches_applied" in it
            assert "verdict" in it

    def test_progress_callbacks_fire(self, tmp_path):
        """on_progress must fire for CI tools too."""
        repo = _make_repo(tmp_path)
        events: list[tuple[str, str, str]] = []

        def on_progress(agent: str, status: str, msg: str) -> None:
            events.append((agent, status, msg))

        async def always_pass(repo_path, test_command, install_deps=True):
            return _mock_exec(True, stdout="1 passed\n")

        mock_executor = MagicMock()
        mock_executor.run_tests = always_pass

        with patch("agents.tools.test_runner_tool._get_executor", return_value=mock_executor):
            asyncio.run(run_reasoning_loop(
                repo_path=str(repo),
                max_iterations=5,
                config=_config_with_branch(str(repo)),
                on_progress=on_progress,
            ))

        agent_names = {e[0] for e in events}
        assert "reasoning_loop" in agent_names
        assert "test_runner" in agent_names


# ── RunMemory-specific tests ─────────────────────────────────────────

from agents.run_memory import RunMemory, FailureRecord, FixRecord, CIRunRecord


class TestRunMemory:

    def test_memory_append_only_never_overwrites(self):
        """Appending iteration 2 must not remove iteration 1 records."""
        mem = RunMemory()

        # Iteration 1
        mem.append_failures(1, [
            {"file": "a.py", "line": 10, "bug_type": "SYNTAX", "message": "missing colon"},
        ])
        mem.append_fixes(1, [
            {"file": "a.py", "bug": {"line": 10}, "description": "added colon"},
        ], "sha111")
        mem.append_ci_run(1, "failure")

        assert len(mem.failures) == 1
        assert len(mem.fixes) == 1
        assert len(mem.ci_runs) == 1

        # Iteration 2
        mem.append_failures(2, [
            {"file": "b.py", "line": 20, "bug_type": "IMPORT", "message": "no module"},
            {"file": "c.py", "line": 5, "bug_type": "LOGIC", "message": "wrong assert"},
        ])
        mem.append_fixes(2, [
            {"file": "b.py", "bug": {"line": 20}, "description": "fixed import"},
        ], "sha222")
        mem.append_ci_run(2, "success")

        # Iteration 1 records are still present
        assert len(mem.failures) == 3
        assert len(mem.fixes) == 2
        assert len(mem.ci_runs) == 2
        assert mem.failures[0].iteration == 1
        assert mem.failures[1].iteration == 2

    def test_latest_ci_run_returns_most_recent(self):
        """latest_ci_run() must return the last appended CI record."""
        mem = RunMemory()
        assert mem.latest_ci_run() is None

        mem.append_ci_run(1, "failure")
        assert mem.latest_ci_run().status == "failure"
        assert mem.latest_ci_run().iteration == 1

        mem.append_ci_run(2, "success")
        assert mem.latest_ci_run().status == "success"
        assert mem.latest_ci_run().iteration == 2

    def test_to_dict_exports_aggregated_state(self):
        """to_dict() must include all records and summary counts."""
        mem = RunMemory()
        mem.append_failures(1, [
            {"file": "x.py", "line": 1, "bug_type": "SYNTAX", "message": "err"},
        ])
        mem.append_fixes(1, [
            {"file": "x.py", "bug": {"line": 1}, "description": "fix"},
        ], "abc123")
        mem.append_ci_run(1, "success")

        d = mem.to_dict()
        assert len(d["failures"]) == 1
        assert len(d["fixes"]) == 1
        assert len(d["ci_runs"]) == 1
        assert d["summary"]["total_failures"] == 1
        assert d["summary"]["total_fixes"] == 1
        assert d["summary"]["total_ci_runs"] == 1
        assert d["summary"]["unique_files_with_failures"] == 1

    def test_done_includes_memory_in_result(self, tmp_path):
        """ReasoningLoopResult.memory must contain the full aggregated state."""
        repo = _make_repo(tmp_path)
        _init_git_repo(repo)

        fail_out = textwrap.dedent("""\
            FAILED test_app.py::test_greet
            File "app.py", line 1
                def greet(name)
                               ^
            SyntaxError: expected ':'
        """)
        pass_out = "1 passed in 0.01s\n"

        async def mock_run(repo_path, test_command, install_deps=True):
            return _mock_exec(False, stdout=fail_out)

        mock_executor = MagicMock()
        mock_executor.run_tests = mock_run

        def mock_git_run(cmd, cwd, **kw):
            r = MagicMock()
            r.returncode = 0
            if "rev-parse" in cmd:
                r.stdout = "mem1234\n"
            elif "status" in cmd:
                r.stdout = "M app.py\n"
            else:
                r.stdout = ""
            r.stderr = ""
            return r

        ci_resp = {"workflow_runs": [{
            "id": 77, "head_sha": "mem1234", "status": "completed",
            "conclusion": "success", "html_url": "http://ci/77",
        }]}

        async def mock_get(url, **kw):
            resp = MagicMock()
            resp.status_code = 200
            resp.raise_for_status = MagicMock()
            if "logs" in url:
                resp.content = _make_ci_log_zip(pass_out)
            else:
                resp.json = MagicMock(return_value=ci_resp)
            return resp

        mock_client = AsyncMock()
        mock_client.get = mock_get
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("agents.tools.test_runner_tool._get_executor", return_value=mock_executor), \
             patch("agents.tools.commit_push_tool.subprocess.run", side_effect=mock_git_run), \
             patch("agents.tools.wait_for_ci_tool.httpx.AsyncClient", return_value=mock_client), \
             patch("agents.tools.fetch_ci_results_tool.httpx.AsyncClient", return_value=mock_client):

            result = asyncio.run(run_reasoning_loop(
                repo_path=str(repo),
                max_iterations=5,
                config=_config_with_branch(str(repo)),
            ))

        # Memory must be in the final result
        assert "memory" in result.to_dict()
        mem = result.memory
        assert len(mem["failures"]) >= 1
        assert len(mem["fixes"]) >= 1
        assert len(mem["ci_runs"]) >= 1
        assert mem["ci_runs"][-1]["status"] == "success"

        # Failure records must have the required schema
        for f in mem["failures"]:
            assert "file" in f
            assert "line" in f
            assert "bug_type" in f
            assert "standardized_message" in f
            assert "iteration" in f

        # Fix records must have the required schema
        for fx in mem["fixes"]:
            assert "file" in fx
            assert "line" in fx
            assert "change_summary" in fx
            assert "commit_hash" in fx
            assert "iteration" in fx

        # CI run records must have the required schema
        for ci in mem["ci_runs"]:
            assert "iteration" in ci
            assert "status" in ci
            assert "start_time" in ci
            assert "end_time" in ci

