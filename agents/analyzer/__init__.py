"""Analyzer Agent — Agent 1 in the heal loop.

Discovers test frameworks, runs all suites in the Docker sandbox,
and returns a structured AnalysisResult with raw logs, exit codes,
and pass/fail counts.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from agents.base import AgentResult, BaseAgent
from agents.test_runner.discovery import discover_test_commands, DiscoveryResult

logger = logging.getLogger(__name__)


class AnalyzerAgent(BaseAgent):
    """Runs tests and produces structured failure output for the classifier."""

    name = "analyzer"

    def __init__(self, test_commands: list[str] | None = None):
        """Optionally accept pre-set test commands (e.g. from a previous iteration)."""
        self._fixed_commands = test_commands

    async def run(self, context: dict[str, Any]) -> AgentResult:
        repo_path = context.get("repo_path", ".")

        # ── Discover test commands (once, or reuse from context) ──────
        commands: list[str] = self._fixed_commands or context.get("test_commands", [])
        if not commands:
            discovery: DiscoveryResult = discover_test_commands(repo_path)
            commands = discovery.commands
            context["test_commands"] = commands

        if not commands:
            return AgentResult(
                agent_name=self.name,
                status="skipped",
                summary="No test framework detected.",
                details={"all_passed": True, "test_output": ""},
            )

        # ── Run tests ────────────────────────────────────────────────
        executor = self._get_executor()
        combined_stdout = ""
        combined_stderr = ""
        total_exit_code = 0
        passing = 0
        failing = 0
        test_results: list[dict[str, Any]] = []

        for cmd in commands:
            logger.info("Analyzer running: %s", cmd)
            result = await executor.run_tests(
                repo_path=repo_path,
                test_command=cmd,
                install_deps=context.get("_first_iteration", True),
            )

            combined_stdout += result.stdout + "\n"
            combined_stderr += result.stderr + "\n"
            total_exit_code = max(total_exit_code, result.exit_code)

            entry = {
                "command": cmd,
                "exit_code": result.exit_code,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "timed_out": result.timed_out,
                "duration_s": result.duration_s,
                "success": result.success,
            }
            test_results.append(entry)

            if result.success:
                passing += 1
            else:
                failing += 1

        all_passed = total_exit_code == 0

        # Build combined test output for the classifier
        test_output = combined_stdout
        if combined_stderr.strip():
            test_output += "\n--- STDERR ---\n" + combined_stderr

        status = "success" if all_passed else "failure"
        summary = f"Ran {len(commands)} suite(s): {passing} passed, {failing} failed."

        return AgentResult(
            agent_name=self.name,
            status=status,
            summary=summary,
            details={
                "all_passed": all_passed,
                "test_output": test_output,
                "exit_code": total_exit_code,
                "passing_suites": passing,
                "failing_suites": failing,
                "test_results": test_results,
                "test_commands": commands,
            },
        )

    @staticmethod
    def _get_executor():
        """Lazy import to avoid hard Docker dependency during testing."""
        import sys
        from pathlib import Path as _P

        _root = _P(__file__).resolve().parents[2]
        if str(_root) not in sys.path:
            sys.path.insert(0, str(_root))

        from sandbox.executor import SandboxExecutor
        return SandboxExecutor()
