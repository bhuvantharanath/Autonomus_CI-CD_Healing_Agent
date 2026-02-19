"""FailureClassifierAgent tool — converts test logs into structured bugs.

Tool 2 in the reasoning loop. Takes raw test output, runs regex +
optional LLM classification, and produces a prioritised list of bugs
with severity and fix hints.
"""

from __future__ import annotations

import logging
from typing import Any

from agents.tools.registry import AgentTool, ToolResult
from agents.bug_classifier.error_classifier import (
    BugReport,
    BugType,
    classify_errors_async,
)

logger = logging.getLogger(__name__)

# ── Severity & hint tables ───────────────────────────────────────────

_SEVERITY: dict[str, str] = {
    BugType.SYNTAX.value: "high",
    BugType.INDENTATION.value: "high",
    BugType.IMPORT.value: "high",
    BugType.TYPE_ERROR.value: "medium",
    BugType.LOGIC.value: "medium",
    BugType.LINTING.value: "low",
}

_FIX_HINTS: dict[str, str] = {
    BugType.SYNTAX.value: "Check for missing colons, brackets, or parentheses.",
    BugType.INDENTATION.value: "Align indentation to surrounding block.",
    BugType.IMPORT.value: "Verify module name and ensure package is installed.",
    BugType.TYPE_ERROR.value: "Check argument types match the function signature.",
    BugType.LOGIC.value: "Review assertion: expected vs actual values differ.",
    BugType.LINTING.value: "Remove unused import or fix style violation.",
}


def _normalize_path(filepath: str, repo_path: str) -> str:
    """Strip the repo_path prefix (temp clone dir) from absolute file paths.

    Returns a clean relative path like 'src/utils.py' instead of
    '/var/folders/.../heal_xxx/src/utils.py'.
    """
    if not filepath or not repo_path:
        return filepath
    # Normalize both paths
    from pathlib import Path
    try:
        fp = Path(filepath)
        rp = Path(repo_path).resolve()
        if fp.is_absolute():
            try:
                return str(fp.relative_to(rp))
            except ValueError:
                # Also try without resolve in case one is resolved and other isn't
                try:
                    return str(fp.relative_to(Path(repo_path)))
                except ValueError:
                    pass
        # If path contains the repo dir as a substring, strip it
        repo_str = str(rp).rstrip("/") + "/"
        if filepath.startswith(repo_str):
            return filepath[len(repo_str):]
        repo_str2 = repo_path.rstrip("/") + "/"
        if filepath.startswith(repo_str2):
            return filepath[len(repo_str2):]
    except Exception:
        pass
    return filepath


class FailureClassifierTool(AgentTool):
    """Classifies test failures into structured, prioritised bug reports."""

    name = "failure_classifier"
    description = (
        "Parses raw test output using regex patterns and optional LLM "
        "fallback to produce structured BugReport objects with file, "
        "line, bug_type, severity, and fix hints."
    )
    input_keys = ["test_output"]
    output_keys = ["classified_bugs"]

    async def execute(self, state: dict[str, Any]) -> ToolResult:
        test_output: str = state.get("test_output", "")
        repo_path: str = state.get("repo_path", "")

        logger.info(
            "[FailureClassifier] === EXECUTE START === | "
            "test_output_len=%d | repo_path=%s",
            len(test_output), repo_path,
        )
        if test_output:
            # Log first and last few lines of test output for diagnostics
            lines = test_output.strip().splitlines()
            preview_head = lines[:5]
            preview_tail = lines[-5:] if len(lines) > 5 else []
            logger.info(
                "[FailureClassifier] test_output preview (first 5 lines): %s",
                preview_head,
            )
            if preview_tail:
                logger.info(
                    "[FailureClassifier] test_output preview (last 5 lines): %s",
                    preview_tail,
                )

        if not test_output:
            return ToolResult(
                tool_name=self.name,
                status="skipped",
                summary="No test output to classify.",
                outputs={"classified_bugs": []},
            )

        # ── Regex + LLM classification ───────────────────────────────
        bugs: list[BugReport] = await classify_errors_async(test_output)

        if not bugs:
            return ToolResult(
                tool_name=self.name,
                status="success",
                summary="No classifiable errors found in test output.",
                outputs={"classified_bugs": []},
            )

        # ── Enrich, sort, deduplicate ────────────────────────────────
        classified: list[dict[str, Any]] = []
        for bug in bugs:
            entry = bug.to_dict()
            # Normalize file paths: strip repo_path prefix to get relative paths
            entry["file"] = _normalize_path(entry.get("file", ""), repo_path)
            entry["severity"] = _SEVERITY.get(bug.bug_type, "low")
            entry["fix_hint"] = _FIX_HINTS.get(
                bug.bug_type, "Manual investigation required."
            )
            classified.append(entry)

        severity_order = {"high": 0, "medium": 1, "low": 2}
        classified.sort(
            key=lambda b: (severity_order.get(b["severity"], 3), b.get("file", ""), b.get("line", 0))
        )

        seen: set[tuple[str, int, str]] = set()
        unique: list[dict[str, Any]] = []
        for b in classified:
            key = (b["file"], b["line"], b["bug_type"])
            if key not in seen:
                seen.add(key)
                unique.append(b)

        high = sum(1 for b in unique if b["severity"] == "high")
        med = sum(1 for b in unique if b["severity"] == "medium")
        low = sum(1 for b in unique if b["severity"] == "low")

        logger.info("[FailureClassifier] %d bug(s): %d high, %d med, %d low",
                     len(unique), high, med, low)

        return ToolResult(
            tool_name=self.name,
            status="success",
            summary=f"Classified {len(unique)} bug(s): {high} high, {med} medium, {low} low.",
            outputs={"classified_bugs": unique},
        )
