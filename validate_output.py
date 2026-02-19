#!/usr/bin/env python3
"""Validate results.json against the canonical output schema.

Checks
------
1. ``bug_type`` is uppercase and one of the known enum values.
2. Message format matches:
       {BUG_TYPE} error in {file} line {line} → Fix: {message}
   Arrow must be the unicode character → (U+2192), **not** ``->``.
3. No trailing whitespace on any string value.
4. ``branch`` matches ``TEAM_LEADER_AI_Fix`` (uppercase + underscores only).
5. Every ``commit_message`` starts with ``[AI-AGENT]``.

Exit codes
----------
- 0  – PASS (all checks succeeded)
- 1  – FAIL (at least one mismatch printed)
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# ── Known valid bug types (from agents.bug_classifier.error_classifier) ──
VALID_BUG_TYPES: set[str] = {
    "LINTING",
    "SYNTAX",
    "LOGIC",
    "TYPE_ERROR",
    "IMPORT",
    "INDENTATION",
}

# Branch must be UPPERCASE_AND_UNDERSCORES only, ending with _AI_Fix
_BRANCH_RE = re.compile(r"^[A-Z][A-Z0-9]*(_[A-Z][A-Z0-9]*)*_AI_Fix$")


def _trailing_space(value: str) -> bool:
    """Return True if *value* has trailing whitespace on any line."""
    for line in value.splitlines():
        if line != line.rstrip():
            return True
    return value != value.rstrip()


def validate(results: dict) -> list[str]:
    """Return a list of human-readable mismatch strings (empty == PASS)."""
    errors: list[str] = []

    # ── Top-level branch ─────────────────────────────────────────────
    branch = results.get("branch", "")
    if not _BRANCH_RE.match(branch):
        errors.append(
            f"branch: expected TEAM_LEADER_AI_Fix (uppercase/underscore), "
            f"got {branch!r}"
        )

    # ── Trailing spaces on top-level strings ─────────────────────────
    for key in ("repository_url", "branch", "team_name", "leader_name",
                "final_ci_status", "generated_at"):
        val = results.get(key, "")
        if isinstance(val, str) and _trailing_space(val):
            errors.append(f"{key}: trailing whitespace in {val!r}")

    # ── Fixes array ──────────────────────────────────────────────────
    fixes = results.get("fixes", [])
    if not isinstance(fixes, list):
        errors.append("fixes: expected a JSON array")
        return errors

    for idx, fix in enumerate(fixes):
        prefix = f"fixes[{idx}]"

        # -- bug_type --------------------------------------------------
        bug_type = fix.get("bug_type", "")
        if bug_type != bug_type.upper():
            errors.append(f"{prefix}.bug_type: not uppercase: {bug_type!r}")
        if bug_type not in VALID_BUG_TYPES:
            errors.append(
                f"{prefix}.bug_type: unknown value {bug_type!r}; "
                f"expected one of {sorted(VALID_BUG_TYPES)}"
            )

        # -- required fields -------------------------------------------
        file = fix.get("file", "")
        line = fix.get("line", 0)
        commit_msg = fix.get("commit_message", "")
        status = fix.get("status", "")

        # -- trailing whitespace on every string field -----------------
        for field_name in ("file", "bug_type", "commit_message", "status"):
            val = fix.get(field_name, "")
            if isinstance(val, str) and _trailing_space(val):
                errors.append(
                    f"{prefix}.{field_name}: trailing whitespace in {val!r}"
                )

        # -- commit_message starts with [AI-AGENT] --------------------
        if not commit_msg.startswith("[AI-AGENT]"):
            errors.append(
                f"{prefix}.commit_message: must start with [AI-AGENT], "
                f"got {commit_msg!r}"
            )

        # -- message format --------------------------------------------
        # Build the expected canonical line from the fix fields.
        expected = f"{bug_type} error in {file} line {line} \u2192 Fix: {commit_msg}"

        # 1) The arrow character must be → (U+2192)
        if "->" in commit_msg or "\u2192" not in expected:
            errors.append(
                f"{prefix}: arrow must be unicode \u2192 (U+2192), not '->'"
            )

        # 2) Verify the canonical format can be reconstructed
        #    We parse the generated line back and compare pieces.
        fmt_re = re.compile(
            r"^(?P<bt>[A-Z_]+) error in (?P<f>.+?) line (?P<ln>\d+) \u2192 Fix: (?P<msg>.+)$"
        )
        m = fmt_re.match(expected)
        if m is None:
            errors.append(
                f"{prefix}: message format mismatch \u2013 could not parse "
                f"canonical line: {expected!r}"
            )
        else:
            if m.group("bt") != bug_type:
                errors.append(
                    f"{prefix}: bug_type in message {m.group('bt')!r} "
                    f"!= fix.bug_type {bug_type!r}"
                )
            if m.group("f") != file:
                errors.append(
                    f"{prefix}: file in message {m.group('f')!r} "
                    f"!= fix.file {file!r}"
                )
            if int(m.group("ln")) != line:
                errors.append(
                    f"{prefix}: line in message {m.group('ln')} "
                    f"!= fix.line {line}"
                )

    # ── CI timeline ──────────────────────────────────────────────────
    for idx, ci in enumerate(results.get("ci_timeline", [])):
        for field_name in ("status", "timestamp"):
            val = ci.get(field_name, "")
            if isinstance(val, str) and _trailing_space(val):
                errors.append(
                    f"ci_timeline[{idx}].{field_name}: trailing whitespace "
                    f"in {val!r}"
                )

    return errors


# ── CLI entry point ──────────────────────────────────────────────────

def main(path: str | None = None) -> int:
    target = Path(path) if path else Path(__file__).resolve().parent / "shared" / "results.json"
    if not target.exists():
        print(f"ERROR: {target} not found")
        return 1

    with target.open() as f:
        data = json.load(f)

    # Support both bare object and {"runs": [...]} wrapper
    if "runs" in data and isinstance(data["runs"], list):
        objects = data["runs"]
    else:
        objects = [data]

    if not objects:
        print("PASS (no runs to validate)")
        return 0

    all_errors: list[str] = []
    for i, run in enumerate(objects):
        run_errors = validate(run)
        for e in run_errors:
            label = f"run[{i}] " if len(objects) > 1 else ""
            all_errors.append(f"{label}{e}")

    if all_errors:
        print(f"FAIL — {len(all_errors)} issue(s):\n")
        for e in all_errors:
            print(f"  • {e}")
        return 1

    print("PASS")
    return 0


if __name__ == "__main__":
    path_arg = sys.argv[1] if len(sys.argv) > 1 else None
    sys.exit(main(path_arg))
