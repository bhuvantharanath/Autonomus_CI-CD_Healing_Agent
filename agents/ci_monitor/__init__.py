"""CI Monitor Agent – watches CI/CD pipelines for failures and triggers healing."""

from __future__ import annotations

from typing import Any

from agents.base import AgentResult, BaseAgent


class CIMonitorAgent(BaseAgent):
    """Monitors CI/CD pipelines (GitHub Actions, etc.) and reports status."""

    name = "ci_monitor"

    async def run(self, context: dict[str, Any]) -> AgentResult:
        repo_url: str = context.get("repo_url", "")
        github_token: str = context.get("github_token", "")

        if not repo_url:
            return AgentResult(
                agent_name=self.name,
                status="failure",
                errors=["No repo_url provided."],
            )

        # TODO: Use GitHub API to fetch workflow runs
        pipeline_status = await self._fetch_pipeline_status(repo_url, github_token)

        return AgentResult(
            agent_name=self.name,
            status="success",
            summary=f"Fetched CI status for {repo_url}.",
            details={"pipelines": pipeline_status},
        )

    async def _fetch_pipeline_status(self, repo_url: str, token: str) -> list[dict]:
        """Fetch recent CI workflow runs from GitHub Actions.

        In production this makes authenticated requests to
        https://api.github.com/repos/{owner}/{repo}/actions/runs
        """
        # Stub data for development
        return [
            {
                "workflow": "CI",
                "branch": "main",
                "status": "completed",
                "conclusion": "failure",
                "run_url": f"{repo_url}/actions/runs/0",
            }
        ]
