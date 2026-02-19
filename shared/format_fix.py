"""Deterministic fix-line formatter.

Canonical format (no variation allowed):

    {BUG_TYPE} error in {file} line {line} → Fix: {standardized_message}

Works with:
  • ``FailureRecord`` / ``FixRecord`` dataclass instances
  • Plain dicts from results.json (``{"bug_type", "file", "line", ...}``)
"""

from __future__ import annotations

from typing import Any

from agents.run_memory import FailureRecord, FixRecord, RunMemory


def format_fix(
    *,
    bug_type: str,
    file: str,
    line: int,
    message: str,
) -> str:
    """Return the single canonical display string for a fix.

    >>> format_fix(bug_type="TypeError", file="app/main.py", line=42,
    ...            message="replaced str concat with f-string")
    'TypeError error in app/main.py line 42 → Fix: replaced str concat with f-string'
    """
    return f"{bug_type} error in {file} line {line} → Fix: {message}"


# ── Convenience wrappers ─────────────────────────────────────────────

def format_failure_record(
    failure: FailureRecord,
) -> str:
    """Format a ``FailureRecord`` using its own ``standardized_message``."""
    return format_fix(
        bug_type=failure.bug_type,
        file=failure.file,
        line=failure.line,
        message=failure.standardized_message,
    )


def format_fix_record(
    fix: FixRecord,
    memory: RunMemory | None = None,
) -> str:
    """Format a ``FixRecord``.

    If *memory* is provided the bug_type is resolved from the matching
    ``FailureRecord``; otherwise falls back to ``"unknown"``.
    """
    bug_type = "unknown"
    if memory is not None:
        for f in memory.failures:
            if f.file == fix.file and f.line == fix.line:
                bug_type = f.bug_type
                break
        else:
            for f in memory.failures:
                if f.file == fix.file:
                    bug_type = f.bug_type
                    break

    return format_fix(
        bug_type=bug_type,
        file=fix.file,
        line=fix.line,
        message=fix.change_summary,
    )


def format_fix_dict(fix: dict[str, Any]) -> str:
    """Format a plain fix dict from ``results.json``.

    Expected keys: ``bug_type``, ``file``, ``line``, ``commit_message``.
    """
    return format_fix(
        bug_type=fix.get("bug_type", "unknown"),
        file=fix.get("file", "unknown"),
        line=fix.get("line", 0),
        message=fix.get("commit_message", ""),
    )


def format_all(fixes: list[dict[str, Any]]) -> list[str]:
    """Format every fix dict in a results.json ``fixes`` array."""
    return [format_fix_dict(f) for f in fixes]
