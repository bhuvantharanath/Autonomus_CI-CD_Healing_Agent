"""Integration test for the heal loop.

Uses mock executors (no Docker required) with a fake repo that has
known bugs.  Verifies the loop converges within the iteration limit.

Run:
    python -m pytest agents/test_heal_loop.py -v
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
import textwrap
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure project root is on path
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from agents.heal_loop import run_heal_loop, HealLoopResult
from agents.analyzer import AnalyzerAgent
from agents.classifier import ClassifierAgent
from agents.fixer import CodeFixerAgent
from agents.verifier import VerifierAgent
from agents.base import AgentResult
from sandbox.executor import ExecutionResult


# ── Helpers ──────────────────────────────────────────────────────────

def _make_repo(tmp: Path) -> Path:
    """Create a minimal Python repo with a known bug (missing colon)."""
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


def _mock_exec_result(success: bool, stdout: str = "", stderr: str = "") -> ExecutionResult:
    return ExecutionResult(
        exit_code=0 if success else 1,
        stdout=stdout,
        stderr=stderr,
        logs=stdout + stderr,
        timed_out=False,
        duration_s=0.5,
    )


# ── Test: heal loop with deterministic fix ───────────────────────────

class TestHealLoop:

    def test_heal_loop_fixes_syntax_error(self, tmp_path):
        """The loop should fix a missing-colon syntax error and converge."""
        repo = _make_repo(tmp_path)

        # Iteration 1: tests fail with SyntaxError
        fail_output = textwrap.dedent(f"""\
            FAILED test_app.py::test_greet
            File "{repo / 'app.py'}", line 1
                def greet(name)
                               ^
            SyntaxError: expected ':'
        """)

        # Iteration 2 (after fix): tests pass
        pass_output = "1 passed in 0.01s\n"

        call_count = {"n": 0}

        async def mock_run_tests(repo_path, test_command, install_deps=True):
            call_count["n"] += 1
            if call_count["n"] <= 1:
                return _mock_exec_result(False, stdout=fail_output)
            return _mock_exec_result(True, stdout=pass_output)

        mock_executor = MagicMock()
        mock_executor.run_tests = mock_run_tests

        # Patch all agent executors to use the mock
        with patch.object(AnalyzerAgent, '_get_executor', return_value=mock_executor), \
             patch.object(VerifierAgent, '_get_executor', return_value=mock_executor):

            result: HealLoopResult = asyncio.run(
                run_heal_loop(
                    repo_path=str(repo),
                    max_iterations=5,
                    config={"test_commands": ["pytest"]},
                )
            )

        assert result.status == "healed"
        assert result.iterations_used <= 3
        assert result.total_fixes_applied >= 1

    def test_heal_loop_protects_test_files(self, tmp_path):
        """The fixer must never modify test files."""
        repo = tmp_path / "repo"
        repo.mkdir()

        # Bug is IN the test file itself
        test_file = repo / "test_broken.py"
        test_file.write_text("def test_x():\n    assert 1 == 2\n")

        original_content = test_file.read_text()

        # Run the fixer directly on a bug pointing at the test file
        from agents.fixer import CodeFixerAgent

        agent = CodeFixerAgent()
        bug = {
            "file": "test_broken.py",
            "line": 2,
            "bug_type": "LOGIC",
            "message": "AssertionError: assert 1 == 2",
            "severity": "medium",
            "fix_hint": "Fix assertion.",
        }

        result: AgentResult = asyncio.run(
            agent.run({"classified_bugs": [bug], "repo_path": str(repo)})
        )

        # Test file must NOT be modified
        assert test_file.read_text() == original_content

        # Fix should be skipped
        fixes = result.details.get("fixes", [])
        assert len(fixes) == 1
        assert fixes[0]["status"] == "skipped_test_file"

    def test_heal_loop_max_iterations(self, tmp_path):
        """Loop should stop after max_iterations even if not healed."""
        repo = _make_repo(tmp_path)

        fail_output = "FAILED: everything is broken\n"

        async def always_fail(repo_path, test_command, install_deps=True):
            return _mock_exec_result(False, stdout=fail_output)

        mock_executor = MagicMock()
        mock_executor.run_tests = always_fail

        with patch.object(AnalyzerAgent, '_get_executor', return_value=mock_executor), \
             patch.object(VerifierAgent, '_get_executor', return_value=mock_executor):

            result: HealLoopResult = asyncio.run(
                run_heal_loop(
                    repo_path=str(repo),
                    max_iterations=3,
                    config={"test_commands": ["pytest"]},
                )
            )

        assert result.iterations_used <= 3
        assert result.status in ("partial", "failed")

    def test_heal_loop_already_passing(self, tmp_path):
        """If tests already pass, the loop should exit immediately."""
        repo = _make_repo(tmp_path)

        async def always_pass(repo_path, test_command, install_deps=True):
            return _mock_exec_result(True, stdout="1 passed\n")

        mock_executor = MagicMock()
        mock_executor.run_tests = always_pass

        with patch.object(AnalyzerAgent, '_get_executor', return_value=mock_executor):

            result: HealLoopResult = asyncio.run(
                run_heal_loop(
                    repo_path=str(repo),
                    max_iterations=5,
                    config={"test_commands": ["pytest"]},
                )
            )

        assert result.status == "healed"
        assert result.iterations_used == 1
        assert result.total_fixes_applied == 0
