"""FixPlannerAgent tool — decides minimal patch strategy per bug.

Tool 3 in the reasoning loop. Reads classified bugs, inspects source
files, and produces a fix plan WITHOUT applying any changes. This
separates planning from execution so the reasoning loop can inspect
the plan before committing patches.
"""

from __future__ import annotations

import logging
import os
import re
import textwrap
from pathlib import Path
from typing import Any

from agents.tools.registry import AgentTool, ToolResult

logger = logging.getLogger(__name__)

# Test-file patterns — we plan around these, never targeting them
_TEST_FILE_PATTERNS = [
    re.compile(r"(^|/)test_[^/]+\.py$"),
    re.compile(r"(^|/)[^/]+_test\.py$"),
    re.compile(r"(^|/)[^/]+\.(test|spec)\.(js|ts|jsx|tsx|mjs|cjs)$"),
    re.compile(r"(^|/)__tests__/"),
    re.compile(r"(^|/)tests?/test_"),
    re.compile(r"(^|/)conftest\.py$"),
]


def _is_test_file(filepath: str) -> bool:
    return any(p.search(filepath) for p in _TEST_FILE_PATTERNS)


class FixPlannerTool(AgentTool):
    """Plans minimal patches for each classified bug without applying them."""

    name = "fix_planner"
    description = (
        "Reads classified bugs and source files, decides the fix strategy "
        "(deterministic rule or LLM), reads source context around the error, "
        "and produces a detailed fix plan. Does NOT apply any patches."
    )
    input_keys = ["classified_bugs", "repo_path"]
    output_keys = ["fix_plan"]

    async def execute(self, state: dict[str, Any]) -> ToolResult:
        bugs: list[dict[str, Any]] = state.get("classified_bugs", [])
        repo_path = Path(state.get("repo_path", "."))

        # ── Retrieve memory of previously-failed fixes ───────────────
        memory = state.get("_run_memory")
        prev_fix_keys: set[tuple[str, str]] = set()   # (file, bug_type)
        if memory is not None:
            for fix in memory.fixes:
                prev_fix_keys.add((fix.file, ""))  # track per-file
        # Also check applied_patches from this run so far
        for patch in state.get("applied_patches", []):
            bug_info = patch.get("bug", {})
            prev_fix_keys.add(
                (bug_info.get("file", ""), bug_info.get("bug_type", ""))
            )

        logger.info(
            "[FixPlanner] === EXECUTE START === | bugs=%d | repo_path=%s | exists=%s"
            " | prev_fix_keys=%d",
            len(bugs), repo_path, repo_path.exists(), len(prev_fix_keys),
        )

        if repo_path.is_dir():
            top = sorted(os.listdir(repo_path))[:20]
            logger.info("[FixPlanner] Top-level entries: %s", top)

        if not bugs:
            return ToolResult(
                tool_name=self.name,
                status="skipped",
                summary="No bugs to plan fixes for.",
                outputs={"fix_plan": []},
            )

        plan: list[dict[str, Any]] = []
        plannable = 0
        skipped = 0
        seen_locations = set()

        for bug in bugs:
            filepath = bug.get("file", "unknown")
            line_no = bug.get("line", 0)
            loc = (filepath, line_no)
            if loc in seen_locations:
                logger.info("[FixPlanner] Skipping duplicate bug at %s:%s", filepath, line_no)
                continue
            seen_locations.add(loc)

            # If a deterministic fix was already tried for this
            # (file, bug_type) in a prior iteration, escalate to LLM
            # so we don't repeat the same failed approach.
            bug_type = bug.get("bug_type", "")
            already_tried = (filepath, bug_type) in prev_fix_keys or \
                            (filepath, "") in prev_fix_keys
            if already_tried:
                logger.info(
                    "[FixPlanner] Prior fix exists for %s/%s — escalating to LLM",
                    filepath, bug_type,
                )
                bug = {**bug, "_escalate_to_llm": True}

            entry = self._plan_one(bug, repo_path)

            # Force LLM strategy when escalating
            if already_tried and entry.get("strategy", "").startswith("deterministic"):
                entry["strategy"] = "llm"
                entry["description"] = (
                    f"Escalated to LLM: deterministic fix already failed for "
                    f"{bug_type} in {filepath}"
                )

            plan.append(entry)
            logger.info(
                "[FixPlanner] Bug '%s' in %s:%s → strategy=%s",
                bug.get("bug_type"), bug.get("file"), bug.get("line"),
                entry.get("strategy"),
            )
            if entry["strategy"] == "skip_test_file":
                skipped += 1
            elif entry["strategy"] != "unresolvable":
                plannable += 1

        return ToolResult(
            tool_name=self.name,
            status="success" if plannable > 0 else "failure",
            summary=(
                f"Planned {plannable}/{len(bugs)} fix(es), "
                f"skipped {skipped} (test file protection)."
            ),
            outputs={"fix_plan": plan},
        )

    # ── Per-bug planning ─────────────────────────────────────────────

    def _plan_one(
        self, bug: dict[str, Any], repo_path: Path
    ) -> dict[str, Any]:
        """Decide fix strategy for a single bug."""

        filepath = bug.get("file", "unknown")
        bug_type = bug.get("bug_type", "")
        line_no = bug.get("line", 0)
        message = bug.get("message", "")

        # ── Guard: test files ────────────────────────────────────────
        if _is_test_file(filepath):
            logger.info(
                "[FixPlanner] Skipping test file: %s", filepath
            )
            return {
                "bug": bug,
                "strategy": "skip_test_file",
                "reason": "Will not modify test files.",
                "target_file": filepath,
                "source_context": "",
            }

        # ── Resolve file path ────────────────────────────────────────
        abs_path = repo_path / filepath
        logger.info(
            "[FixPlanner] Resolving file: filepath=%s | abs_path=%s | exists=%s",
            filepath, abs_path, abs_path.is_file(),
        )
        if not abs_path.is_file():
            candidates = sorted(repo_path.rglob(Path(filepath).name))
            logger.info(
                "[FixPlanner] File not at expected path, searching rglob: "
                "found %d candidate(s): %s",
                len(candidates),
                [str(c.relative_to(repo_path)) for c in candidates[:5]],
            )
            if candidates:
                abs_path = candidates[0]
                logger.info("[FixPlanner] Using candidate: %s", abs_path)
            else:
                logger.warning(
                    "[FixPlanner] File NOT FOUND anywhere: %s", filepath
                )
                return {
                    "bug": bug,
                    "strategy": "unresolvable",
                    "reason": f"File not found: {filepath}",
                    "target_file": filepath,
                    "source_context": "",
                }

        # ── Read source context ──────────────────────────────────────
        try:
            content = abs_path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            return {
                "bug": bug,
                "strategy": "unresolvable",
                "reason": f"Cannot read file: {exc}",
                "target_file": str(abs_path),
                "source_context": "",
            }

        lines = content.splitlines(keepends=True)

        # Window of ±15 lines around error
        start = max(0, line_no - 16)
        end = min(len(lines), line_no + 15)
        snippet = "".join(
            f"{start + i + 1:4d} | {ln}" for i, ln in enumerate(lines[start:end])
        )

        # ── Choose strategy ──────────────────────────────────────────
        strategy, description = self._choose_strategy(
            bug_type, line_no, message, lines
        )

        return {
            "bug": bug,
            "strategy": strategy,
            "description": description,
            "target_file": str(abs_path),
            "source_context": snippet,
            "line_range": {"start": start + 1, "end": end},
        }

    def _choose_strategy(
        self,
        bug_type: str,
        line_no: int,
        message: str,
        lines: list[str],
    ) -> tuple[str, str]:
        """Return (strategy_name, description)."""

        if line_no < 1 or line_no > len(lines):
            return "llm", "Line out of range; will use LLM to infer fix."


        original_line = lines[line_no - 1]

        # ── Deterministic: indentation ───────────────────────────────
        if bug_type == "INDENTATION":
            return "deterministic_indent", (
                f"Fix indentation at line {line_no} to match surrounding block."
            )

        # ── Deterministic: missing colon ─────────────────────────────
        if bug_type == "SYNTAX" and "expected ':'" in message.lower():
            stripped = original_line.rstrip()
            if not stripped.endswith(":"):
                return "deterministic_colon", (
                    f"Add missing colon at end of line {line_no}."
                )

        # ── Deterministic: missing semicolon (Java) ──────────────────
        if bug_type == "SYNTAX" and "missing semicolon" in message.lower():
            return "deterministic_semicolon", (
                f"Add missing semicolon at end of line {line_no}."
            )

        # ── Deterministic: missing bracket ───────────────────────────
        if bug_type == "SYNTAX" and (
            "unexpected EOF" in message or "expected" in message.lower()
        ):
            stripped = original_line.rstrip()
            open_p = stripped.count("(") - stripped.count(")")
            open_b = stripped.count("[") - stripped.count("]")
            open_c = stripped.count("{") - stripped.count("}")
            if open_p > 0 or open_b > 0 or open_c > 0:
                suffix = ")" * open_p + "]" * open_b + "}" * open_c
                return "deterministic_bracket", (
                    f"Add missing bracket(s) '{suffix}' at line {line_no}."
                )

        # ── Deterministic: unused import ─────────────────────────────
        if bug_type == "LINTING" and (
            "imported but unused" in message.lower()
            or "f401" in message.lower()
            or "unused import" in message.lower()
        ):
            return "deterministic_unused_import", (
                f"Remove unused import at line {line_no}: {message}"
            )

        # ── Deterministic: type error (method on wrong type) ─────────
        if bug_type == "TYPE_ERROR" and "is not a function" in message.lower():
            return "deterministic_type_error", (
                f"Fix type error at line {line_no}: {message}"
            )

        # ── Deterministic: logic error (wrong operator) ──────────────
        if bug_type == "LOGIC" and (
            "should multiply" in message.lower()
            or "should divide" in message.lower()
            or "should add" in message.lower()
            or "should subtract" in message.lower()
        ):
            return "deterministic_logic", (
                f"Fix logic error at line {line_no}: {message}"
            )

        # ── Deterministic: ZeroDivisionError ─────────────────────────
        if "zerodivisionerror" in message.lower() or (
            bug_type == "LOGIC" and "/" in original_line
        ):
            return "deterministic_zero_division", (
                f"Add zero-division guard at line {line_no}: {message}"
            )

        # ── Deterministic: IndexError ────────────────────────────────
        if "indexerror" in message.lower() or (
            bug_type == "LOGIC" and ("[" in original_line or "split" in original_line)
        ):
            return "deterministic_index_error", (
                f"Add bounds check at line {line_no}: {message}"
            )

        # ── Deterministic: RecursionError ────────────────────────────
        if "recursion" in message.lower():
            return "deterministic_recursion_error", (
                f"Add recursion base-case guard at line {line_no}: {message}"
            )

        # ── Fallback: LLM ────────────────────────────────────────────
        return "llm", (
            f"Use LLM (temperature=0) to generate minimal fix for "
            f"{bug_type} at line {line_no}: {message}"
        )

