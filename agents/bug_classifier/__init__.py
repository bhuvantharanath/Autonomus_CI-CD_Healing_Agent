"""Bug Classifier Agent – analyzes test failures and classifies bugs.

Two classification layers:
1. **Structured error classifier** (``error_classifier`` module) – regex +
   optional LLM fallback that converts raw logs into ``BugReport`` objects
   with schema ``{file, line, bug_type, message}``.
2. **Legacy rule-based classifier** – kept as fallback for pre-structured
   failure dicts that already contain ``test_name`` / ``message`` keys.
"""

from __future__ import annotations

from typing import Any

from agents.base import AgentResult, BaseAgent
from agents.bug_classifier.error_classifier import (
    BugReport,
    BugType,
    classify_errors,
    classify_errors_async,
)


class BugClassifierAgent(BaseAgent):
    """Classifies bugs from test output and stack traces."""

    name = "bug_classifier"

    # Valid structured bug types
    BUG_TYPES = [t.value for t in BugType]

    # Legacy taxonomy (kept for backward-compat)
    CATEGORIES = [
        "syntax_error",
        "type_error",
        "null_reference",
        "index_out_of_bounds",
        "import_error",
        "assertion_failure",
        "timeout",
        "dependency_issue",
        "configuration_error",
        "concurrency_bug",
        "unknown",
    ]

    async def run(self, context: dict[str, Any]) -> AgentResult:
        """Classify bugs from test output.

        Accepts **either**:
        * ``context["test_output"]``  – raw log string  → structured classifier
        * ``context["test_results"]`` – list of failure dicts → legacy path
        """

        # ── Path A: raw log string → structured BugReports ──────────
        raw_log: str = context.get("test_output", "")
        if raw_log:
            return await self._classify_from_log(raw_log)

        # ── Path B: pre-structured failure dicts (legacy) ────────────
        test_results = context.get("test_results", [])
        if not test_results:
            return AgentResult(
                agent_name=self.name,
                status="skipped",
                summary="No test results to classify.",
            )
        return self._classify_legacy(test_results)

    # ── Structured classification (new) ──────────────────────────────

    async def _classify_from_log(self, raw_log: str) -> AgentResult:
        """Run regex + LLM-fallback classifier over a raw log string."""
        bugs: list[BugReport] = await classify_errors_async(raw_log)

        if not bugs:
            return AgentResult(
                agent_name=self.name,
                status="success",
                summary="No classifiable errors found in test output.",
                details={"bug_reports": [], "raw_log_length": len(raw_log)},
            )

        return AgentResult(
            agent_name=self.name,
            status="success",
            summary=f"Classified {len(bugs)} bug(s) from test output.",
            details={
                "bug_reports": [b.to_dict() for b in bugs],
                "raw_log_length": len(raw_log),
            },
        )

    # ── Legacy classification (backward-compat) ─────────────────────

    def _classify_legacy(self, test_results: list[dict]) -> AgentResult:
        classified = []
        for failure in test_results:
            category = self._classify(failure)
            classified.append({
                "test": failure.get("test_name", "unknown"),
                "category": category,
                "message": failure.get("message", ""),
                "severity": self._severity(category),
            })

        return AgentResult(
            agent_name=self.name,
            status="success",
            summary=f"Classified {len(classified)} bug(s).",
            details={"bugs": classified},
        )

    def _classify(self, failure: dict) -> str:
        """Rule-based classification – legacy fallback."""
        msg = (failure.get("message", "") + failure.get("traceback", "")).lower()

        rules = {
            "syntax_error": ["syntaxerror", "unexpected token"],
            "type_error": ["typeerror", "type mismatch"],
            "null_reference": ["nonetype", "null", "undefined is not"],
            "index_out_of_bounds": ["indexerror", "out of range", "out of bounds"],
            "import_error": ["importerror", "modulenotfounderror", "cannot find module"],
            "assertion_failure": ["assertionerror", "assert", "expected"],
            "timeout": ["timeout", "timed out"],
            "dependency_issue": ["version conflict", "dependency"],
            "configuration_error": ["config", "environment variable"],
            "concurrency_bug": ["deadlock", "race condition"],
        }

        for category, keywords in rules.items():
            if any(kw in msg for kw in keywords):
                return category
        return "unknown"

    def _severity(self, category: str) -> str:
        high = {"syntax_error", "null_reference", "import_error"}
        medium = {"type_error", "index_out_of_bounds", "assertion_failure", "dependency_issue"}
        if category in high:
            return "high"
        if category in medium:
            return "medium"
        return "low"
