"""Pipeline orchestrator – runs the full CI-driven DevOps workflow.

Workflow:
  1. One-time repo analysis (RepoAnalysisAgent)
  2. CI-driven reasoning loop (8 tools × up to 5 iterations)
     RUN_TESTS → CLASSIFY → PLAN_FIX → APPLY_PATCH →
     COMMIT_PUSH → WAIT_FOR_CI → FETCH_CI_RESULTS → VERIFY
  3. Report results
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agents.base import AgentResult
from agents.repo_analysis import RepoAnalysisAgent
from agents.reasoning_loop import run_reasoning_loop, ReasoningLoopResult
from shared.results_exporter import export_results

RESULTS_FILE = Path(__file__).resolve().parents[1] / "shared" / "results.json"


async def run_pipeline(
    repo_path: str,
    repo_url: str = "",
    job_id: str = "",
    config: dict[str, Any] | None = None,
    max_iterations: int = 5,
) -> dict[str, Any]:
    """Execute the CI-driven self-healing pipeline.

    1. Repo analysis (one-time)
    2. Reasoning loop — patches + commits + CI verification
    3. Persist results
    """

    pipeline_start = time.monotonic()

    context: dict[str, Any] = {
        "repo_path": repo_path,
        "repo_url": repo_url,
        "job_id": job_id,
        **(config or {}),
    }

    results: list[dict] = []

    # ── 1. Repo analysis (one-time pre-step) ─────────────────────────
    repo_agent = RepoAnalysisAgent()
    repo_result: AgentResult = await repo_agent.run(context)
    results.append(repo_result.to_dict())
    if repo_result.details:
        context.update(repo_result.details)

    # ── 2. CI-driven reasoning loop ──────────────────────────────────
    # Commit/push is now INSIDE the loop — no separate step needed
    loop_result: ReasoningLoopResult = await run_reasoning_loop(
        repo_path=repo_path,
        max_iterations=max_iterations,
        config=context,
    )
    results.append({
        "agent_name": "reasoning_loop",
        "status": loop_result.status,
        "summary": (
            f"{loop_result.status}: {loop_result.iterations_used} iteration(s), "
            f"{loop_result.total_bugs_found} bug(s), "
            f"{loop_result.total_fixes_applied} fix(es)"
        ),
        "details": loop_result.to_dict(),
        "errors": [],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

    runtime_seconds = time.monotonic() - pipeline_start

    run_record = {
        "job_id": job_id,
        "repo_url": repo_url,
        "status": loop_result.status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agent_results": results,
        "reasoning_loop": loop_result.to_dict(),
    }

    _persist(run_record)

    # ── 3. Export canonical results.json ─────────────────────────────
    if loop_result._memory_ref is not None:
        export_results(
            memory=loop_result._memory_ref,
            repo_url=repo_url,
            branch=context.get("branch", "unknown"),
            team_name=context.get("team_name", "default"),
            leader_name=context.get("leader_name", "unknown"),
            runtime_seconds=runtime_seconds,
        )

    return run_record


def _persist(run_record: dict) -> None:
    """Append run to shared/results.json."""
    RESULTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    if RESULTS_FILE.exists():
        data = json.loads(RESULTS_FILE.read_text())
    else:
        data = {"runs": []}
    data["runs"].append(run_record)
    RESULTS_FILE.write_text(json.dumps(data, indent=2))
