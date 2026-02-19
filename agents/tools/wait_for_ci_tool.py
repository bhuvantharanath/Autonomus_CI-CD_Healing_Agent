"""WaitForCITool — polls GitHub Actions until the workflow completes.

Tool 7 in the CI-driven reasoning loop. After a push, polls the
GitHub Actions API every 15 seconds (up to 10 minutes) waiting for
the workflow run triggered by our commit to reach 'completed' status.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any

import httpx

from agents.tools.registry import AgentTool, ToolResult

logger = logging.getLogger(__name__)

POLL_INTERVAL_S = 15
MAX_WAIT_S = 600  # 10 minutes
NO_WORKFLOW_BAIL_COUNT = 4  # bail after 4 consecutive "no workflow" polls (~60s)


def _parse_owner_repo(repo_url: str) -> tuple[str, str]:
    """Extract owner/repo from a GitHub URL.

    Handles:
      https://github.com/owner/repo.git
      https://github.com/owner/repo
      git@github.com:owner/repo.git
    """
    # HTTPS format
    m = re.search(r"github\.com[/:]([^/]+)/([^/.]+?)(?:\.git)?$", repo_url)
    if m:
        return m.group(1), m.group(2)
    raise ValueError(f"Cannot parse owner/repo from: {repo_url}")


class WaitForCITool(AgentTool):
    """Polls GitHub Actions until the CI workflow completes."""

    name = "wait_for_ci"
    description = (
        "Polls the GitHub Actions API every 15 seconds (max 10 minutes) "
        "waiting for the workflow run triggered by our commit to complete. "
        "Outputs the CI run ID, conclusion, and URL."
    )
    input_keys = ["repo_url", "branch", "commit_sha"]
    output_keys = ["ci_run_id", "ci_conclusion", "ci_run_url", "ci_status"]

    async def execute(self, state: dict[str, Any]) -> ToolResult:
        repo_url = state.get("repo_url", "")
        branch = state.get("branch", "")
        commit_sha = state.get("commit_sha", "")
        token = state.get("github_token", "") or os.environ.get("GITHUB_TOKEN", "")

        if not repo_url or not branch:
            return ToolResult(
                tool_name=self.name,
                status="failure",
                summary="Missing repo_url or branch.",
                outputs=_empty_outputs(),
                errors=["repo_url and branch are required."],
            )

        if not token:
            return ToolResult(
                tool_name=self.name,
                status="failure",
                summary="No GITHUB_TOKEN — cannot poll CI.",
                outputs=_empty_outputs(),
                errors=["GITHUB_TOKEN is required to poll GitHub Actions."],
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

        api_base = f"https://api.github.com/repos/{owner}/{repo}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        elapsed = 0
        run_data: dict[str, Any] | None = None
        consecutive_no_workflow = 0  # track consecutive empty polls

        logger.debug(
            "[WaitForCI] === CI POLL START ==="
            " | repo=%s/%s | branch=%s | commit_sha=%s"
            " | poll_interval=%ds | max_wait=%ds",
            owner, repo, branch, commit_sha or "<any>",
            POLL_INTERVAL_S, MAX_WAIT_S,
        )

        async with httpx.AsyncClient(timeout=30) as client:
            while elapsed < MAX_WAIT_S:
                logger.info(
                    "[WaitForCI] Polling %s branch=%s (elapsed=%ds/%ds)…",
                    f"{owner}/{repo}", branch, elapsed, MAX_WAIT_S,
                )

                try:
                    resp = await client.get(
                        f"{api_base}/actions/runs",
                        headers=headers,
                        params={
                            "branch": branch,
                            "per_page": 5,
                            "event": "push",
                        },
                    )
                    resp.raise_for_status()
                    runs = resp.json().get("workflow_runs", [])

                    # ── Log every workflow run returned by the API ────
                    if not runs:
                        consecutive_no_workflow += 1
                        logger.warning(
                            "[WaitForCI] NO_WORKFLOW_FOUND"
                            " | branch=%s | elapsed=%ds"
                            " — no workflow runs returned by GitHub API."
                            " (consecutive=%d/%d)",
                            branch, elapsed,
                            consecutive_no_workflow, NO_WORKFLOW_BAIL_COUNT,
                        )
                        # Early bail: if we've seen no workflow N times in
                        # a row, the repo probably has no Actions YAML at
                        # all — stop wasting time.
                        if consecutive_no_workflow >= NO_WORKFLOW_BAIL_COUNT:
                            logger.warning(
                                "[WaitForCI] Bailing early — %d consecutive "
                                "polls with no workflow. Repo likely has no "
                                "GitHub Actions configuration.",
                                consecutive_no_workflow,
                            )
                            break
                    else:
                        consecutive_no_workflow = 0  # reset on any result
                        for idx, r in enumerate(runs):
                            logger.debug(
                                "[WaitForCI] Workflow run [%d/%d]"
                                " | id=%s | status=%s | conclusion=%s"
                                " | head_sha=%s | url=%s",
                                idx + 1, len(runs),
                                r.get("id"),
                                r.get("status"),
                                r.get("conclusion", "—"),
                                r.get("head_sha", "")[:8],
                                r.get("html_url", ""),
                            )

                    # Find the run matching our commit
                    for run in runs:
                        if commit_sha and run.get("head_sha", "").startswith(commit_sha):
                            run_data = run
                            logger.debug(
                                "[WaitForCI] SHA match found"
                                " | run_id=%s | sha=%s",
                                run.get("id"), commit_sha[:8],
                            )
                            break
                    else:
                        # If no SHA match, take the latest run on the branch
                        if runs:
                            run_data = runs[0]
                            logger.debug(
                                "[WaitForCI] No SHA match — falling back"
                                " to latest run on branch"
                                " | run_id=%s | head_sha=%s",
                                runs[0].get("id"),
                                runs[0].get("head_sha", "")[:8],
                            )

                    if run_data and run_data.get("status") == "completed":
                        logger.debug(
                            "[WaitForCI] Run completed"
                            " | run_id=%s | conclusion=%s"
                            " | waited=%ds",
                            run_data.get("id"),
                            run_data.get("conclusion"),
                            elapsed,
                        )
                        break

                except httpx.HTTPError as exc:
                    logger.warning("[WaitForCI] API error: %s", exc)

                await asyncio.sleep(POLL_INTERVAL_S)
                elapsed += POLL_INTERVAL_S

        # ── No workflow found at all ─────────────────────────────────
        if run_data is None:
            logger.error(
                "[WaitForCI] NO_WORKFLOW_FOUND (final)"
                " | branch=%s | commit_sha=%s | elapsed=%ds/%ds"
                " — giving up.",
                branch, commit_sha or "<any>", elapsed, MAX_WAIT_S,
            )
            return ToolResult(
                tool_name=self.name,
                status="failure",
                summary=f"No CI run found for {branch} after {elapsed}s.",
                outputs=_empty_outputs(),
                errors=["No matching workflow run found."],
            )

        # ── Timeout: workflow exists but didn't complete ─────────────
        if run_data.get("status") != "completed":
            logger.error(
                "[WaitForCI] TIMEOUT"
                " | run_id=%s | status=%s | conclusion=%s"
                " | branch=%s | elapsed=%ds/%ds"
                " — workflow did not complete within budget.",
                run_data.get("id"),
                run_data.get("status"),
                run_data.get("conclusion", "—"),
                branch, elapsed, MAX_WAIT_S,
            )
            return ToolResult(
                tool_name=self.name,
                status="failure",
                summary=f"CI timed out after {MAX_WAIT_S}s (status: {run_data.get('status')}).",
                outputs={
                    "ci_run_id": run_data.get("id", 0),
                    "ci_conclusion": "timeout",
                    "ci_run_url": run_data.get("html_url", ""),
                    "ci_status": run_data.get("status", ""),
                },
                errors=["Workflow did not complete within timeout."],
            )

        # ── Success path ─────────────────────────────────────────────
        conclusion = run_data.get("conclusion", "unknown")
        run_id = run_data.get("id", 0)
        run_url = run_data.get("html_url", "")

        logger.info(
            "[WaitForCI] === CI POLL DONE ==="
            " | run_id=%s | conclusion=%s | branch=%s"
            " | elapsed=%ds | url=%s",
            run_id, conclusion, branch, elapsed, run_url,
        )

        return ToolResult(
            tool_name=self.name,
            status="success",
            summary=f"CI run #{run_id} completed: {conclusion}.",
            outputs={
                "ci_run_id": run_id,
                "ci_conclusion": conclusion,
                "ci_run_url": run_url,
                "ci_status": "completed",
            },
        )


def _empty_outputs() -> dict[str, Any]:
    return {
        "ci_run_id": 0,
        "ci_conclusion": "",
        "ci_run_url": "",
        "ci_status": "",
    }
