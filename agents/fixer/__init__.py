"""Code Fixer Agent — Agent 3 in the heal loop.

Applies minimal, deterministic fixes to source files based on classified bugs.

Safety rules:
  • Modify minimal lines (smallest contiguous edit per bug)
  • Preserve original style (indentation, quotes, line endings)
  • Never remove or modify test files
  • Deterministic fixes first; LLM with temperature=0 only for remainder
"""

from __future__ import annotations

import json
import logging
import os
import re
import textwrap
from pathlib import Path
from typing import Any

from agents.base import AgentResult, BaseAgent
from shared.determinism import LLM_DETERMINISTIC_PARAMS

logger = logging.getLogger(__name__)

# Patterns that identify test files — patches targeting these are REJECTED
_TEST_FILE_PATTERNS = [
    re.compile(r"(^|/)test_[^/]+\.py$"),
    re.compile(r"(^|/)[^/]+_test\.py$"),
    re.compile(r"(^|/)[^/]+\.(test|spec)\.(js|ts|jsx|tsx|mjs|cjs)$"),
    re.compile(r"(^|/)__tests__/"),
    re.compile(r"(^|/)tests?/test_"),
    re.compile(r"(^|/)conftest\.py$"),
]

# Maximum changed lines per single bug fix
MAX_CHANGED_LINES = 20


def _is_test_file(filepath: str) -> bool:
    """Return True if *filepath* looks like a test file."""
    return any(p.search(filepath) for p in _TEST_FILE_PATTERNS)


class CodeFixerAgent(BaseAgent):
    """Generates and applies minimal code fixes for classified bugs."""

    name = "fixer"

    async def run(self, context: dict[str, Any]) -> AgentResult:
        bugs: list[dict[str, Any]] = context.get("classified_bugs", [])
        repo_path = Path(context.get("repo_path", "."))

        if not bugs:
            return AgentResult(
                agent_name=self.name,
                status="skipped",
                summary="No bugs to fix.",
                details={"fixes": [], "applied_count": 0},
            )

        fixes: list[dict[str, Any]] = []
        applied = 0
        skipped = 0

        for bug in bugs:
            fix = await self._fix_one(bug, repo_path, context)
            fixes.append(fix)
            if fix["status"] == "applied":
                applied += 1
            elif fix["status"] == "skipped_test_file":
                skipped += 1

        return AgentResult(
            agent_name=self.name,
            status="success" if applied > 0 else "failure",
            summary=f"Applied {applied}/{len(bugs)} fix(es), skipped {skipped} (test file protection).",
            details={"fixes": fixes, "applied_count": applied},
        )

    # ── Single-bug fixer ─────────────────────────────────────────────

    async def _fix_one(
        self, bug: dict[str, Any], repo_path: Path, context: dict[str, Any]
    ) -> dict[str, Any]:
        """Attempt to fix a single bug. Returns a fix record."""

        filepath = bug.get("file", "unknown")
        bug_type = bug.get("bug_type", "")
        line_no = bug.get("line", 0)
        message = bug.get("message", "")

        # ── Safety: never touch test files ───────────────────────────
        if _is_test_file(filepath):
            logger.info("Skipping fix for test file: %s", filepath)
            return {
                "bug": bug,
                "status": "skipped_test_file",
                "reason": "Test file protection: will not modify test files.",
                "patch": None,
            }

        # Resolve the file on disk
        abs_path = repo_path / filepath
        if not abs_path.is_file():
            # Try relative to repo root
            candidates = sorted(repo_path.rglob(Path(filepath).name))
            if candidates:
                abs_path = candidates[0]
            else:
                return {
                    "bug": bug,
                    "status": "file_not_found",
                    "reason": f"Source file not found: {filepath}",
                    "patch": None,
                }

        try:
            original = abs_path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            return {
                "bug": bug,
                "status": "read_error",
                "reason": str(exc),
                "patch": None,
            }

        lines = original.splitlines(keepends=True)

        # ── Phase 1: deterministic regex-based fixes ─────────────────
        fixed_lines, patch_desc = self._deterministic_fix(
            lines, bug_type, line_no, message
        )

        if fixed_lines is not None:
            return self._apply_patch(
                abs_path, original, lines, fixed_lines, bug, patch_desc, "deterministic"
            )

        # ── Phase 2: LLM-powered fix ────────────────────────────────
        fixed_lines, patch_desc = await self._llm_fix(
            lines, bug, abs_path, context
        )

        if fixed_lines is not None:
            return self._apply_patch(
                abs_path, original, lines, fixed_lines, bug, patch_desc, "llm"
            )

        return {
            "bug": bug,
            "status": "no_fix_found",
            "reason": "Neither deterministic nor LLM fix could be generated.",
            "patch": None,
        }

    # ── Deterministic fixes ──────────────────────────────────────────

    def _deterministic_fix(
        self,
        lines: list[str],
        bug_type: str,
        line_no: int,
        message: str,
    ) -> tuple[list[str] | None, str]:
        """Try rule-based fixes.  Returns (fixed_lines, description) or (None, '')."""

        if line_no < 1 or line_no > len(lines):
            return None, ""

        idx = line_no - 1  # 0-based
        original_line = lines[idx]

        # ── INDENTATION fixes ────────────────────────────────────────
        if bug_type == "INDENTATION":
            fixed = self._fix_indentation(lines, idx)
            if fixed is not None:
                return fixed, f"Fixed indentation at line {line_no}"

        # ── SYNTAX: missing colon ────────────────────────────────────
        if bug_type == "SYNTAX" and "expected ':'" in message.lower():
            stripped = original_line.rstrip()
            if not stripped.endswith(":"):
                # Add colon at end of line (for def/class/if/for/while/etc.)
                new_lines = lines.copy()
                new_lines[idx] = stripped + ":\n"
                return new_lines, f"Added missing colon at line {line_no}"

        # ── SYNTAX: missing closing bracket ──────────────────────────
        if bug_type == "SYNTAX" and ("unexpected EOF" in message or "expected" in message.lower()):
            stripped = original_line.rstrip()
            open_parens = stripped.count("(") - stripped.count(")")
            open_brackets = stripped.count("[") - stripped.count("]")
            open_braces = stripped.count("{") - stripped.count("}")

            suffix = ")" * open_parens + "]" * open_brackets + "}" * open_braces
            if suffix:
                new_lines = lines.copy()
                new_lines[idx] = stripped + suffix + "\n"
                return new_lines, f"Added missing bracket(s) '{suffix}' at line {line_no}"

        # ── IMPORT: typo in module name ──────────────────────────────
        if bug_type == "IMPORT":
            # Common typo corrections
            typo_map = {
                "dateutil": "python-dateutil",
                "yaml": "pyyaml",
                "cv2": "opencv-python",
                "PIL": "Pillow",
                "sklearn": "scikit-learn",
                "bs4": "beautifulsoup4",
            }
            for mod, pkg in typo_map.items():
                if mod in message:
                    return None, ""  # Can't fix in code — needs pip install

        return None, ""

    def _fix_indentation(
        self, lines: list[str], idx: int
    ) -> list[str] | None:
        """Fix indentation of lines[idx] to match the surrounding block."""
        if idx == 0:
            return None

        # Find the previous non-empty line
        prev_idx = idx - 1
        while prev_idx >= 0 and not lines[prev_idx].strip():
            prev_idx -= 1

        if prev_idx < 0:
            return None

        prev_line = lines[prev_idx]
        prev_indent = len(prev_line) - len(prev_line.lstrip())

        # If previous line ends with ':', expect one level deeper
        prev_stripped = prev_line.rstrip()
        if prev_stripped.endswith(":"):
            # Detect indent unit from file
            indent_unit = self._detect_indent_unit(lines)
            expected_indent = prev_indent + indent_unit
        else:
            expected_indent = prev_indent

        current_line = lines[idx]
        current_indent = len(current_line) - len(current_line.lstrip())

        if current_indent == expected_indent:
            return None  # Already correct

        new_lines = lines.copy()
        new_lines[idx] = " " * expected_indent + current_line.lstrip()
        return new_lines

    @staticmethod
    def _detect_indent_unit(lines: list[str]) -> int:
        """Detect the most common indent step in the file (2 or 4 spaces)."""
        indent_counts: dict[int, int] = {}
        for line in lines:
            stripped = line.lstrip()
            if stripped and not stripped.startswith("#"):
                indent = len(line) - len(stripped)
                if indent > 0:
                    indent_counts[indent] = indent_counts.get(indent, 0) + 1

        if not indent_counts:
            return 4  # default

        # Find GCD-like smallest common indent
        indents = sorted(indent_counts.keys())
        diffs = [indents[i + 1] - indents[i] for i in range(len(indents) - 1) if indents[i + 1] - indents[i] > 0]
        if diffs:
            from collections import Counter
            most_common = Counter(diffs).most_common(1)[0][0]
            return most_common

        return min(indents) if indents else 4

    # ── LLM-powered fix ──────────────────────────────────────────────

    async def _llm_fix(
        self,
        lines: list[str],
        bug: dict[str, Any],
        abs_path: Path,
        context: dict[str, Any],
    ) -> tuple[list[str] | None, str]:
        """Call OpenAI to generate a minimal fix."""

        api_key = os.environ.get("GEMINI_API_KEY", "")
        if not api_key:
            logger.debug("No GEMINI_API_KEY — LLM fix unavailable")
            return None, ""

        line_no = bug.get("line", 0)
        # Extract a window of ±15 lines around the error
        start = max(0, line_no - 16)
        end = min(len(lines), line_no + 15)
        snippet_lines = lines[start:end]
        snippet = "".join(
            f"{start + i + 1:4d} | {ln}" for i, ln in enumerate(snippet_lines)
        )

        prompt = textwrap.dedent(f"""\
        Fix the following bug. Return ONLY the corrected lines — no explanation,
        no markdown fences, no line numbers.  Preserve the original style exactly
        (indentation, quotes, variable names).  Change the MINIMUM lines possible.

        File: {abs_path.name}
        Bug type: {bug.get('bug_type', 'unknown')}
        Message: {bug.get('message', '')}
        Fix hint: {bug.get('fix_hint', '')}

        Code around line {line_no}:
        ```
        {snippet}
        ```

        Return the full corrected snippet (lines {start + 1} to {end}) with NO
        line numbers, just the raw source code.
        """)

        model = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")

        try:
            import httpx

            base_url = os.environ.get("GEMINI_API_BASE", "https://generativelanguage.googleapis.com/v1beta/openai")

            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    f"{base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model,
                        **LLM_DETERMINISTIC_PARAMS,
                        "messages": [
                            {
                                "role": "system",
                                "content": (
                                    "You are a precise code fixer. Return only corrected "
                                    "source lines. No markdown. No explanations. Minimal changes."
                                ),
                            },
                            {"role": "user", "content": prompt},
                        ],
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                content = data["choices"][0]["message"]["content"].strip()

                # Strip markdown fences if model wraps anyway
                if content.startswith("```"):
                    content = re.sub(r"^```\w*\n?", "", content)
                    content = re.sub(r"\n?```$", "", content)

                fixed_snippet_lines = content.splitlines(keepends=True)
                # Ensure last line has newline
                if fixed_snippet_lines and not fixed_snippet_lines[-1].endswith("\n"):
                    fixed_snippet_lines[-1] += "\n"

                # Guard: reject if too many lines changed
                changed_count = sum(
                    1
                    for a, b in zip(snippet_lines, fixed_snippet_lines)
                    if a != b
                )
                extra = abs(len(fixed_snippet_lines) - len(snippet_lines))
                if changed_count + extra > MAX_CHANGED_LINES:
                    logger.warning(
                        "LLM fix changed %d lines (limit %d) — rejecting",
                        changed_count + extra,
                        MAX_CHANGED_LINES,
                    )
                    return None, ""

                # Apply snippet back into full file
                new_lines = lines[:start] + fixed_snippet_lines + lines[end:]
                return new_lines, f"LLM fix applied around line {line_no} ({changed_count} line(s) changed)"

        except Exception as exc:
            logger.warning("LLM fix failed: %s", exc)
            return None, ""

    # ── Patch application ────────────────────────────────────────────

    def _apply_patch(
        self,
        abs_path: Path,
        original: str,
        old_lines: list[str],
        new_lines: list[str],
        bug: dict[str, Any],
        description: str,
        method: str,
    ) -> dict[str, Any]:
        """Write fixed content to disk and return a fix record."""

        # Compute a simple unified-diff-like patch for the record
        changed_ranges: list[str] = []
        for i, (old, new) in enumerate(zip(old_lines, new_lines)):
            if old != new:
                changed_ranges.append(f"L{i + 1}: -{old.rstrip()} → +{new.rstrip()}")

        new_content = "".join(new_lines)

        # Write fix to disk
        try:
            abs_path.write_text(new_content, encoding="utf-8")
            logger.info("Patch applied: %s (%s)", abs_path, description)
        except Exception as exc:
            return {
                "bug": bug,
                "status": "write_error",
                "reason": str(exc),
                "patch": None,
            }

        return {
            "bug": bug,
            "status": "applied",
            "method": method,
            "description": description,
            "file": str(abs_path),
            "patch": changed_ranges[:MAX_CHANGED_LINES],
        }
