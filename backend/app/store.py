"""In-memory run store for tracking agent pipeline executions."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class RunState:
    """Tracks the live state of a single pipeline run."""

    run_id: str
    repo_url: str
    team_name: str
    leader_name: str
    status: str = "queued"  # queued | running | completed | failed
    current_agent: str = ""
    progress: list[dict[str, Any]] = field(default_factory=list)
    result: dict[str, Any] | None = None
    created_at: str = field(default_factory=_utcnow_iso)
    updated_at: str = field(default_factory=_utcnow_iso)

    # ── Live tracking fields (updated as iterations progress) ────────
    branch: str = ""
    iteration_count: int = 0
    max_iterations: int = 0
    latest_ci_status: str = ""          # PASSED | FAILED | ""
    total_failures_detected: int = 0
    total_fixes_applied: int = 0
    runtime_seconds: float = 0.0
    final_results: dict[str, Any] | None = None

    # ── Step-level progress (updated on every phase transition) ───────
    current_step: str = ""              # RUN_TESTS | CLASSIFY | APPLY_PATCH | …
    current_iteration: int = 0
    latest_message: str = ""

    # Async event fired every time state changes so SSE listeners wake up
    _event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)

    def push_progress(self, agent_name: str, status: str, message: str = "") -> None:
        self.current_agent = agent_name
        self.status = "running"
        if message:
            self.latest_message = message
        self.updated_at = _utcnow_iso()
        self.progress.append(
            {
                "agent": agent_name,
                "status": status,
                "message": message,
                "timestamp": self.updated_at,
            }
        )
        self._notify()

    def update_step(self, step: str, iteration: int, message: str = "") -> None:
        """Update step-level progress on every phase transition."""
        self.current_step = step
        self.current_iteration = iteration
        if message:
            self.latest_message = message
        self.updated_at = _utcnow_iso()
        self._notify()

    def complete(self, result: dict[str, Any]) -> None:
        self.status = "completed"
        self.result = result
        self.updated_at = _utcnow_iso()
        self._notify()

    def fail(self, error: str) -> None:
        self.status = "failed"
        self.updated_at = _utcnow_iso()
        self.progress.append(
            {"agent": self.current_agent, "status": "error", "message": error, "timestamp": self.updated_at}
        )
        self._notify()

    def update_iteration(
        self,
        iteration: int,
        ci_status: str = "",
        failures: int = 0,
        fixes: int = 0,
    ) -> None:
        """Update live iteration tracking fields."""
        self.iteration_count = iteration
        if ci_status:
            self.latest_ci_status = ci_status
        self.total_failures_detected = failures
        self.total_fixes_applied = fixes
        self.updated_at = _utcnow_iso()
        self._notify()

    def _notify(self) -> None:
        """Wake SSE listeners without the set/clear race condition."""
        self._event.set()
        # Do NOT call clear() here — let the listener clear after waking.

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "repo_url": self.repo_url,
            "team_name": self.team_name,
            "leader_name": self.leader_name,
            "status": self.status,
            "current_agent": self.current_agent,
            "current_step": self.current_step,
            "current_iteration": self.current_iteration,
            "latest_message": self.latest_message,
            "branch": self.branch,
            "iteration_count": self.iteration_count,
            "max_iterations": self.max_iterations,
            "latest_ci_status": self.latest_ci_status,
            "total_failures_detected": self.total_failures_detected,
            "total_fixes_applied": self.total_fixes_applied,
            "runtime_seconds": round(self.runtime_seconds, 2),
            "progress": self.progress,
            "result": self.result,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


# ── Global in-memory store ──────────────────────────────────────────
_runs: dict[str, RunState] = {}


def create_run(run_id: str, repo_url: str, team_name: str, leader_name: str) -> RunState:
    state = RunState(run_id=run_id, repo_url=repo_url, team_name=team_name, leader_name=leader_name)
    _runs[run_id] = state
    return state


def get_run(run_id: str) -> RunState | None:
    return _runs.get(run_id)


def all_runs() -> list[dict[str, Any]]:
    return [r.to_dict() for r in _runs.values()]
