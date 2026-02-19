"""FetchCIResultsTool — downloads CI logs and extracts test output.

Tool 8 in the CI-driven reasoning loop. After the CI workflow has
completed, this tool downloads the logs archive, extracts test output,
and parses pass/fail counts so the Classifier can re-consume them.
"""

from __future__ import annotations

import io
import logging
import os
import re

import httpx
import zipfile
from typing import Any

from agents.tools.registry import AgentTool, ToolResult

logger = logging.getLogger(__name__)


def _parse_owner_repo(repo_url: str) -> tuple[str, str]:
    """Extract owner/repo from a GitHub URL."""
    m = re.search(r"github\.com[/:]([^/]+)/([^/.]+?)(?:\.git)?$", repo_url)
    if m:
        return m.group(1), m.group(2)
    raise ValueError(f"Cannot parse owner/repo from: {repo_url}")


# Patterns to detect test result lines in CI logs
_PASS_PATTERNS = [
    re.compile(r"(\d+)\s+passed"),                    # pytest
    re.compile(r"Tests:\s+(\d+)\s+passed"),            # jest
    re.compile(r"(\d+)\s+test[s]?\s+passed"),          # generic
    re.compile(r"OK\s*\((\d+)\s+test"),                # unittest
]

_FAIL_PATTERNS = [
    re.compile(r"(\d+)\s+failed"),                     # pytest
    re.compile(r"Tests:\s+.*?(\d+)\s+failed"),         # jest
    re.compile(r"FAILED\s*\(.*?failures=(\d+)"),       # unittest
    re.compile(r"(\d+)\s+test[s]?\s+failed"),          # generic
]


class FetchCIResultsTool(AgentTool):
    """Downloads CI logs and extracts structured test results."""

    name = "fetch_ci_results"
    description = (
        "Downloads the log archive for a completed GitHub Actions run, "
        "extracts and parses test output to produce structured pass/fail "
        "counts and raw logs for the classifier."
    )
    input_keys = ["repo_url", "ci_run_id"]
    output_keys = [
        "ci_logs",
        "test_output",
        "ci_passed",
        "ci_failed",
        "ci_passing_suites",
        "ci_failing_suites",
    ]

    async def execute(self, state: dict[str, Any]) -> ToolResult:
        repo_url = state.get("repo_url", "")
        run_id = state.get("ci_run_id", 0)
        conclusion = state.get("ci_conclusion", "")
        token = state.get("github_token", "") or os.environ.get("GITHUB_TOKEN", "")

        logger.debug(
            "[FetchCI] === FETCH START ==="
            " | repo_url=%s | run_id=%s | conclusion=%s",
            repo_url, run_id, conclusion,
        )

        if not repo_url or not run_id:
            logger.warning(
                "[FetchCI] Missing inputs — repo_url=%r, run_id=%r",
                repo_url, run_id,
            )
            return ToolResult(
                tool_name=self.name,
                status="failure",
                summary="Missing repo_url or ci_run_id.",
                outputs=_empty_outputs(),
                errors=["repo_url and ci_run_id are required."],
            )

        if not token:
            return ToolResult(
                tool_name=self.name,
                status="failure",
                summary="No GITHUB_TOKEN — cannot fetch CI logs.",
                outputs=_empty_outputs(),
                errors=["GITHUB_TOKEN required."],
            )

        try:
            owner, repo = _parse_owner_repo(repo_url)
        except ValueError as exc:
            return ToolResult(
                tool_name=self.name,
                status="failure",
                summary=str(exc),
                outputs=_empty_outputs(),
                errors=[str(exc)],
            )

        # ── Download logs archive ────────────────────────────────────
        api_url = f"https://api.github.com/repos/{owner}/{repo}/actions/runs/{run_id}/logs"
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        logger.debug(
            "[FetchCI] Downloading logs | url=%s | run_id=%s",
            api_url, run_id,
        )

        try:
            async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
                resp = await client.get(api_url, headers=headers)
                resp.raise_for_status()
                log_bytes = resp.content
                logger.debug(
                    "[FetchCI] Log archive downloaded | size=%d bytes | run_id=%s",
                    len(log_bytes), run_id,
                )
        except Exception as exc:
            logger.warning(
                "[FetchCI] FALLBACK — log download failed: %s"
                " | run_id=%s | conclusion=%s"
                " — returning synthetic output from conclusion only.",
                exc, run_id, conclusion,
            )
            # Fallback: produce minimal output from conclusion alone
            return ToolResult(
                tool_name=self.name,
                status="success",
                summary=f"Could not download logs; using CI conclusion: {conclusion}.",
                outputs={
                    "ci_logs": f"[CI conclusion: {conclusion}]",
                    "test_output": f"CI workflow concluded: {conclusion}",
                    "ci_passed": 0,
                    "ci_failed": 1 if conclusion != "success" else 0,
                    "ci_passing_suites": 1 if conclusion == "success" else 0,
                    "ci_failing_suites": 0 if conclusion == "success" else 1,
                },
            )

        # ── Extract logs from zip ────────────────────────────────────
        combined_logs = _extract_logs(log_bytes)

        # ── Parse pass/fail ──────────────────────────────────────────
        passed, failed = _parse_counts(combined_logs)
        passing_suites = 1 if conclusion == "success" else 0
        failing_suites = 0 if conclusion == "success" else 1

        logger.info(
            "[FetchCI] === FETCH DONE ==="
            " | run_id=%s | conclusion=%s"
            " | log_bytes=%d | parsed_passed=%d | parsed_failed=%d"
            " | passing_suites=%d | failing_suites=%d",
            run_id, conclusion,
            len(combined_logs), passed, failed,
            passing_suites, failing_suites,
        )

        return ToolResult(
            tool_name=self.name,
            status="success",
            summary=f"Fetched CI logs ({len(combined_logs)} bytes): {passed} passed, {failed} failed.",
            outputs={
                "ci_logs": combined_logs,
                "test_output": combined_logs,  # Feed to classifier
                "ci_passed": passed,
                "ci_failed": failed,
                "ci_passing_suites": passing_suites,
                "ci_failing_suites": failing_suites,
            },
        )


def _extract_logs(zip_bytes: bytes) -> str:
    """Extract all log files from a zip archive and concatenate."""
    combined: list[str] = []
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            for name in sorted(zf.namelist()):
                if name.endswith("/"):
                    continue  # skip directories
                try:
                    content = zf.read(name).decode("utf-8", errors="replace")
                    # Strip ANSI escape codes
                    content = re.sub(r"\x1b\[[0-9;]*m", "", content)
                    combined.append(f"=== {name} ===\n{content}\n")
                except Exception:
                    pass
    except zipfile.BadZipFile:
        # Not a zip — treat raw bytes as plain text
        combined.append(zip_bytes.decode("utf-8", errors="replace"))
    return "\n".join(combined)


def _parse_counts(logs: str) -> tuple[int, int]:
    """Extract pass/fail counts from log text."""
    passed = 0
    failed = 0

    for pattern in _PASS_PATTERNS:
        for m in pattern.finditer(logs):
            passed += int(m.group(1))

    for pattern in _FAIL_PATTERNS:
        for m in pattern.finditer(logs):
            failed += int(m.group(1))

    return passed, failed


def _empty_outputs() -> dict[str, Any]:
    return {
        "ci_logs": "",
        "test_output": "",
        "ci_passed": 0,
        "ci_failed": 0,
        "ci_passing_suites": 0,
        "ci_failing_suites": 0,
    }
