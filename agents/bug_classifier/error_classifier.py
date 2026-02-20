"""Error Classifier – converts raw test-failure logs into structured bug reports.

Strategy
────────
1.  **Regex pass** – a bank of compiled patterns matches the most common
    Python / JS / TS error shapes and extracts *file*, *line*, *bug_type*,
    and a standardised *message* in one shot.
2.  **LLM fallback** – any line that survives regex is sent (in batch) to an
    OpenAI-compatible chat model which returns the same JSON schema.

Output schema (per bug)::

    {
        "file": "src/app.py",
        "line": 42,
        "bug_type": "SYNTAX",          # enum – see BugType
        "message": "SyntaxError: invalid syntax"
    }

Usage::

    from agents.bug_classifier.error_classifier import classify_errors

    bugs = classify_errors(raw_log_text)            # sync, regex-only
    bugs = await classify_errors_async(raw_log_text) # async, regex + LLM
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any
from shared.determinism import LLM_DETERMINISTIC_PARAMS
logger = logging.getLogger(__name__)


# ── Bug-type enum ────────────────────────────────────────────────────

class BugType(str, Enum):
    LINTING = "LINTING"
    SYNTAX = "SYNTAX"
    LOGIC = "LOGIC"
    TYPE_ERROR = "TYPE_ERROR"
    IMPORT = "IMPORT"
    INDENTATION = "INDENTATION"


# ── Output dataclass ─────────────────────────────────────────────────

@dataclass
class BugReport:
    file: str
    line: int
    bug_type: str          # one of BugType values
    message: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ═══════════════════════════════════════════════════════════════════════
#  REGEX PASS
# ═══════════════════════════════════════════════════════════════════════

# Each entry: (compiled_regex, BugType, message_builder)
#   group names expected from regex: "file", "line", "detail" (optional)
_REGEX_RULES: list[tuple[re.Pattern, BugType, str]] = []


def _r(pattern: str, bug_type: BugType, message: str) -> None:
    """Register a regex rule."""
    _REGEX_RULES.append((re.compile(pattern, re.MULTILINE | re.IGNORECASE), bug_type, message))


# ── Python tracebacks ────────────────────────────────────────────────
#
# Real tracebacks have multiple lines between `File "...", line N` and
# the actual error (code line, caret, sometimes more).  We bridge up to
# 5 intermediate lines with ``(?:.*\n){1,5}``.

# IndentationError / TabError
_r(
    r'File "(?P<file>[^"]+)", line (?P<line>\d+)(?:.*\n){1,5}.*?(?P<detail>IndentationError:.+)',
    BugType.INDENTATION,
    "{detail}",
)
_r(
    r'File "(?P<file>[^"]+)", line (?P<line>\d+)(?:.*\n){1,5}.*?(?P<detail>TabError:.+)',
    BugType.INDENTATION,
    "{detail}",
)

# SyntaxError
_r(
    r'File "(?P<file>[^"]+)", line (?P<line>\d+)(?:.*\n){1,5}.*?(?P<detail>SyntaxError:.+)',
    BugType.SYNTAX,
    "{detail}",
)

# TypeError
_r(
    r'File "(?P<file>[^"]+)", line (?P<line>\d+)(?:.*\n){1,5}.*?(?P<detail>TypeError:.+)',
    BugType.TYPE_ERROR,
    "{detail}",
)

# ImportError / ModuleNotFoundError
_r(
    r'File "(?P<file>[^"]+)", line (?P<line>\d+)(?:.*\n){1,5}.*?(?P<detail>(?:ImportError|ModuleNotFoundError):.+)',
    BugType.IMPORT,
    "{detail}",
)

# AssertionError (Python traceback form)
_r(
    r'File "(?P<file>[^"]+)", line (?P<line>\d+)(?:.*\n){1,5}.*?(?P<detail>AssertionError.*)',
    BugType.LOGIC,
    "{detail}",
)

# Generic "assert" failure (pytest short form)
_r(
    r'(?P<file>[^\s:]+):(?P<line>\d+): (?P<detail>AssertionError.*)',
    BugType.LOGIC,
    "AssertionError: {detail}",
)

# ── Python linting (flake8 / pylint / ruff) ──────────────────────────

# flake8 / ruff:  path:line:col: CODE message
_r(
    r"(?P<file>[^\s:]+):(?P<line>\d+):\d+:\s*(?P<detail>[EWFCB]\d+\s+.+)",
    BugType.LINTING,
    "{detail}",
)

# pylint:  path:line:col: CODE (msg) message
_r(
    r"(?P<file>[^\s:]+):(?P<line>\d+):\d+:\s*(?P<detail>[CRWEF]\d{4}:.+)",
    BugType.LINTING,
    "{detail}",
)

# "imported but unused" / "unused import" (flake8 F401, ruff, etc.)
_r(
    r"(?P<file>[^\s:]+):(?P<line>\d+).*(?P<detail>['\"]?\w+['\"]?\s+imported but unused.*)",
    BugType.LINTING,
    "unused import: {detail}",
)

# ── JS / TS errors ───────────────────────────────────────────────────

# Node.js SyntaxError:  path:line
_r(
    r"(?P<file>[^\s:]+\.(?:js|ts|jsx|tsx|mjs|cjs)):(?P<line>\d+)\n.*\n\s*(?P<detail>SyntaxError:.+)",
    BugType.SYNTAX,
    "{detail}",
)

# TypeScript type error:  TSnnnn
_r(
    r"(?P<file>[^\s(]+\.tsx?)\((?P<line>\d+),\d+\):\s*error\s+(?P<detail>TS\d+:.+)",
    BugType.TYPE_ERROR,
    "{detail}",
)
# tsc format: path(line,col): error TSnnnn: ...
_r(
    r"(?P<file>[^\s:]+\.tsx?):(?P<line>\d+):\d+\s*-\s*error\s+(?P<detail>TS\d+:.+)",
    BugType.TYPE_ERROR,
    "{detail}",
)

# ESLint:  path:line:col  rule  message
_r(
    r"(?P<file>[^\s:]+):(?P<line>\d+):\d+\s+(?:error|warning)\s+(?P<detail>.+?)\s{2,}\S+",
    BugType.LINTING,
    "{detail}",
)

# Cannot find module (JS/TS)
_r(
    r"(?P<file>[^\s:]+):(?P<line>\d+).*Cannot find module\s+'(?P<detail>[^']+)'",
    BugType.IMPORT,
    "Cannot find module '{detail}'",
)

# Jest / Vitest expect assertion failure
_r(
    r"(?P<file>[^\s:]+):(?P<line>\d+).*(?P<detail>expect\(.+\)\.\w+\(.+\))",
    BugType.LOGIC,
    "Assertion failure: {detail}",
)

# ── py_compile / static analysis errors ──────────────────────────────

# py_compile.PyCompileError one-line form:
#   Sorry: SyntaxError: expected ':' ('validator.py', line 8)
_r(
    r"Sorry:\s*(?P<detail>SyntaxError:.+?)\s*\('?(?P<file>[^']+?)'?,\s*line\s+(?P<line>\d+)\)",
    BugType.SYNTAX,
    "{detail}",
)
_r(
    r"Sorry:\s*(?P<detail>IndentationError:.+?)\s*\('?(?P<file>[^']+?)'?,\s*line\s+(?P<line>\d+)\)",
    BugType.INDENTATION,
    "{detail}",
)
_r(
    r"Sorry:\s*(?P<detail>TabError:.+?)\s*\('?(?P<file>[^']+?)'?,\s*line\s+(?P<line>\d+)\)",
    BugType.INDENTATION,
    "{detail}",
)

# FAIL: py_compile.PyCompileError verbose form emitted by our syntax checker
_r(
    r"FAIL:\s*(?P<detail>(?:SyntaxError|IndentationError|TabError):.+?)\s*\(?(?P<file>[^\s',]+?)(?:,|\s+)line\s+(?P<line>\d+)",
    BugType.SYNTAX,
    "{detail}",
)

# node --check syntax error:
#   /path/file.js:10  SyntaxError: Unexpected token ...
_r(
    r"(?P<file>[^\s:]+\.(?:js|jsx|mjs|cjs)):(?P<line>\d+)\s*\n.*\n\n\s*(?P<detail>SyntaxError:.+)",
    BugType.SYNTAX,
    "{detail}",
)

# ── Static-analysis heuristic outputs ─────────────────────────────────

# TypeError from JS heuristic checker (our custom format):
#   File "file.js", line N
#     code
#   TypeError: varname.method is not a function (varname is a number)
_r(
    r'File "(?P<file>[^"]+)", line (?P<line>\d+)\n\s+.+\n(?P<detail>TypeError:.+)',
    BugType.TYPE_ERROR,
    "{detail}",
)

# LogicError from JS heuristic checker (our custom format):
#   File "file.js", line N
#     code
#   LogicError: description
_r(
    r'File "(?P<file>[^"]+)", line (?P<line>\d+)\n\s+.+\n(?P<detail>LogicError:.+)',
    BugType.LOGIC,
    "{detail}",
)

# Java missing semicolon (our format):
#   File "AuthService.java", line N
#     code
#   SyntaxError: missing semicolon
_r(
    r'File "(?P<file>[^"]+\.java)", line (?P<line>\d+)\n\s+.+\n(?P<detail>SyntaxError:.+)',
    BugType.SYNTAX,
    "{detail}",
)

# ── Generic fallbacks (broad patterns, checked last) ─────────────────

# "missing colon" / colon-related syntax errors
_r(
    r'File "(?P<file>[^"]+)", line (?P<line>\d+).*(?P<detail>expected.+:)',
    BugType.SYNTAX,
    "{detail}",
)

# Bare "file:line: error-keyword", ensure file looks like a file (has an extension)
_r(
    r"(?P<file>[^\s:]+\.[a-zA-Z0-9]+):(?P<line>\d+).*\b(?P<detail>(?:IndentationError|unexpected indent|unindent does not match).+)",
    BugType.INDENTATION,
    "{detail}",
)
_r(
    r"(?P<file>[^\s:]+\.[a-zA-Z0-9]+):(?P<line>\d+).*\b(?P<detail>(?:SyntaxError|invalid syntax|unexpected EOF|missing colon).+)",
    BugType.SYNTAX,
    "{detail}",
)
_r(
    r"(?P<file>[^\s:]+\.[a-zA-Z0-9]+):(?P<line>\d+).*\b(?P<detail>(?:TypeError|type mismatch|cannot assign).+)",
    BugType.TYPE_ERROR,
    "{detail}",
)
_r(
    r"(?P<file>[^\s:]+\.[a-zA-Z0-9]+):(?P<line>\d+).*\b(?P<detail>(?:ModuleNotFoundError|ImportError|No module named|Cannot find module).+)",
    BugType.IMPORT,
    "{detail}",
)
_r(
    r"(?P<file>[^\s:]+\.[a-zA-Z0-9]+):(?P<line>\d+).*\b(?P<detail>(?:AssertionError|assertion failed|assert ).+)",
    BugType.LOGIC,
    "{detail}",
)


# ── Regex classifier ─────────────────────────────────────────────────

def _regex_classify(log: str) -> tuple[list[BugReport], list[str]]:
    """Return (matched_bugs, unmatched_lines).

    Walks every regex rule over the full log.  Lines that aren't part
    of any match are collected for the LLM fallback.
    """
    bugs: list[BugReport] = []
    matched_spans: list[tuple[int, int]] = []

    for pattern, bug_type, msg_template in _REGEX_RULES:
        for m in pattern.finditer(log):
            file = m.group("file")
            line = int(m.group("line"))
            detail = m.group("detail") if "detail" in m.groupdict() else ""

            # Normalise the message using the template
            message = msg_template.format(detail=detail.strip()) if detail else msg_template
            # De-duplicate: skip if same file+line+type already recorded
            key = (file, line, bug_type.value)
            if any((b.file, b.line, b.bug_type) == key for b in bugs):
                continue

            bugs.append(BugReport(file=file, line=line, bug_type=bug_type.value, message=message))
            matched_spans.append((m.start(), m.end()))

    # Collect lines that weren't covered by any match
    covered = set()
    for start, end in matched_spans:
        # Map span back to line indices
        for i, line in enumerate(log.splitlines()):
            line_start = log.index(line) if i == 0 else log.index(line, sum(len(l) + 1 for l in log.splitlines()[:i]))
            # Simplified: we mark entire match region
        covered.add((start, end))

    # Build unmatched text by removing matched spans
    unmatched_chars = list(range(len(log)))
    for start, end in sorted(matched_spans, reverse=True):
        del unmatched_chars[start:end]

    unmatched_text = ""
    if matched_spans:
        # Reconstruct unmatched text
        remaining = set(unmatched_chars)
        unmatched_lines = []
        for i, line in enumerate(log.splitlines()):
            line_start = sum(len(l) + 1 for l in log.splitlines()[:i])
            line_end = line_start + len(line)
            if any(pos in remaining for pos in range(line_start, line_end)):
                stripped = line.strip()
                if stripped and not stripped.startswith("Traceback") and len(stripped) > 10:
                    unmatched_lines.append(stripped)
        unmatched_text = "\n".join(unmatched_lines)
    else:
        # Nothing matched – pass the whole log
        unmatched_text = log

    # Filter truly useful unmatched lines (skip noise like blank/separator lines)
    useful = [
        ln for ln in unmatched_text.splitlines()
        if ln.strip()
        and not ln.strip().startswith("---")
        and not ln.strip().startswith("===")
        and not ln.strip().startswith("FAILED")
        and not ln.strip().startswith("PASSED")
        and "short test summary" not in ln.lower()
    ]

    # Always include SOURCE ANALYSIS blocks for LLM deep analysis,
    # even if regex matched some other errors.
    source_block_pattern = re.compile(
        r"--- SOURCE ANALYSIS .+?---\n.+?\n--- END .+?---",
        re.DOTALL,
    )
    source_blocks = source_block_pattern.findall(log)
    if source_blocks:
        useful.extend(source_blocks)

    return bugs, useful


# ═══════════════════════════════════════════════════════════════════════
#  LLM FALLBACK
# ═══════════════════════════════════════════════════════════════════════

_LLM_SYSTEM_PROMPT = """\
You are a test-failure and source-code classifier.  You receive two kinds
of input:

1. **Error log lines** from test / build / static-analysis output that
   could not be classified by regex.
2. **Source code blocks** wrapped in markers like:
       --- SOURCE ANALYSIS (ALL): path/to/file.ext ---
       1 | code line 1
       2 | code line 2
       --- END path/to/file.ext ---

For EACH distinct bug you find, return a JSON object:
{
  "file": "<source file path or 'unknown'>",
  "line": <line number or 0>,
  "bug_type": "<one of: LINTING, SYNTAX, LOGIC, TYPE_ERROR, IMPORT, INDENTATION>",
  "message": "<one concise standardised sentence>"
}

Classification rules:
- unused import / unused variable        → LINTING
- missing colon / semicolon / bad syntax → SYNTAX
- wrong operator / wrong logic           → LOGIC
- type mismatch / calling method on wrong type → TYPE_ERROR
- module not found / import error        → IMPORT
- indentation / tab / alignment error    → INDENTATION

When analysing SOURCE blocks, look carefully for:
- Logic errors (e.g., using / instead of * in a calculation)
- Type errors (e.g., calling .toUpperCase() on a number)
- Unused imports / variables
- Missing semicolons or syntax issues
- Indentation inconsistencies

Return a JSON **array** of objects.  If nothing is classifiable return [].
Do NOT wrap in markdown fences.
"""


async def _llm_classify(lines: list[str]) -> list[BugReport]:
    """Send unmatched lines to an OpenAI-compatible model."""
    if not lines:
        return []

    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        logger.debug("No GEMINI_API_KEY set – skipping LLM fallback")
        return []

    model = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")

    try:
        import asyncio as _asyncio
        import httpx
    except ImportError:
        logger.warning("httpx not installed – LLM fallback unavailable")
        return []

    payload = {
        "model": model,
        **LLM_DETERMINISTIC_PARAMS,
        "messages": [
            {"role": "system", "content": _LLM_SYSTEM_PROMPT},
            {"role": "user", "content": "\n".join(lines)},
        ],
    }

    base_url = os.environ.get("GEMINI_API_BASE", "https://generativelanguage.googleapis.com/v1beta/openai")

    max_retries = 3
    for attempt in range(max_retries + 1):
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                # Handle 429 rate limit with retry
                if resp.status_code == 429 and attempt < max_retries:
                    delay = 2 ** (attempt + 1)  # 2, 4, 8 seconds
                    logger.warning(
                        "LLM rate-limited (429), retrying in %ds (attempt %d/%d)",
                        delay, attempt + 1, max_retries,
                    )
                    await _asyncio.sleep(delay)
                    continue

                if resp.status_code != 200:
                    logger.error(
                        "LLM classifier HTTP %d from %s: %s",
                        resp.status_code, base_url,
                        resp.text[:500],
                    )

                resp.raise_for_status()
                data = resp.json()
                content = data["choices"][0]["message"]["content"].strip()
                logger.info(
                    "LLM classifier returned %d chars from %s/%s",
                    len(content), base_url, model,
                )

                # Strip markdown fences if the model wraps anyway
                if content.startswith("```"):
                    content = re.sub(r"^```(?:json)?\n?", "", content)
                    content = re.sub(r"\n?```$", "", content)

                items = json.loads(content)
                if not isinstance(items, list):
                    items = [items]

                results: list[BugReport] = []
                valid_types = {t.value for t in BugType}
                for item in items:
                    bug_type = item.get("bug_type", "").upper()
                    if bug_type not in valid_types:
                        continue
                    results.append(BugReport(
                        file=item.get("file", "unknown"),
                        line=int(item.get("line", 0)),
                        bug_type=bug_type,
                        message=item.get("message", ""),
                    ))
                return results

        except Exception as exc:
            if attempt < max_retries:
                delay = 2 ** (attempt + 1)
                logger.warning(
                    "LLM fallback error (attempt %d/%d): %s — retrying in %ds",
                    attempt + 1, max_retries, exc, delay,
                )
                await _asyncio.sleep(delay)
            else:
                logger.warning("LLM fallback failed after %d retries: %s", max_retries, exc)
                return []

    return []


# ═══════════════════════════════════════════════════════════════════════
#  PUBLIC API
# ═══════════════════════════════════════════════════════════════════════

def classify_errors(log: str) -> list[BugReport]:
    """Synchronous, regex-only classification.

    Returns a list of :class:`BugReport` for every error found.
    """
    bugs, _ = _regex_classify(log)
    bugs.sort(key=lambda b: (b.file, b.line))
    return bugs


async def classify_errors_async(log: str) -> list[BugReport]:
    """Async classification with regex + LLM fallback.

    Any log lines not matched by regex are sent to the configured
    OpenAI model for classification.
    """
    bugs, unmatched = _regex_classify(log)
    if unmatched:
        llm_bugs = await _llm_classify(unmatched)
        # De-duplicate against regex results
        existing = {(b.file, b.line, b.bug_type) for b in bugs}
        for lb in llm_bugs:
            key = (lb.file, lb.line, lb.bug_type)
            if key not in existing:
                bugs.append(lb)
                existing.add(key)

    # ── Post-pass: trace test-file bugs back to source files ─────
    # When the LLM is unavailable, regex only catches error lines
    # pointing to test files. Parse the full log to extract the
    # actual source-file references from Python tracebacks.
    bugs = _trace_test_bugs_to_source(bugs, log)

    bugs.sort(key=lambda b: (b.file, b.line))
    return bugs


# ── Test-file detection (shared logic) ───────────────────────────────

_TEST_FILE_RE = [
    re.compile(r"(^|/)test_[^/]+\.py$"),
    re.compile(r"(^|/)[^/]+_test\.py$"),
    re.compile(r"(^|/)tests?/"),
    re.compile(r"(^|/)conftest\.py$"),
    re.compile(r"(^|/)__tests__/"),
]


def _is_test_path(filepath: str) -> bool:
    return any(p.search(filepath) for p in _TEST_FILE_RE)


def _trace_test_bugs_to_source(
    bugs: list[BugReport], log: str
) -> list[BugReport]:
    """Re-attribute test-file bugs to source files using traceback parsing.

    Pytest tracebacks look like:

        _____________________________ test_divide_by_zero ______________________________

            def test_divide_by_zero():
        >       result = divide(10, 0)

        tests/test_math_ops.py:24:
        _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

            def divide(a, b):
        >       return a / b
        E       ZeroDivisionError: division by zero

        src/math_ops.py:21: ZeroDivisionError

    The source file ``src/math_ops.py`` is where we should apply the fix.
    """
    # Parse all FAILED lines from pytest short summary
    # e.g.: FAILED tests/test_math_ops.py::test_divide_by_zero - ZeroDivisionError: ...
    failed_pattern = re.compile(
        r"FAILED\s+(\S+?)::(\S+)\s+-\s+(.+)"
    )

    # File references in pytest tracebacks — TWO formats:
    # 1. Python traceback: File "src/math_ops.py", line 21, in divide
    # 2. Pytest short:     src/math_ops.py:21: ZeroDivisionError
    file_ref_verbose = re.compile(r'File "([^"]+)", line (\d+)')
    file_ref_short = re.compile(r'^(\S+\.(?:py|js|ts|jsx|tsx)):(\d+):', re.MULTILINE)

    # Collect failed test info from summary lines
    failed_tests = {}
    for m in failed_pattern.finditer(log):
        test_path = m.group(1)
        test_name = m.group(2)
        error_msg = m.group(3)
        failed_tests[test_name] = {
            "test_path": test_path,
            "error_msg": error_msg.strip(),
        }

    if not failed_tests:
        return bugs

    # Pytest traceback sections: ______ test_name ______
    # The test name can contain underscores, so we match any non-space chars
    test_section_re = re.compile(
        r"_{5,}\s+(\S+)\s+_{5,}(.*?)(?=_{5,}\s+\S+\s+_{5,}|={5,}|\Z)",
        re.DOTALL,
    )

    source_bugs_from_tracebacks: list[BugReport] = []

    for section_match in test_section_re.finditer(log):
        test_name = section_match.group(1)
        section_text = section_match.group(2)

        if test_name not in failed_tests:
            continue

        info = failed_tests[test_name]
        error_msg = info["error_msg"]

        # Find ALL file references in this section using both patterns
        file_refs: list[tuple[str, int]] = []
        for m in file_ref_verbose.finditer(section_text):
            file_refs.append((m.group(1), int(m.group(2))))
        for m in file_ref_short.finditer(section_text):
            file_refs.append((m.group(1), int(m.group(2))))

        # Filter to source files only
        source_refs = [
            (f, ln) for f, ln in file_refs
            if not _is_test_path(f)
            and "/site-packages/" not in f
            and not f.startswith("/usr/")
            and not f.startswith("<")
        ]

        if source_refs:
            # Use the LAST source ref (closest to the actual error)
            src_file, src_line = source_refs[-1]
            # Strip Docker /workspace/ prefix
            if src_file.startswith("/workspace/"):
                src_file = src_file[len("/workspace/"):]

            # Determine bug type from the error message
            bug_type = BugType.LOGIC.value
            el = error_msg.lower()
            if "indexerror" in el:
                bug_type = BugType.LOGIC.value
            elif "zerodivisionerror" in el:
                bug_type = BugType.LOGIC.value
            elif "recursionerror" in el:
                bug_type = BugType.LOGIC.value
            elif "typeerror" in el:
                bug_type = BugType.TYPE_ERROR.value
            elif "importerror" in el or "modulenotfound" in el:
                bug_type = BugType.IMPORT.value
            elif "syntaxerror" in el:
                bug_type = BugType.SYNTAX.value
            elif "indentationerror" in el:
                bug_type = BugType.INDENTATION.value

            source_bugs_from_tracebacks.append(BugReport(
                file=src_file,
                line=src_line,
                bug_type=bug_type,
                message=error_msg,
            ))
            logger.info(
                "[TracebackTrace] %s → %s:%d (%s)",
                test_name, src_file, src_line, error_msg[:80],
            )

    if not source_bugs_from_tracebacks:
        return bugs

    # Replace test-file bugs with traced source-file bugs
    non_test_bugs = [b for b in bugs if not _is_test_path(b.file)]
    all_bugs = non_test_bugs + source_bugs_from_tracebacks

    # De-duplicate
    seen: set[tuple[str, int, str]] = set()
    unique: list[BugReport] = []
    for b in all_bugs:
        key = (b.file, b.line, b.bug_type)
        if key not in seen:
            seen.add(key)
            unique.append(b)

    logger.info(
        "[TracebackTrace] Traced %d test-file bug(s) → %d source-file bug(s)",
        sum(1 for b in bugs if _is_test_path(b.file)),
        len(source_bugs_from_tracebacks),
    )
    return unique


