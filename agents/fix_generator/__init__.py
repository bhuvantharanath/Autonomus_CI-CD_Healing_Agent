"""Fix Generator Agent – proposes and generates code fixes for classified bugs."""

from __future__ import annotations

from typing import Any

from agents.base import AgentResult, BaseAgent


class FixGeneratorAgent(BaseAgent):
    """Generates fix suggestions and patches for classified bugs."""

    name = "fix_generator"

    async def run(self, context: dict[str, Any]) -> AgentResult:
        bugs = context.get("bugs", [])

        if not bugs:
            return AgentResult(
                agent_name=self.name,
                status="skipped",
                summary="No bugs to fix.",
            )

        fixes: list[dict[str, Any]] = []
        for bug in bugs:
            fix = await self._generate_fix(bug, context)
            fixes.append(fix)

        applied = [f for f in fixes if f["status"] == "generated"]
        return AgentResult(
            agent_name=self.name,
            status="success",
            summary=f"Generated {len(applied)}/{len(bugs)} fix(es).",
            details={"fixes": fixes},
        )

    async def _generate_fix(self, bug: dict, context: dict) -> dict[str, Any]:
        """Generate a fix for a single bug.

        In production this calls an LLM with the bug context, source code,
        and stack trace to produce a unified diff patch.
        """
        category = bug.get("category", "unknown")

        # Placeholder: template-based fixes for common categories
        templates: dict[str, str] = {
            "import_error": "Ensure the module is installed: `pip install <module>`",
            "null_reference": "Add a null/None guard before accessing the attribute.",
            "type_error": "Verify argument types match the function signature.",
            "syntax_error": "Check the reported line for missing brackets or keywords.",
        }

        suggestion = templates.get(category, "Manual investigation required.")

        return {
            "bug": bug,
            "status": "generated" if category in templates else "needs_review",
            "suggestion": suggestion,
            "patch": None,  # Unified diff goes here after LLM generation
        }
