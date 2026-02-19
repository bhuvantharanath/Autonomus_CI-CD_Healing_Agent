"""Deterministic results.json exporter.

Converts the agent RunMemory (and pipeline metadata) into a
canonical ``results.json`` file with a fixed, competition-ready schema.

Usage::

    from shared.results_exporter import export_results

    export_results(
        memory=memory,                 # agents.run_memory.RunMemory
        repo_url="https://github.com/org/repo",
        branch="auto-heal-abc123",
        team_name="TeamAlpha",
        leader_name="Alice",
        runtime_seconds=247.3,
        output_path="shared/results.json",
    )
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agents.run_memory import RunMemory

# Default output location
_DEFAULT_OUTPUT = Path(__file__).resolve().parent / "results.json"


# ── Public API ───────────────────────────────────────────────────────

def export_results(
    memory: RunMemory,
    repo_url: str,
    branch: str,
    team_name: str,
    leader_name: str,
    runtime_seconds: float,
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    """Build the canonical results dict, write it to disk, and return it.

    Args:
        memory:          Populated RunMemory from the reasoning loop.
        repo_url:        GitHub repository URL.
        branch:          Branch created by the agent.
        team_name:       Team / org name.
        leader_name:     Team leader or runner name.
        runtime_seconds: Wall-clock seconds for the full pipeline run.
        output_path:     Where to write the JSON (default: shared/results.json).

    Returns:
        The complete results dictionary that was persisted.
    """
    results = build_results(
        memory=memory,
        repo_url=repo_url,
        branch=branch,
        team_name=team_name,
        leader_name=leader_name,
        runtime_seconds=runtime_seconds,
    )

    dest = Path(output_path) if output_path else _DEFAULT_OUTPUT
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(results, indent=2) + "\n")
    return results


def build_results(
    memory: RunMemory,
    repo_url: str,
    branch: str,
    team_name: str,
    leader_name: str,
    runtime_seconds: float,
) -> dict[str, Any]:
    """Build the canonical results dictionary without writing to disk.

    Useful for testing or when the caller handles persistence.
    """
    fixes = _build_fixes(memory)
    failures_detected = _build_failures_detected(memory)
    ci_timeline = _build_ci_timeline(memory)
    total_commits = _count_unique_commits(memory)
    final_ci = _resolve_final_ci_status(memory)

    return {
        "repository_url": repo_url,
        "branch": branch,
        "team_name": team_name,
        "leader_name": leader_name,
        "total_failures_detected": len(memory.failures),
        "total_fixes_applied": len(memory.fixes),
        "final_ci_status": final_ci,
        "runtime_seconds": round(runtime_seconds, 2),
        "failures_detected": failures_detected,
        "fixes": fixes,
        "ci_timeline": ci_timeline,
        "score": _calculate_score(
            final_ci=final_ci,
            runtime_seconds=runtime_seconds,
            total_commits=total_commits,
        ),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# ── Internal helpers ─────────────────────────────────────────────────

def _build_failures_detected(memory: RunMemory) -> list[dict[str, Any]]:
    """Build the failures array from RunMemory failure records.

    Each detection entry includes a 'description' in the canonical test-case
    format: '{file} — Line {line}: {message}'
    """
    seen: set[tuple[str, int, str]] = set()
    failures: list[dict[str, Any]] = []
    for failure in memory.failures:
        key = (failure.file, failure.line, failure.bug_type)
        if key in seen:
            continue
        seen.add(key)

        # Build canonical detection description
        # Format: "src/utils.py — Line 15: Unused import 'os'"
        description = f"{failure.file} — Line {failure.line}: {failure.standardized_message}"

        failures.append({
            "file": failure.file,
            "line": failure.line,
            "bug_type": failure.bug_type,
            "message": failure.standardized_message,
            "description": description,
            "iteration": failure.iteration,
        })
    failures.sort(key=lambda f: (f["file"], f["line"]))
    return failures


def _build_fixes(memory: RunMemory) -> list[dict[str, Any]]:
    """Build the fixes array from RunMemory fix records.

    Output is sorted by (file, line) for deterministic ordering.
    Each fix includes a 'description' in the canonical test-case
    format: '{BUG_TYPE} error in {file} line {line} → Fix: {message}'
    """
    fixes: list[dict[str, Any]] = []
    for fix in memory.fixes:
        # Determine status: if the fix's iteration has a later CI run
        # that passed, mark as "verified"; otherwise "applied".
        status = "applied"
        for ci in memory.ci_runs:
            if ci.iteration >= fix.iteration and ci.status == "success":
                status = "verified"
                break

        # Normalize file path: strip temp clone dir to get relative path
        rel_file = _strip_temp_prefix(fix.file)

        bug_type = _infer_bug_type(fix, memory)
        failure_msg = _infer_failure_message(fix, memory)
        commit_msg = fix.change_summary

        # Build the canonical description line required by the competition
        # Format: "{BUG_TYPE} error in {file} line {line} → Fix: {message}"
        description = f"{bug_type} error in {rel_file} line {fix.line} → Fix: {commit_msg}"

        fixes.append({
            "file": rel_file,
            "bug_type": bug_type,
            "line": fix.line,
            "commit_message": commit_msg,
            "status": status,
            "description": description,
            "failure_message": failure_msg,
        })
    fixes.sort(key=lambda f: (f["file"], f["line"]))
    return fixes


def _strip_temp_prefix(filepath: str) -> str:
    """Strip temp clone directory prefix from absolute paths.

    Converts '/var/folders/.../heal_xxx/src/app.py' → 'src/app.py'
    Converts '/tmp/heal_xxx/src/app.py' → 'src/app.py'
    Leaves relative paths like 'src/app.py' unchanged.
    """
    import re as _re
    # Match common temp dir patterns produced by tempfile.mkdtemp(prefix="heal_")
    # e.g. /var/folders/.../heal_abcdef/ or /tmp/heal_abcdef/
    m = _re.search(r'/heal_[^/]+/(.*)', filepath)
    if m:
        return m.group(1)
    # Generic: if it looks like an absolute path, try to find src/ or a
    # common project root marker
    if filepath.startswith("/"):
        for marker in ("src/", "lib/", "app/", "tests/", "test/"):
            idx = filepath.find(marker)
            if idx != -1:
                return filepath[idx:]
    return filepath


def _normalize_for_match(filepath: str) -> str:
    """Normalize a file path for comparison (strip temp prefix, get basename-relative)."""
    return _strip_temp_prefix(filepath)


def _infer_bug_type(fix, memory: RunMemory) -> str:
    """Match a fix back to its failure record to extract bug_type."""
    fix_file = _normalize_for_match(fix.file)
    # Exact match on normalized file + line
    for failure in memory.failures:
        if _normalize_for_match(failure.file) == fix_file and failure.line == fix.line:
            return failure.bug_type
    # Fallback: same file, any line
    for failure in memory.failures:
        if _normalize_for_match(failure.file) == fix_file:
            return failure.bug_type
    # Fallback: basename match
    from pathlib import Path as _Path
    fix_basename = _Path(fix_file).name
    for failure in memory.failures:
        if _Path(failure.file).name == fix_basename and failure.line == fix.line:
            return failure.bug_type
    for failure in memory.failures:
        if _Path(failure.file).name == fix_basename:
            return failure.bug_type
    return "unknown"


def _infer_failure_message(fix, memory: RunMemory) -> str:
    """Match a fix back to its failure record to extract the original message."""
    fix_file = _normalize_for_match(fix.file)
    for failure in memory.failures:
        if _normalize_for_match(failure.file) == fix_file and failure.line == fix.line:
            return failure.standardized_message
    for failure in memory.failures:
        if _normalize_for_match(failure.file) == fix_file:
            return failure.standardized_message
    # Fallback: basename match
    from pathlib import Path as _Path
    fix_basename = _Path(fix_file).name
    for failure in memory.failures:
        if _Path(failure.file).name == fix_basename and failure.line == fix.line:
            return failure.standardized_message
    for failure in memory.failures:
        if _Path(failure.file).name == fix_basename:
            return failure.standardized_message
    return ""


def _build_ci_timeline(memory: RunMemory) -> list[dict[str, Any]]:
    """Build the CI timeline array from RunMemory CI run records."""
    return [
        {
            "iteration": ci.iteration,
            "status": "PASSED" if ci.status == "success" else "FAILED",
            "timestamp": ci.start_time,
        }
        for ci in memory.ci_runs
    ]


def _resolve_final_ci_status(memory: RunMemory) -> str:
    """Return 'PASSED' or 'FAILED' based on the last CI run.

    When no CI runs were recorded *and* no failures were detected the
    repo was already clean — return PASSED instead of a misleading
    FAILED.
    """
    latest = memory.latest_ci_run()
    if latest is None:
        # No CI runs: if there are also no failures the repo is clean.
        return "PASSED" if not memory.failures else "FAILED"
    return "PASSED" if latest.status == "success" else "FAILED"


def _count_unique_commits(memory: RunMemory) -> int:
    """Count the number of distinct commit SHAs across all fixes."""
    return len({fix.commit_hash for fix in memory.fixes if fix.commit_hash})


def _calculate_score(
    final_ci: str,
    runtime_seconds: float,
    total_commits: int,
) -> dict[str, Any]:
    """Calculate the competition score.

    Rules:
      • base:  100 points
      • +10   if runtime < 300 seconds
      • −2    per commit after 20
      • final_score = max(base + bonuses + penalties, 0)
    """
    base = 100

    speed_bonus = 10 if runtime_seconds < 300 else 0

    excess_commits = max(total_commits - 20, 0)
    commit_penalty = -2 * excess_commits

    final_score = max(base + speed_bonus + commit_penalty, 0)

    return {
        "base": base,
        "speed_bonus": speed_bonus,
        "commit_penalty": commit_penalty,
        "total_commits": total_commits,
        "final_score": final_score,
    }
