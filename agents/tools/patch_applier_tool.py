"""PatchApplierAgent tool — edits code safely, never modifies tests.

Tool 4 in the reasoning loop.  Takes a fix_plan produced by the
FixPlannerTool and actually applies the patches to disk.  Contains
all safety guards:
  • Never modify test files
  • Cap changed lines at 20 per bug
  • Preserve original style
  • Deterministic patches applied first; LLM only as fallback
"""

from __future__ import annotations

import logging
import os
import re
import textwrap
from collections import Counter
from pathlib import Path
from typing import Any

from agents.tools.registry import AgentTool, ToolResult
from shared.determinism import LLM_DETERMINISTIC_PARAMS

logger = logging.getLogger(__name__)

MAX_CHANGED_LINES = 20

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


class PatchApplierTool(AgentTool):
    """Applies planned patches to source files with safety guards."""

    name = "patch_applier"
    description = (
        "Takes a fix plan and applies each patch to disk. Enforces safety: "
        "never modifies test files, caps each patch at 20 changed lines, "
        "and preserves original code style."
    )
    input_keys = ["fix_plan", "repo_path"]
    output_keys = ["applied_patches", "skipped_patches", "applied_count"]

    async def execute(self, state: dict[str, Any]) -> ToolResult:
        plan: list[dict[str, Any]] = state.get("fix_plan", [])
        repo_path = Path(state.get("repo_path", "."))

        logger.info(
            "[PatchApplier] === EXECUTE START === | plan_entries=%d | "
            "repo_path=%s | exists=%s",
            len(plan), repo_path, repo_path.exists(),
        )
        for i, entry in enumerate(plan):
            logger.info(
                "[PatchApplier] Plan[%d]: strategy=%s | target=%s | bug_type=%s",
                i, entry.get("strategy"), entry.get("target_file"),
                entry.get("bug", {}).get("bug_type"),
            )

        if not plan:
            return ToolResult(
                tool_name=self.name,
                status="skipped",
                summary="No fix plan to apply.",
                outputs={
                    "applied_patches": [],
                    "skipped_patches": [],
                    "applied_count": 0,
                },
            )

        applied: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []

        # ── Group fixes by file and sort BOTTOM-UP within each file ──
        # This prevents line-number drift when multiple fixes target the
        # same file: applying higher line numbers first means earlier
        # line numbers remain stable.
        from collections import defaultdict
        by_file: dict[str, list[dict[str, Any]]] = defaultdict(list)
        non_actionable: list[dict[str, Any]] = []
        for entry in plan:
            strategy = entry.get("strategy", "")
            if strategy in ("skip_test_file", "unresolvable"):
                non_actionable.append(entry)
                continue
            target = entry.get("target_file", entry.get("bug", {}).get("file", ""))
            by_file[target].append(entry)

        # Skip non-actionable entries
        for entry in non_actionable:
            skipped.append({
                "bug": entry.get("bug"),
                "reason": entry.get("reason", entry.get("strategy", "")),
                "status": f"skipped_{entry.get('strategy', '')}",
            })

        # Process each file group: sort by line DESC (bottom-up)
        ordered_entries: list[dict[str, Any]] = []
        for target_file in sorted(by_file.keys()):
            entries = by_file[target_file]
            entries.sort(
                key=lambda e: e.get("bug", {}).get("line", 0),
                reverse=True,  # highest line first → no drift
            )
            ordered_entries.extend(entries)

        for entry in ordered_entries:
            result = await self._apply_one(entry, repo_path, state)
            if result["status"] == "applied":
                applied.append(result)
            else:
                skipped.append(result)

        total = len(applied)
        return ToolResult(
            tool_name=self.name,
            status="success" if total > 0 else "failure",
            summary=f"Applied {total}/{len(plan)} patch(es), skipped {len(skipped)}.",
            outputs={
                "applied_patches": applied,
                "skipped_patches": skipped,
                "applied_count": total,
            },
        )

    # ── Single-patch application ─────────────────────────────────────

    async def _apply_one(
        self,
        entry: dict[str, Any],
        repo_path: Path,
        state: dict[str, Any],
    ) -> dict[str, Any]:
        """Apply a single planned fix to disk."""

        target_file = entry.get("target_file", "")
        strategy = entry.get("strategy", "")
        bug = entry.get("bug", {})

        logger.info(
            "[PatchApplier] Applying fix: strategy=%s, file=%s, bug_type=%s, line=%s",
            strategy, target_file, bug.get("bug_type"), bug.get("line"),
        )

        # ── Double-check: test file guard ────────────────────────────
        if _is_test_file(target_file):
            return {
                "bug": bug,
                "status": "skipped_test_file",
                "reason": "Test file protection (double check).",
                "patch": None,
            }

        abs_path = Path(target_file)
        if not abs_path.is_absolute():
            abs_path = repo_path / target_file

        if not abs_path.is_file():
            return {
                "bug": bug,
                "status": "file_not_found",
                "reason": f"Target file not found: {target_file}",
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

        # ── Deterministic patches ────────────────────────────────────
        if strategy.startswith("deterministic"):
            fixed_lines, desc = self._deterministic_patch(
                strategy, lines, bug
            )
            if fixed_lines is not None:
                logger.info("[PatchApplier] Deterministic fix succeeded: %s", desc)
                return self._write_patch(abs_path, original, lines, fixed_lines, bug, desc, "deterministic", repo_path)
            logger.info("[PatchApplier] Deterministic fix returned None for strategy=%s — falling back to LLM", strategy)

        # ── LLM patch ───────────────────────────────────────────────
        if strategy == "llm" or strategy.startswith("deterministic"):
            logger.info("[PatchApplier] Attempting LLM patch for %s", target_file)
            fixed_lines, desc = await self._llm_patch(
                lines, bug, abs_path, entry.get("source_context", "")
            )
            if fixed_lines is not None:
                logger.info("[PatchApplier] LLM fix succeeded: %s", desc)
                return self._write_patch(abs_path, original, lines, fixed_lines, bug, desc, "llm", repo_path)
            logger.warning("[PatchApplier] LLM fix also failed for %s", target_file)

        return {
            "bug": bug,
            "status": "no_fix_generated",
            "reason": f"Could not generate a fix via strategy '{strategy}'.",
            "patch": None,
        }

    # ── Deterministic patch strategies ───────────────────────────────

    def _deterministic_patch(
        self,
        strategy: str,
        lines: list[str],
        bug: dict[str, Any],
    ) -> tuple[list[str] | None, str]:
        """Apply a deterministic patch. Returns (new_lines, description)."""

        line_no = bug.get("line", 0)
        if line_no < 1 or line_no > len(lines):
            return None, ""

        idx = line_no - 1
        original_line = lines[idx]

        if strategy == "deterministic_indent":
            fixed = self._fix_indentation(lines, idx)
            if fixed is not None:
                return fixed, f"Fixed indentation at line {line_no}"

        elif strategy == "deterministic_colon":
            stripped = original_line.rstrip()
            if not stripped.endswith(":"):
                new_lines = lines.copy()
                new_lines[idx] = stripped + ":\n"
                return new_lines, f"Added missing colon at line {line_no}"

        elif strategy == "deterministic_bracket":
            stripped = original_line.rstrip()
            open_p = stripped.count("(") - stripped.count(")")
            open_b = stripped.count("[") - stripped.count("]")
            open_c = stripped.count("{") - stripped.count("}")
            suffix = ")" * max(open_p, 0) + "]" * max(open_b, 0) + "}" * max(open_c, 0)
            if suffix:
                new_lines = lines.copy()
                new_lines[idx] = stripped + suffix + "\n"
                return new_lines, f"Added missing bracket(s) '{suffix}' at line {line_no}"

        elif strategy == "deterministic_semicolon":
            stripped = original_line.rstrip()
            if not stripped.endswith(";"):
                new_lines = lines.copy()
                new_lines[idx] = stripped + ";\n"
                return new_lines, f"Added missing semicolon at line {line_no}"

        elif strategy == "deterministic_unused_import":
            new_lines, desc = self._remove_unused_import(lines, idx, bug)
            if new_lines is not None:
                return new_lines, desc

        elif strategy == "deterministic_type_error":
            new_lines, desc = self._fix_type_error(lines, idx, bug)
            if new_lines is not None:
                return new_lines, desc

        elif strategy == "deterministic_logic":
            new_lines, desc = self._fix_logic_error(lines, idx, bug)
            if new_lines is not None:
                return new_lines, desc

        elif strategy == "deterministic_zero_division":
            new_lines, desc = self._fix_zero_division(lines, idx, bug)
            if new_lines is not None:
                return new_lines, desc

        elif strategy == "deterministic_index_error":
            new_lines, desc = self._fix_index_error(lines, idx, bug)
            if new_lines is not None:
                return new_lines, desc

        elif strategy == "deterministic_recursion_error":
            new_lines, desc = self._fix_recursion_error(lines, idx, bug)
            if new_lines is not None:
                return new_lines, desc

        return None, ""

    # ── Unused import removal ────────────────────────────────────────

    def _remove_unused_import(
        self,
        lines: list[str],
        idx: int,
        bug: dict[str, Any],
    ) -> tuple[list[str] | None, str]:
        """Remove an unused import from the source.

        Handles:
          - ``import foo``          → delete the line
          - ``import foo, bar``     → remove just the unused name
          - ``from x import foo``   → delete the line
          - ``from x import a, foo, b`` → remove just `foo`
          - Java ``import pkg.Class;``  → delete the line
        """
        message = bug.get("message", "")
        line_no = idx + 1
        original = lines[idx]
        stripped = original.strip()

        # Extract the unused import name from the message
        # Patterns: "'os' imported but unused", "F401 'os' imported but unused",
        # "'java.io.File' imported but unused"
        m = re.search(r"['\"]([^'\"]+)['\"].*imported but unused", message, re.IGNORECASE)
        if not m:
            m = re.search(r"unused import:?\s*['\"]?(\S+)['\"]?", message, re.IGNORECASE)
        if not m:
            # Fallback: just delete the entire import line
            new_lines = lines.copy()
            new_lines.pop(idx)
            return new_lines, f"Removed unused import line {line_no}"

        unused_name = m.group(1)

        # Java import:  import pkg.Class;
        if stripped.startswith("import ") and stripped.endswith(";"):
            new_lines = lines.copy()
            new_lines.pop(idx)
            return new_lines, f"Removed unused Java import '{unused_name}' at line {line_no}"

        # Python: from X import a, unused, b
        if stripped.startswith("from ") and "import " in stripped:
            from_match = re.match(
                r"(from\s+\S+\s+import\s+)(.*)", stripped
            )
            if from_match:
                prefix = from_match.group(1)
                names_str = from_match.group(2)
                names = [n.strip() for n in names_str.split(",")]
                # Get the simple name for comparison
                unused_simple = unused_name.rsplit(".", 1)[-1]
                filtered = [n for n in names if n != unused_simple and n != unused_name]
                if not filtered:
                    # All names removed → delete the line
                    new_lines = lines.copy()
                    new_lines.pop(idx)
                    return new_lines, f"Removed unused import line {line_no}"
                indent = len(original) - len(original.lstrip())
                new_lines = lines.copy()
                new_lines[idx] = " " * indent + prefix + ", ".join(filtered) + "\n"
                return new_lines, f"Removed '{unused_name}' from import at line {line_no}"

        # Python: import foo / import foo, bar
        if stripped.startswith("import "):
            names_str = stripped[len("import "):].strip()
            names = [n.strip() for n in names_str.split(",")]
            unused_simple = unused_name.rsplit(".", 1)[-1]
            filtered = [n for n in names if n != unused_simple and n != unused_name]
            if not filtered:
                new_lines = lines.copy()
                new_lines.pop(idx)
                return new_lines, f"Removed unused import '{unused_name}' at line {line_no}"
            indent = len(original) - len(original.lstrip())
            new_lines = lines.copy()
            new_lines[idx] = " " * indent + "import " + ", ".join(filtered) + "\n"
            return new_lines, f"Removed '{unused_name}' from import at line {line_no}"

        # Fallback: delete the line
        new_lines = lines.copy()
        new_lines.pop(idx)
        return new_lines, f"Removed unused import line {line_no}"

    # ── Type error fix (method on wrong type) ────────────────────────

    def _fix_type_error(
        self,
        lines: list[str],
        idx: int,
        bug: dict[str, Any],
    ) -> tuple[list[str] | None, str]:
        """Fix calling string methods on non-string types.

        e.g. ``age.toUpperCase()`` → ``String(age).toUpperCase()`` (JS)
             or ``str(age).upper()`` (Python)
        """
        message = bug.get("message", "")
        original = lines[idx]
        line_no = idx + 1

        # Extract variable name and method from message
        # "TypeError: age.toUpperCase is not a function (age is a number)"
        m = re.search(r"(\w+)\.(\w+)\s+is not a function.*is a number", message)
        if m:
            var_name = m.group(1)
            method = m.group(2)
            # JS fix: wrap in String()
            new_line = original.replace(
                f"{var_name}.{method}(",
                f"String({var_name}).{method}("
            )
            if new_line != original:
                new_lines = lines.copy()
                new_lines[idx] = new_line
                return new_lines, (
                    f"Wrapped '{var_name}' in String() before .{method}() at line {line_no}"
                )

        return None, ""

    # ── Logic error fix (wrong operator) ─────────────────────────────

    def _fix_logic_error(
        self,
        lines: list[str],
        idx: int,
        bug: dict[str, Any],
    ) -> tuple[list[str] | None, str]:
        """Fix wrong arithmetic operator based on error message hint.

        e.g. "should multiply tax, not divide" → replace / with *
        """
        message = bug.get("message", "").lower()
        original = lines[idx]
        line_no = idx + 1

        # Map: "should X, not Y" → replace Y's operator with X's operator
        op_map = {
            "multiply": "*", "divide": "/",
            "add": "+", "subtract": "-",
        }

        should_op = None
        not_op = None

        for op_name, op_char in op_map.items():
            if f"should {op_name}" in message:
                should_op = op_char
            if f"not {op_name}" in message:
                not_op = op_char

        if should_op and not_op and should_op != not_op:
            new_line = original.replace(f" {not_op} ", f" {should_op} ", 1)
            if new_line != original:
                new_lines = lines.copy()
                new_lines[idx] = new_line
                return new_lines, (
                    f"Changed '{not_op}' to '{should_op}' at line {line_no}"
                )

        return None, ""

    # ── ZeroDivisionError fix ────────────────────────────────────────

    def _fix_zero_division(
        self,
        lines: list[str],
        idx: int,
        bug: dict[str, Any],
    ) -> tuple[list[str] | None, str]:
        """Add a zero-division guard before lines that divide.

        Detects the divisor variable/expression and adds an
        ``if divisor == 0: return 0`` guard before the offending line.
        """
        line_no = idx + 1
        original = lines[idx]
        indent_len = len(original) - len(original.lstrip())
        pad = original[:indent_len]

        # Find the division in this line
        stripped = original.strip()

        # Pattern: `result = X / Y`  or  `something[name] = X / Y`
        div_match = re.search(r'(\S+)\s*/\s*(\S+)', stripped)
        if not div_match:
            return None, ""

        divisor = div_match.group(2).rstrip(",;:")

        indent_str = self._detect_indent_string(lines)

        # Determine what to return when divisor is 0
        # If the line is `return X / Y`, the guard should also return
        if stripped.startswith("return "):
            guard = f"{pad}if {divisor} == 0:\n{pad}{indent_str}return 0\n"
        else:
            # For assignment like `result = X / Y`, guard and set to 0
            # Find the assignment target
            assign_match = re.match(r'(\S+(?:\[\S+\])?)\s*=', stripped)
            if assign_match:
                target = assign_match.group(1)
                guard = f"{pad}if {divisor} == 0:\n{pad}{indent_str}{target} = 0\n{pad}{indent_str}continue\n"
                # Check if we're inside a loop (for/while) — if so, use continue
                # Otherwise, just set to 0 and skip the division
                in_loop = False
                for i in range(idx - 1, max(idx - 10, -1), -1):
                    ls = lines[i].strip()
                    if ls.startswith("for ") or ls.startswith("while "):
                        in_loop = True
                        break
                    if ls and not ls.startswith("#"):
                        # Check if dedented (exited a block)
                        l_indent = len(lines[i]) - len(lines[i].lstrip())
                        if l_indent < indent_len:
                            if ls.startswith("for ") or ls.startswith("while "):
                                in_loop = True
                            break
                if not in_loop:
                    guard = f"{pad}if {divisor} == 0:\n{pad}{indent_str}{target} = 0\n"
                    # Need to also skip the division line — wrap in else
                    new_lines = lines.copy()
                    new_lines[idx] = guard + f"{pad}else:\n{pad}{indent_str}{stripped}\n"
                    return new_lines, f"Added zero-division guard at line {line_no}"
            else:
                guard = f"{pad}if {divisor} == 0:\n{pad}{indent_str}pass\n"
                # fallback

        new_lines = lines.copy()
        new_lines.insert(idx, guard)
        return new_lines, f"Added zero-division guard before line {line_no}"

    # ── IndexError fix ───────────────────────────────────────────────

    def _fix_index_error(
        self,
        lines: list[str],
        idx: int,
        bug: dict[str, Any],
    ) -> tuple[list[str] | None, str]:
        """Add a bounds check or try/except for IndexError.

        For list indexing like ``parts[1]``, wraps the containing block
        in a ``if len(parts) > 1:`` guard or try/except.
        """
        line_no = idx + 1
        original = lines[idx]
        indent_len = len(original) - len(original.lstrip())
        pad = original[:indent_len]
        stripped = original.strip()

        # Find pattern like: variable[N] where N is an integer
        idx_match = re.search(r'(\w+)\[(\d+)\]', stripped)
        indent_str = self._detect_indent_string(lines)
        if not idx_match:
            # Try to wrap with try/except
            new_lines = lines.copy()
            # Find the block: from this line, collect consecutive lines at same or greater indent
            block_end = idx + 1
            while block_end < len(new_lines) and new_lines[block_end].strip():
                bi = len(new_lines[block_end]) - len(new_lines[block_end].lstrip())
                if bi < indent_len:
                    break
                block_end += 1

            # Wrap in try/except
            new_lines.insert(idx, f"{pad}try:\n")
            for i in range(idx + 1, block_end + 1):
                new_lines[i] = indent_str + new_lines[i]
            new_lines.insert(block_end + 1, f"{pad}except (IndexError, KeyError):\n{pad}{indent_str}continue\n")
            return new_lines, f"Wrapped line {line_no} in try/except for IndexError"

        var_name = idx_match.group(1)
        index_val = int(idx_match.group(2))

        # Add length check: `if len(var) > index:`
        guard = f"{pad}if len({var_name}) <= {index_val}:\n{pad}{indent_str}continue\n"

        new_lines = lines.copy()
        new_lines.insert(idx, guard)
        return new_lines, f"Added bounds check for {var_name}[{index_val}] at line {line_no}"

    # ── RecursionError fix ───────────────────────────────────────────

    def _fix_recursion_error(
        self,
        lines: list[str],
        idx: int,
        bug: dict[str, Any],
    ) -> tuple[list[str] | None, str]:
        """Add a base-case guard for recursive functions.

        Inserts ``if n < 0: return 1`` (or similar) before the recursive call.
        Detects the function parameter from the recursive call.
        """
        line_no = idx + 1
        original = lines[idx]
        indent_len = len(original) - len(original.lstrip())
        pad = original[:indent_len]
        stripped = original.strip()

        # Look for the function name — scan upward for `def func_name(`
        func_name = None
        param_name = None
        for i in range(idx - 1, max(idx - 20, -1), -1):
            ls = lines[i].strip()
            func_match = re.match(r'def\s+(\w+)\s*\((\w+)', ls)
            if func_match:
                func_name = func_match.group(1)
                param_name = func_match.group(2)
                break

        if not param_name:
            # Fallback: try to extract the argument from the recursive call
            call_match = re.search(r'(\w+)\s*\((\w+)\s*-', stripped)
            if call_match:
                param_name = call_match.group(2)
            else:
                param_name = "n"

        # Add guard: if param < 0: return 1
        # Insert BEFORE the existing base case check or at function body start
        # Find the line right after `def func():` at the function indent level
        func_body_indent = indent_len
        indent_str = self._detect_indent_string(lines)
        guard = f"{pad}if {param_name} < 0:\n{pad}{indent_str}return 1\n"

        # Insert the guard before the current line
        new_lines = lines.copy()
        # Find the best insertion point — right after the existing `if n == 0` check
        insert_at = idx
        for i in range(idx - 1, max(idx - 5, -1), -1):
            ls = lines[i].strip()
            if ls.startswith(f"if {param_name} ==") or ls.startswith(f"if {param_name}=="):
                # Insert after the return line that follows this if
                for j in range(i + 1, min(i + 3, len(lines))):
                    if lines[j].strip().startswith("return"):
                        insert_at = j + 1
                        break
                break

        new_lines.insert(insert_at, guard)
        return new_lines, f"Added negative-input guard for {param_name} at line {line_no}"

    def _fix_indentation(self, lines: list[str], idx: int) -> list[str] | None:
        """Fix indentation of lines[idx] to match surrounding block.

        Also handles the 'expected an indented block' case: when the
        previous non-blank line ends with ':' but the current line is
        at the *same* or *lower* indent (empty block body), we insert
        a ``pass`` statement instead of just re-indenting.
        """
        if idx == 0:
            return None

        prev_idx = idx - 1
        while prev_idx >= 0 and not lines[prev_idx].strip():
            prev_idx -= 1
        if prev_idx < 0:
            return None

        prev_line = lines[prev_idx]
        prev_indent = len(prev_line) - len(prev_line.lstrip())
        prev_stripped = prev_line.rstrip()
        indent_unit = self._detect_indent_unit(lines)

        if prev_stripped.endswith(":"):
            expected_indent = prev_indent + indent_unit
        else:
            expected_indent = prev_indent

        current_line = lines[idx]
        current_stripped = current_line.strip()
        current_indent = len(current_line) - len(current_line.lstrip())

        # ── Empty-block case ─────────────────────────────────────────
        # The *previous* line ends with ':' (if/else/for/def/etc.) but
        # the current line is NOT indented deeper → the block body is
        # missing.  Insert a ``pass`` line rather than re-indenting the
        # current line (which belongs to the outer scope).
        if prev_stripped.endswith(":") and current_indent <= prev_indent:
            new_lines = lines.copy()
            pass_line = " " * expected_indent + "pass\n"
            new_lines.insert(idx, pass_line)
            return new_lines

        if current_indent == expected_indent:
            return None

        new_lines = lines.copy()
        new_lines[idx] = " " * expected_indent + current_line.lstrip()
        return new_lines

    @staticmethod
    def _detect_indent_unit(lines: list[str]) -> int:
        """Detect the most common indent step (2 or 4 spaces)."""
        indent_counts: dict[int, int] = {}
        for line in lines:
            stripped = line.lstrip()
            if stripped and not stripped.startswith("#"):
                indent = len(line) - len(stripped)
                if indent > 0:
                    indent_counts[indent] = indent_counts.get(indent, 0) + 1

        if not indent_counts:
            return 4

        indents = sorted(indent_counts.keys())
        diffs = [
            indents[i + 1] - indents[i]
            for i in range(len(indents) - 1)
            if indents[i + 1] - indents[i] > 0
        ]
        if diffs:
            most_common = Counter(diffs).most_common(1)[0][0]
            return most_common
        return min(indents) if indents else 4

    @staticmethod
    def _detect_indent_string(lines: list[str]) -> str:
        """Detect the indent string step (e.g. 4 spaces or 1 tab)."""
        for line in lines:
            if line.startswith('\t'):
                return '\t'
        return " " * PatchApplierTool._detect_indent_unit(lines)

    # ── LLM-powered patch ────────────────────────────────────────────

    async def _llm_patch(
        self,
        lines: list[str],
        bug: dict[str, Any],
        abs_path: Path,
        source_context: str,
    ) -> tuple[list[str] | None, str]:
        """Generate a minimal fix via OpenAI."""

        api_key = os.environ.get("GEMINI_API_KEY", "")
        if not api_key:
            logger.warning("No GEMINI_API_KEY set — LLM patch unavailable")
            return None, ""

        line_no = bug.get("line", 0)
        start = max(0, line_no - 4)
        end = min(len(lines), line_no + 3)
        snippet_lines = lines[start:end]
        snippet = "".join(
            f"{start + i + 1:4d} | {ln}" for i, ln in enumerate(snippet_lines)
        )

        prompt = textwrap.dedent(f"""\
        Fix the following bug in the code.
        
        File: {abs_path.name}
        Bug type: {bug.get('bug_type', 'unknown')}
        Error message: {bug.get('message', '')}
        Fix hint: {bug.get('fix_hint', '')}

        Original code (lines {start + 1} to {end}):
        {snippet}

        Respond ONLY with a SEARCH and REPLACE block.
        Format strictly as:
        <<<<
        [exact original lines to replace]
        ====
        [new modified lines]
        >>>>
        Do NOT include markdown fences, line numbers, or explanations.
        """)

        model = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")

        try:
            import httpx

            base_url = os.environ.get(
                "GEMINI_API_BASE", "https://generativelanguage.googleapis.com/v1beta/openai"
            )
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
                                    "You are a precise code fixer. Return only SEARCH and REPLACE blocks. "
                                    "No markdown. No explanations."
                                ),
                            },
                            {"role": "user", "content": prompt},
                        ],
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                content = data["choices"][0]["message"]["content"].strip()

                # Basic parsing of <<<< .... ==== .... >>>>
                content = content.replace("```python", "").replace("```", "").strip()
                if "<<<<" not in content or "====" not in content or ">>>>" not in content:
                    logger.warning("LLM patch failed to return valid SEARCH/REPLACE blocks.")
                    return None, ""
                
                parts = content.split("====")
                search_part = parts[0].split("<<<<")[-1].strip("\n")
                replace_part = parts[1].split(">>>>")[0].strip("\n")

                search_lines = search_part.splitlines(keepends=True) if search_part else []
                replace_lines = replace_part.splitlines(keepends=True) if replace_part else []

                # Clean hallucinated line numbers from replace lines just in case
                cleaned_replace = []
                for line in replace_lines:
                    cleaned_line = re.sub(r"^\s*\d+\s+\|\s?", "", line)
                    if not cleaned_line.endswith("\n") and line.endswith("\n"):
                        cleaned_line += "\n"
                    cleaned_replace.append(cleaned_line)
                replace_lines = cleaned_replace

                if replace_lines and not replace_lines[-1].endswith("\n"):
                    replace_lines[-1] += "\n"

                # We will perform the search/replace on the FULL file 'lines' directly
                # To be lenient, we'll try to find the exact search string in the snippet window first
                search_str = "".join(search_lines)
                replace_str = "".join(replace_lines)
                
                snippet_str = "".join(snippet_lines)
                # Clean snippet lines to match search str without line numbers
                clean_snippet_str = "".join([re.sub(r"^\s*\d+\s+\|\s?", "", ln) for ln in snippet_lines])
                
                full_text = "".join(lines)
                
                if search_str and search_str in clean_snippet_str:
                    new_text = full_text.replace(search_str, replace_str, 1)
                elif search_str and search_str in full_text:
                    new_text = full_text.replace(search_str, replace_str, 1)
                else:
                    # Fallback: just replace the entire snippet window if search block doesn't match perfectly
                    logger.warning("SEARCH block did not match exactly, falling back to replacing the window.")
                    new_text = "".join(lines[:start] + replace_lines + lines[end:])

                new_lines = new_text.splitlines(keepends=True)

                changed_count = abs(len(new_lines) - len(lines))
                if changed_count > MAX_CHANGED_LINES:
                    logger.warning("LLM patch changed too many lines (%d) -> rejected", changed_count)
                    return None, ""

                return new_lines, f"LLM fix around line {line_no} via SEARCH/REPLACE"
        except Exception as exc:
            logger.warning("LLM patch failed: %s", exc)
            return None, ""

    # ── Write patch to disk ──────────────────────────────────────────

    def _write_patch(
        self,
        abs_path: Path,
        original: str,
        old_lines: list[str],
        new_lines: list[str],
        bug: dict[str, Any],
        description: str,
        method: str,
        repo_path: Path | None = None,
    ) -> dict[str, Any]:
        """Write fixed content and return a patch record."""

        diff_lines: list[str] = []
        for i, (old, new) in enumerate(zip(old_lines, new_lines)):
            if old != new:
                diff_lines.append(
                    f"L{i + 1}: -{old.rstrip()} → +{new.rstrip()}"
                )

        try:
            abs_path.write_text("".join(new_lines), encoding="utf-8")
            logger.info("Patch applied: %s (%s)", abs_path, description)
        except Exception as exc:
            return {
                "bug": bug,
                "status": "write_error",
                "reason": str(exc),
                "patch": None,
            }

        # Use relative path for display / results.json
        display_path = str(abs_path)
        if repo_path is not None:
            try:
                display_path = str(abs_path.relative_to(repo_path.resolve()))
            except ValueError:
                pass

        return {
            "bug": bug,
            "status": "applied",
            "method": method,
            "description": description,
            "file": display_path,
            "patch": diff_lines[:MAX_CHANGED_LINES],
        }
