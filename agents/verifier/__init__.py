"""Verifier Agent — Agent 4 in the heal loop.

Re-runs the exact same test commands the Analyzer used, compares results
against the previous iteration, and decides whether the loop should continue.
"""

from __future__ import annotations

import logging
from typing import Any

from agents.base import AgentResult, BaseAgent

logger = logging.getLogger(__name__)


class VerifierAgent(BaseAgent):
    """Re-runs tests after fixes and compares to previous results."""

    name = "verifier"

    async def run(self, context: dict[str, Any]) -> AgentResult:
        repo_path = context.get("repo_path", ".")
        commands: list[str] = context.get("test_commands", [])

        if not commands:
            return AgentResult(
                agent_name=self.name,
                status="skipped",
                summary="No test commands to verify.",
                details={
                    "all_passed": True,
                    "should_continue": False,
                    "verification_output": "",
                },
            )

        # ── Run the same tests again ─────────────────────────────────
        executor = self._get_executor()
        combined_stdout = ""
        combined_stderr = ""
        total_exit_code = 0
        passing = 0
        failing = 0
        test_results: list[dict[str, Any]] = []

        for cmd in commands:
            logger.info("Verifier re-running: %s", cmd)
            result = await executor.run_tests(
                repo_path=repo_path,
                test_command=cmd,
                install_deps=False,  # deps already installed by analyzer
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

        local_all_passed = total_exit_code == 0

        # Local tests are informational — final pass/fail comes from CI.
        # We still report improvement to guide the fixer, but never
        # declare healing complete based on local results alone.

        # ── Compare to previous iteration ────────────────────────────
        prev_failing = context.get("failing_suites", 0)
        improvement = prev_failing - failing

        if local_all_passed:
            verdict = "local_pass"
            should_continue = True   # must continue to CI for confirmation
            summary = (
                f"All {len(commands)} test suite(s) pass locally. "
                f"Awaiting CI confirmation."
            )
        elif improvement > 0:
            verdict = "partial"
            should_continue = True
            summary = (
                f"Partial improvement: {failing} suite(s) still failing "
                f"(was {prev_failing}, fixed {improvement})."
            )
        else:
            verdict = "fail"
            should_continue = failing > 0  # continue trying if still failing
            summary = (
                f"No improvement: {failing} suite(s) still failing "
                f"(was {prev_failing})."
            )

        # Update context for next iteration's test output
        verification_output = combined_stdout
        if combined_stderr.strip():
            verification_output += "\n--- STDERR ---\n" + combined_stderr

        return AgentResult(
            agent_name=self.name,
            status="success" if local_all_passed else "failure",
            summary=summary,
            details={
                "local_all_passed": local_all_passed,
                "all_passed": False,  # only CI can set this to True
                "should_continue": should_continue,
                "verdict": verdict,
                "passing_suites": passing,
                "failing_suites": failing,
                "improvement": improvement,
                "test_results": test_results,
                "verification_output": verification_output,
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
