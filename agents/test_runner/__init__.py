"""Test Runner Agent – discovers and executes tests inside the Docker sandbox."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

from agents.base import AgentResult, BaseAgent
from agents.test_runner.discovery import discover_test_commands, DiscoveryResult

# Allow importing the sandbox package from the monorepo root
_MONOREPO_ROOT = Path(__file__).resolve().parents[2]
if str(_MONOREPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_MONOREPO_ROOT))

from sandbox.executor import SandboxExecutor, ExecutionResult

logger = logging.getLogger(__name__)


class TestRunnerAgent(BaseAgent):
    """Discovers test frameworks, runs them inside Docker, and returns results."""

    name = "test_runner"

    async def run(self, context: dict[str, Any]) -> AgentResult:
        repo_path = context.get("repo_path", ".")

        # ── Discover frameworks ──────────────────────────────────
        discovery: DiscoveryResult = discover_test_commands(repo_path)

        if not discovery.commands:
            return AgentResult(
                agent_name=self.name,
                status="skipped",
                summary="No test framework detected.",
                details={"discovery": discovery.to_dict()},
            )

        # ── Execute each discovered command in sandbox ───────────
        executor = SandboxExecutor()
        test_results: list[dict[str, Any]] = []
        failures: list[str] = []

        for cmd in discovery.commands:
            logger.info("Running: %s", cmd)
            result: ExecutionResult = await executor.run_tests(
                repo_path=repo_path,
                test_command=cmd,
                install_deps=True,
            )

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

            if not result.success:
                failures.append(cmd)

        total = len(discovery.commands)
        failed = len(failures)
        status = "success" if failed == 0 else "failure"
        summary = f"Ran {total} test suite(s): {total - failed} passed, {failed} failed."

        return AgentResult(
            agent_name=self.name,
            status=status,
            summary=summary,
            details={
                "repo_path": str(repo_path),
                "discovery": discovery.to_dict(),
                "test_commands": discovery.commands,
                "test_results": test_results,
            },
            errors=failures,
        )
