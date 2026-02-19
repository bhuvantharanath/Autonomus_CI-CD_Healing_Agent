"""Agent orchestration endpoints.

POST /run-agent          – kick off a pipeline, returns run_id immediately
GET  /status/{run_id}    – JSON snapshot of current run state
GET  /results/{run_id}   – final canonical results.json for a completed run
GET  /stream/{run_id}    – SSE stream of live progress updates
GET  /runs               – list all runs
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.store import create_run, get_run, all_runs, RunState
from app.orchestrator import execute_pipeline

router = APIRouter()
logger = logging.getLogger(__name__)

# Keep strong references so background tasks aren't garbage-collected.
_background_tasks: dict[str, asyncio.Task] = {}


def _handle_task_done(task: asyncio.Task, run_id: str, state: RunState) -> None:
    """Callback invoked when a pipeline task finishes (success or crash)."""
    _background_tasks.pop(run_id, None)
    if task.cancelled():
        state.fail("Pipeline task was cancelled")
    elif exc := task.exception():
        logger.exception("Pipeline %s crashed: %s", run_id, exc)
        state.fail(f"Unhandled error: {exc}")


# ── Request / Response schemas ───────────────────────────────────────

class RunAgentRequest(BaseModel):
    repo_url: str
    team_name: str
    leader_name: str


class RunAgentResponse(BaseModel):
    run_id: str
    status: str
    message: str


# ── POST /run-agent ──────────────────────────────────────────────────

@router.post("/run-agent", response_model=RunAgentResponse)
async def run_agent(request: RunAgentRequest):
    """Create a unique run, launch the orchestrator in the background, and
    return the run_id immediately."""

    run_id = str(uuid.uuid4())

    state: RunState = create_run(
        run_id=run_id,
        repo_url=request.repo_url,
        team_name=request.team_name,
        leader_name=request.leader_name,
    )

    # Background task – store a reference so it isn't garbage-collected,
    # and add an error callback to surface unhandled exceptions.
    task = asyncio.create_task(execute_pipeline(state), name=f"pipeline-{run_id}")
    _background_tasks[run_id] = task
    task.add_done_callback(lambda t: _handle_task_done(t, run_id, state))

    return RunAgentResponse(
        run_id=run_id,
        status="queued",
        message=f"Pipeline queued for {request.repo_url}",
    )


# ── GET /status/{run_id}  (JSON snapshot) ────────────────────────────

@router.get("/status/{run_id}")
async def get_status(run_id: str):
    """Return the current run state as a JSON snapshot.

    Includes iteration count, latest CI status, and partial results
    so callers can poll without establishing an SSE connection.
    """
    state = get_run(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

    # Filter git-specific progress entries for the frontend
    git_ops = [
        p for p in state.progress if p.get("agent") == "github"
    ]

    return {
        "run_id": state.run_id,
        "status": state.status,
        "repo_url": state.repo_url,
        "branch": state.branch,
        "team_name": state.team_name,
        "leader_name": state.leader_name,
        "current_step": state.current_step,
        "current_iteration": state.current_iteration,
        "latest_message": state.latest_message,
        "iteration_count": state.iteration_count,
        "max_iterations": state.max_iterations,
        "latest_ci_status": state.latest_ci_status,
        "total_failures_detected": state.total_failures_detected,
        "total_fixes_applied": state.total_fixes_applied,
        "runtime_seconds": round(state.runtime_seconds, 2),
        "current_agent": state.current_agent,
        "created_at": state.created_at,
        "updated_at": state.updated_at,
        "git_operations": git_ops,
    }


# ── GET /results/{run_id}  (final canonical results) ────────────────

@router.get("/results/{run_id}")
async def get_results(run_id: str):
    """Return the final canonical results for a completed run.

    Returns 409 if the run is still in progress, 404 if the run_id
    doesn't exist, or 204 if the run failed and has no results.
    """
    state = get_run(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

    if state.status not in ("completed", "failed"):
        raise HTTPException(
            status_code=409,
            detail=f"Run {run_id} is still {state.status}. Poll /status/{run_id} until complete.",
        )

    if state.final_results is None:
        raise HTTPException(
            status_code=404,
            detail=f"Run {run_id} finished as '{state.status}' but produced no results.",
        )

    return state.final_results


# ── GET /stream/{run_id}  (Server-Sent Events) ──────────────────────

@router.get("/stream/{run_id}")
async def stream_status(run_id: str):
    """Stream progress updates as Server-Sent Events (SSE).

    Each event is a JSON-encoded snapshot of the current RunState.
    The stream closes automatically once the run reaches a terminal state
    (completed / failed).
    """

    state = get_run(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

    return StreamingResponse(
        _event_generator(state),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _event_generator(state: RunState):
    """Yield SSE-formatted events whenever the run state changes."""

    while True:
        snapshot = state.to_dict()
        yield f"data: {json.dumps(snapshot)}\n\n"

        # Terminal states → close the stream
        if state.status in ("completed", "failed"):
            return

        # Wait for the next state change (or poll every 2 s as fallback)
        try:
            await asyncio.wait_for(state._event.wait(), timeout=2.0)
            state._event.clear()  # reset so we block again on next iteration
        except asyncio.TimeoutError:
            pass  # heartbeat: re-send current snapshot


# ── GET /runs ────────────────────────────────────────────────────────

@router.get("/runs")
async def list_runs():
    """Return all pipeline runs stored in memory."""
    return {"runs": all_runs()}


# ── GET /agents ──────────────────────────────────────────────────────

@router.get("/")
async def list_agents():
    """List available agent modules."""
    return {
        "agents": [
            {"name": "repo_analysis", "description": "Repository structure & code analysis"},
            {"name": "test_runner", "description": "Automated test execution"},
            {"name": "bug_classifier", "description": "Bug detection & classification"},
            {"name": "fix_generator", "description": "Automated fix generation"},
            {"name": "ci_monitor", "description": "CI/CD pipeline monitoring"},
        ]
    }
