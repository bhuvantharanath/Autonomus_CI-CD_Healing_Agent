"""Persistent run state memory — append-only structured records.

Provides a shared memory container that accumulates structured data
across all reasoning-loop iterations.  Every iteration appends
FailureRecord, FixRecord, and CIRunRecord objects.  Previous entries
are never overwritten or cleared.

The final aggregated state is exported via ``to_dict()`` for
``results.json`` persistence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


# ── Record dataclasses ───────────────────────────────────────────────

@dataclass(frozen=True)
class FailureRecord:
    """A single classified failure."""

    file: str
    line: int
    bug_type: str
    standardized_message: str
    iteration: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "file": self.file,
            "line": self.line,
            "bug_type": self.bug_type,
            "standardized_message": self.standardized_message,
            "iteration": self.iteration,
        }


@dataclass(frozen=True)
class FixRecord:
    """A single applied fix."""

    file: str
    line: int
    change_summary: str
    commit_hash: str
    iteration: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "file": self.file,
            "line": self.line,
            "change_summary": self.change_summary,
            "commit_hash": self.commit_hash,
            "iteration": self.iteration,
        }


@dataclass(frozen=True)
class CIRunRecord:
    """A single CI workflow run result."""

    iteration: int
    status: str          # "success" | "failure" | "timeout"
    start_time: str      # ISO 8601
    end_time: str        # ISO 8601

    def to_dict(self) -> dict[str, Any]:
        return {
            "iteration": self.iteration,
            "status": self.status,
            "start_time": self.start_time,
            "end_time": self.end_time,
        }


# ── RunMemory container ─────────────────────────────────────────────

class RunMemory:
    """Append-only memory shared across all reasoning-loop nodes.

    Rules:
      • ``append_*`` methods only add — they never clear or overwrite.
      • ``latest_ci_run()`` returns the most recent CI run record.
      • ``to_dict()`` exports the full aggregated state for results.json.
    """

    def __init__(self) -> None:
        self._failures: list[FailureRecord] = []
        self._fixes: list[FixRecord] = []
        self._ci_runs: list[CIRunRecord] = []

    # ── Read-only properties ─────────────────────────────────────────

    @property
    def failures(self) -> list[FailureRecord]:
        return sorted(self._failures, key=lambda f: (f.file, f.line))

    @property
    def fixes(self) -> list[FixRecord]:
        return sorted(self._fixes, key=lambda f: (f.file, f.line))

    @property
    def ci_runs(self) -> list[CIRunRecord]:
        return list(self._ci_runs)

    # ── Append methods (write-only, never overwrite) ─────────────────

    def append_failures(
        self,
        iteration: int,
        classified_bugs: list[dict[str, Any]],
    ) -> None:
        """Append classified failures for an iteration.

        Args:
            iteration:       Current iteration number.
            classified_bugs: List of bug dicts from FailureClassifierTool.
                             Each must have 'file', 'line', 'bug_type',
                             and 'message'.
        """
        # Deduplicate: remove any failures previously appended for this iteration
        self._failures = [f for f in self._failures if f.iteration != iteration]
        
        for bug in classified_bugs:
            record = FailureRecord(
                file=bug.get("file", "unknown"),
                line=bug.get("line", 0),
                bug_type=bug.get("bug_type", "unknown"),
                standardized_message=bug.get("message", ""),
                iteration=iteration,
            )
            self._failures.append(record)

    def append_fixes(
        self,
        iteration: int,
        applied_patches: list[dict[str, Any]],
        commit_hash: str,
    ) -> None:
        """Append applied fixes for an iteration.

        Args:
            iteration:       Current iteration number.
            applied_patches: List of patch dicts from PatchApplierTool.
                             Each must have 'file', 'description', and 'bug'.
            commit_hash:     Short SHA from CommitPushTool.
        """
        # Deduplicate: remove any fixes previously appended for this iteration
        self._fixes = [f for f in self._fixes if f.iteration != iteration]
        
        for patch in applied_patches:
            bug = patch.get("bug", {})
            record = FixRecord(
                file=patch.get("file", bug.get("file", "unknown")),
                line=bug.get("line", 0),
                change_summary=patch.get("description", ""),
                commit_hash=commit_hash,
                iteration=iteration,
            )
            self._fixes.append(record)

    def append_ci_run(
        self,
        iteration: int,
        status: str,
        start_time: str = "",
        end_time: str = "",
    ) -> None:
        """Append a CI run result for an iteration.

        Args:
            iteration:  Current iteration number.
            status:     CI conclusion: "success", "failure", or "timeout".
            start_time: ISO 8601 timestamp of CI start.
            end_time:   ISO 8601 timestamp of CI end.
        """
        # Deduplicate: remove any CI run previously appended for this iteration
        self._ci_runs = [r for r in self._ci_runs if r.iteration != iteration]
        
        if not start_time:
            start_time = datetime.now(timezone.utc).isoformat()
        if not end_time:
            end_time = datetime.now(timezone.utc).isoformat()

        record = CIRunRecord(
            iteration=iteration,
            status=status,
            start_time=start_time,
            end_time=end_time,
        )
        self._ci_runs.append(record)

    # ── Query methods ────────────────────────────────────────────────

    def latest_ci_run(self) -> CIRunRecord | None:
        """Return the most recent CI run, or None if no runs exist."""
        return self._ci_runs[-1] if self._ci_runs else None

    def failures_for_iteration(self, iteration: int) -> list[FailureRecord]:
        """Return all failures from a specific iteration."""
        return [f for f in self._failures if f.iteration == iteration]

    def fixes_for_iteration(self, iteration: int) -> list[FixRecord]:
        """Return all fixes from a specific iteration."""
        return [f for f in self._fixes if f.iteration == iteration]

    # ── Export ────────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """Export the full aggregated memory for results.json."""
        return {
            "failures": [f.to_dict() for f in self._failures],
            "fixes": [f.to_dict() for f in self._fixes],
            "ci_runs": [r.to_dict() for r in self._ci_runs],
            "summary": {
                "total_failures": len(self._failures),
                "total_fixes": len(self._fixes),
                "total_ci_runs": len(self._ci_runs),
                "unique_files_with_failures": len(
                    {f.file for f in self._failures}
                ),
                "unique_files_fixed": len(
                    {f.file for f in self._fixes}
                ),
            },
        }
