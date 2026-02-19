"""VerificationAgent tool — uses CI results to determine if healing succeeded.

Tool 5 in the CI-driven reasoning loop. Reads CI conclusion and CI
logs (written by FetchCIResultsTool) instead of running tests locally.
Compares against previous iteration and outputs a verdict + should_continue.
"""

from __future__ import annotations

import logging
from typing import Any

from agents.tools.registry import AgentTool, ToolResult

logger = logging.getLogger(__name__)


class VerificationTool(AgentTool):
    """Verifies healing success using CI results (not local execution)."""

    name = "verification"
    description = (
        "Reads CI conclusion and parsed CI logs to determine whether "
        "the fixes resolved the failing tests. Compares pass/fail "
        "counts to the previous iteration and produces a verdict "
        "(pass / partial / fail) plus a should_continue flag."
    )
    input_keys = ["ci_conclusion"]
    output_keys = [
        "all_passed",
        "should_continue",
        "verdict",
        "improvement",
        "verification_output",
        "passing_suites",
        "failing_suites",
    ]

    async def execute(self, state: dict[str, Any]) -> ToolResult:
        ci_conclusion = state.get("ci_conclusion", "")
        ci_logs = state.get("ci_logs", "")
        ci_failed = state.get("ci_failed", 0)
        ci_passed = state.get("ci_passed", 0)
        ci_failing_suites = state.get("ci_failing_suites", 0)
        ci_passing_suites = state.get("ci_passing_suites", 0)
        ci_unavailable = state.get("_ci_unavailable", False)

        # ── Read from RunMemory if available ──────────────────────────
        memory = state.get("_run_memory")
        if memory is not None:
            latest = memory.latest_ci_run()
            if latest is not None:
                ci_conclusion = ci_conclusion or latest.status

        # ── CI unavailable — run local tests as fallback verification ─
        if ci_unavailable or not ci_conclusion:
            repo_path = state.get("repo_path", ".")
            logger.info(
                "[Verification] CI unavailable — running local test "
                "verification on %s", repo_path,
            )
            local_passed, local_output = await self._run_local_verification(repo_path, state)
            all_passed = local_passed

            if all_passed:
                if memory:
                    memory.append_ci_run(
                        state.get("_current_iteration", 1), "success"
                    )
                return ToolResult(
                    tool_name=self.name,
                    status="success",
                    summary="CI unavailable but local tests PASS — healing verified locally.",
                    outputs={
                        "all_passed": True,
                        "should_continue": False,
                        "verdict": "pass",
                        "improvement": 0,
                        "verification_output": local_output,
                        "local_all_passed": True,
                        "passing_suites": 1,
                        "failing_suites": 0,
                    },
                )
            else:
                return ToolResult(
                    tool_name=self.name,
                    status="failure",
                    summary=f"CI unavailable. Local tests still FAIL — continue iterating.",
                    outputs={
                        "all_passed": False,
                        "should_continue": True,
                        "verdict": "fail",
                        "improvement": 0,
                        "verification_output": local_output,
                        "local_all_passed": False,
                        "passing_suites": 0,
                        "failing_suites": 1,
                    },
                )

        all_passed = ci_conclusion == "success"

        # ── Compare to previous iteration ────────────────────────────
        prev_failing = state.get("failing_suites", 0)

        # Use memory to find previous CI run's failure count if available
        if memory is not None and len(memory.ci_runs) >= 2:
            prev_ci = memory.ci_runs[-2]
            # If previous CI was also a failure, keep iterating
            if prev_ci.status != "success" and prev_failing == 0:
                prev_failing = 1  # at least 1 was failing before

        failing = ci_failing_suites
        passing = ci_passing_suites
        improvement = prev_failing - failing

        if all_passed:
            verdict = "pass"
            should_continue = False
            summary = f"CI passed (conclusion: success). Healing complete."
        elif improvement > 0:
            verdict = "partial"
            should_continue = True
            summary = (
                f"CI failed (conclusion: {ci_conclusion}), "
                f"but improved: {failing} failing (was {prev_failing}, fixed {improvement})."
            )
        else:
            verdict = "fail"
            should_continue = failing > 0
            summary = (
                f"CI failed (conclusion: {ci_conclusion}). "
                f"No improvement: {failing} failing (was {prev_failing})."
            )

        verification_output = ci_logs or f"CI conclusion: {ci_conclusion}"

        logger.info(
            "[Verification] CI %s — verdict=%s, improvement=%d, should_continue=%s",
            ci_conclusion, verdict, improvement, should_continue,
        )

        return ToolResult(
            tool_name=self.name,
            status="success" if all_passed else "failure",
            summary=summary,
            outputs={
                "all_passed": all_passed,
                "should_continue": should_continue,
                "verdict": verdict,
                "improvement": improvement,
                "verification_output": verification_output,
                "passing_suites": passing,
                "failing_suites": failing,
            },
        )

    # ── Local verification fallback ──────────────────────────────────

    @staticmethod
    async def _run_local_verification(
        repo_path: str, state: dict
    ) -> tuple[bool, str]:
        """Re-run tests locally when CI is unavailable.

        Returns (all_passed, test_output).
        """
        import subprocess
        import sys
        from pathlib import Path

        root = Path(repo_path).resolve()
        commands = state.get("test_commands", [])

        if not commands:
            # Fall back to static analysis
            try:
                from agents.tools.test_runner_tool import (
                    _run_native_static_analysis,
                )
                result = _run_native_static_analysis(repo_path)
                if result is not None:
                    return result.outputs.get("local_all_passed", False), result.outputs.get("test_output", "")
            except Exception as exc:
                logger.warning("[Verification] Static analysis fallback failed: %s", exc)
            return False, ""

        combined = ""
        max_exit = 0
        for cmd in commands:
            try:
                if cmd.strip() == "pytest":
                    run_cmd = [sys.executable, "-m", "pytest", "--tb=short", "-v"]
                elif cmd.strip().startswith("python -m unittest"):
                    parts = cmd.strip().split()
                    run_cmd = [sys.executable] + parts[1:]
                else:
                    run_cmd = ["sh", "-c", cmd]

                result = subprocess.run(
                    run_cmd, cwd=str(root),
                    capture_output=True, text=True, timeout=120,
                )
                combined += result.stdout + "\n" + result.stderr + "\n"
                max_exit = max(max_exit, result.returncode)
            except Exception as exc:
                combined += f"Error running {cmd}: {exc}\n"
                max_exit = 1

        return max_exit == 0, combined
