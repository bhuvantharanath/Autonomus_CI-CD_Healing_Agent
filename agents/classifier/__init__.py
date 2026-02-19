"""Error Classifier Agent — Agent 2 in the heal loop.

Takes raw test output from the Analyzer and classifies each failure
into structured BugReport objects with severity and fix hints.
"""

from __future__ import annotations

import logging
from typing import Any

from agents.base import AgentResult, BaseAgent
from agents.bug_classifier.error_classifier import (
    BugReport,
    BugType,
    classify_errors,
    classify_errors_async,
)

logger = logging.getLogger(__name__)

# Severity mapping per bug type
_SEVERITY: dict[str, str] = {
    BugType.SYNTAX.value: "high",
    BugType.INDENTATION.value: "high",
    BugType.IMPORT.value: "high",
    BugType.TYPE_ERROR.value: "medium",
    BugType.LOGIC.value: "medium",
    BugType.LINTING.value: "low",
}

# Deterministic fix hints per bug type
_FIX_HINTS: dict[str, str] = {
    BugType.SYNTAX.value: "Check for missing colons, brackets, or parentheses on the reported line.",
    BugType.INDENTATION.value: "Align indentation to match surrounding block (use spaces, not tabs).",
    BugType.IMPORT.value: "Verify module name spelling and ensure package is installed.",
    BugType.TYPE_ERROR.value: "Check argument types match the function signature at the call site.",
    BugType.LOGIC.value: "Review the assertion: expected vs actual values differ — check computation logic.",
    BugType.LINTING.value: "Remove unused import or fix style violation.",
}


class ClassifierAgent(BaseAgent):
    """Classifies test failures into structured, prioritised bug reports."""

    name = "classifier"

    async def run(self, context: dict[str, Any]) -> AgentResult:
        test_output: str = context.get("test_output", "")

        if not test_output:
            return AgentResult(
                agent_name=self.name,
                status="skipped",
                summary="No test output to classify.",
                details={"classified_bugs": []},
            )

        # ── Classify via regex + optional LLM fallback ───────────────
        bugs: list[BugReport] = await classify_errors_async(test_output)

        if not bugs:
            return AgentResult(
                agent_name=self.name,
                status="success",
                summary="No classifiable errors found in test output.",
                details={"classified_bugs": []},
            )

        # ── Enrich with severity and fix hints ───────────────────────
        classified: list[dict[str, Any]] = []
        for bug in bugs:
            entry = bug.to_dict()
            entry["severity"] = _SEVERITY.get(bug.bug_type, "low")
            entry["fix_hint"] = _FIX_HINTS.get(bug.bug_type, "Manual investigation required.")
            classified.append(entry)

        # ── Sort by severity (high → medium → low), then file, then line ──
        severity_order = {"high": 0, "medium": 1, "low": 2}
        classified.sort(key=lambda b: (severity_order.get(b["severity"], 3), b.get("file", ""), b.get("line", 0)))

        # Deduplicate by (file, line, bug_type)
        seen: set[tuple[str, int, str]] = set()
        unique: list[dict[str, Any]] = []
        for b in classified:
            key = (b["file"], b["line"], b["bug_type"])
            if key not in seen:
                seen.add(key)
                unique.append(b)

        logger.info("Classified %d unique bug(s) from test output", len(unique))

        return AgentResult(
            agent_name=self.name,
            status="success",
            summary=f"Classified {len(unique)} bug(s): "
                    f"{sum(1 for b in unique if b['severity'] == 'high')} high, "
                    f"{sum(1 for b in unique if b['severity'] == 'medium')} medium, "
                    f"{sum(1 for b in unique if b['severity'] == 'low')} low.",
            details={"classified_bugs": unique},
        )
