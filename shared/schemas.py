"""Shared schemas used across agents and backend."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PipelineRun:
    job_id: str
    repo_url: str
    status: str  # success | failure | partial | healed
    timestamp: str
    agent_results: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class BugReport:
    test_name: str
    category: str
    severity: str  # high | medium | low
    message: str
    traceback: str = ""
    file_path: str = ""
    line_number: int | None = None


@dataclass
class FixProposal:
    bug: BugReport
    suggestion: str
    patch: str | None = None  # unified diff
    confidence: float = 0.0
    status: str = "pending"  # pending | applied | rejected


@dataclass
class HealIteration:
    """Snapshot of one heal-loop iteration."""
    iteration: int
    bugs_found: int
    fixes_applied: int
    all_passed: bool
    agent_summaries: dict[str, str] = field(default_factory=dict)
    timestamp: str = ""


@dataclass
class HealLoopResult:
    """Aggregated result of the entire heal loop."""
    status: str  # healed | partial | failed
    iterations_used: int
    max_iterations: int
    total_bugs_found: int
    total_fixes_applied: int
    iterations: list[HealIteration] = field(default_factory=list)
