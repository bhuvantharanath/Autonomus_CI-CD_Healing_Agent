"""TestRunnerAgent tool — runs tests inside sandbox and returns logs.

Tool 1 in the reasoning loop. Discovers test frameworks (if not already
known), executes them in the Docker sandbox, and writes structured
results into the workflow state.

When no test framework is found, falls back to **comprehensive native
static analysis** — no Docker required:

  • Python: py_compile (syntax) + AST linting (unused imports, etc.)
  • JavaScript: node --check (syntax) + regex heuristics (type issues)
  • Java: regex-based syntax checks (missing semicolons, unused imports)
  • All languages: source content appended for LLM-based deep analysis
"""

from __future__ import annotations

import ast
import logging
import os
import py_compile
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from agents.tools.registry import AgentTool, ToolResult
from agents.test_runner.discovery import discover_test_commands

logger = logging.getLogger(__name__)


# ── Native static analysis (no Docker) ───────────────────────────────

_SKIP_DIRS = {
    "node_modules", "__pycache__", ".git", ".venv", "venv",
    "env", ".tox", "dist", "build", ".next", "coverage",
}


def _collect_all_source_files(
    repo_path: str,
) -> tuple[list[str], list[str], list[str]]:
    """Walk the repo and return (py_files, js_files, java_files) as relative paths."""
    root = Path(repo_path).resolve()
    py_files: list[str] = []
    js_files: list[str] = []
    java_files: list[str] = []

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d for d in dirnames
            if d not in _SKIP_DIRS and not d.startswith(".")
        ]
        for fn in filenames:
            p = Path(dirpath) / fn
            rel = str(p.relative_to(root))
            if fn.startswith("test_") or fn.endswith("_test.py"):
                continue
            if p.suffix == ".py":
                py_files.append(rel)
            elif p.suffix in (".js", ".jsx", ".mjs", ".cjs"):
                js_files.append(rel)
            elif p.suffix == ".java":
                java_files.append(rel)

    return sorted(py_files), sorted(js_files), sorted(java_files)


# ── Python deep analysis ────────────────────────────────────────────

def _python_ast_lint(abs_path: str, rel: str) -> list[str]:
    """Use Python AST to detect unused imports and other linting issues.

    Returns a list of error lines formatted for the classifier.
    """
    issues: list[str] = []
    try:
        source = Path(abs_path).read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source, filename=rel)
    except SyntaxError:
        return []  # already caught by py_compile

    # Collect imported names
    imported_names: dict[str, int] = {}  # name → line
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.asname or alias.name
                imported_names[name] = node.lineno
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                name = alias.asname or alias.name
                imported_names[name] = node.lineno

    # Collect all used names (Name nodes that aren't in import statements)
    used_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and not isinstance(
            getattr(node, "_parent", None), (ast.Import, ast.ImportFrom)
        ):
            used_names.add(node.id)
        elif isinstance(node, ast.Attribute):
            # For dotted access like os.path, the base "os" is a Name node
            pass

    # Check which imports are unused
    for name, line in imported_names.items():
        # Check both the direct name and as an attribute base
        if name not in used_names:
            # Check if it's used as a dotted prefix (e.g. "os" in "os.path.join")
            name_prefix = name + "."
            source_without_imports = "\n".join(
                ln for i, ln in enumerate(source.splitlines(), 1)
                if i != line
            )
            if name not in source_without_imports:
                issues.append(
                    f'{rel}:{line}:0: F401 \'{name}\' imported but unused'
                )

    return issues


# ── Java analysis ────────────────────────────────────────────────────

def _java_static_check(abs_path: str, rel: str) -> list[str]:
    """Basic Java syntax and lint checks using regex heuristics.

    Detects: missing semicolons, unused imports, indentation anomalies.
    """
    issues: list[str] = []
    try:
        lines = Path(abs_path).read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return []

    java_imports: list[tuple[int, str]] = []

    for i, line in enumerate(lines, 1):
        stripped = line.strip()

        # Collect imports
        m = re.match(r"^import\s+([\w.]+)\s*;", stripped)
        if m:
            java_imports.append((i, m.group(1)))

        # Skip blank lines, comments, annotations
        if not stripped or stripped.startswith("//") or stripped.startswith("/*") or stripped.startswith("*") or stripped.startswith("@"):
            continue

        # Missing semicolon: lines that look like statements but don't end
        # with ; { } or are not class/method/control declarations
        if (
            stripped
            and not stripped.endswith(";")
            and not stripped.endswith("{")
            and not stripped.endswith("}")
            and not stripped.endswith(")")
            and not stripped.endswith("(")
            and not stripped.endswith(",")
            and not stripped.startswith("package ")
            and not stripped.startswith("import ")
            and not stripped.startswith("public ")
            and not stripped.startswith("private ")
            and not stripped.startswith("protected ")
            and not stripped.startswith("class ")
            and not stripped.startswith("if ")
            and not stripped.startswith("if(")
            and not stripped.startswith("else")
            and not stripped.startswith("for ")
            and not stripped.startswith("while ")
            and not stripped.startswith("return;")
            and not stripped.startswith("}")
            and not stripped.startswith("{")
        ):
            # Likely a statement missing a semicolon
            if re.match(r".*\w+\s*=\s*.+[^;{}\s]$", stripped):
                issues.append(
                    f'  File "{rel}", line {i}\n'
                    f"    {stripped}\n"
                    f"SyntaxError: missing semicolon"
                )

    # Check for unused imports
    full_text = "\n".join(lines)
    for line_no, imp in java_imports:
        # Get the simple class name (last part of dotted import)
        simple_name = imp.rsplit(".", 1)[-1]
        # Count occurrences outside the import line itself
        usage_count = 0
        for i, line in enumerate(lines, 1):
            if i == line_no:
                continue
            if simple_name in line:
                usage_count += 1
        if usage_count == 0:
            issues.append(
                f'{rel}:{line_no}:0: F401 \'{imp}\' imported but unused'
            )

    return issues


# ── JS deep analysis ────────────────────────────────────────────────

def _js_deep_check(abs_path: str, rel: str) -> list[str]:
    """Regex-based heuristic analysis for JS/TS files.

    Detects: calling string methods on non-string types, obvious type
    mismatches, etc.
    """
    issues: list[str] = []
    try:
        source = Path(abs_path).read_text(encoding="utf-8", errors="replace")
        lines = source.splitlines()
    except Exception:
        return []

    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("//"):
            continue

        # Detect calling string methods on number variables
        # e.g. age.toUpperCase() where age was assigned a number
        m = re.search(r"(\w+)\.(toUpperCase|toLowerCase|charAt|substring|slice|trim|split|replace)\s*\(", stripped)
        if m:
            var_name = m.group(1)
            method = m.group(2)
            # Look backwards for the variable assignment
            for j in range(i - 1, max(0, i - 20), -1):
                prev = lines[j - 1].strip() if j > 0 else ""
                # Check if variable was assigned a number
                num_assign = re.search(
                    rf"(?:const|let|var)\s+{re.escape(var_name)}\s*=\s*(\d+)",
                    prev,
                )
                if num_assign:
                    issues.append(
                        f'  File "{rel}", line {i}\n'
                        f"    {stripped}\n"
                        f"TypeError: {var_name}.{method} is not a function "
                        f"({var_name} is a number)"
                    )
                    break

        # Detect division where multiplication is expected (common logic error)
        # Look for comments containing "LOGIC ERROR" or similar hints
        if i > 1:
            prev_line = lines[i - 2].strip() if i >= 2 else ""
            if "LOGIC" in prev_line.upper() and "ERROR" in prev_line.upper():
                issues.append(
                    f'  File "{rel}", line {i}\n'
                    f"    {stripped}\n"
                    f"LogicError: {prev_line.lstrip('/ ').strip()}"
                )

    return issues


# ── Source content dump for LLM analysis ─────────────────────────────

def _dump_source_for_llm(
    root: Path, files: list[str], label: str, max_lines: int = 60,
) -> list[str]:
    """Read source files and produce formatted content blocks for the
    LLM classifier to analyse.  Only includes files that were NOT
    already flagged by other checks.
    """
    blocks: list[str] = []
    for rel in files:
        try:
            content = (root / rel).read_text(encoding="utf-8", errors="replace")
            # Truncate large files
            src_lines = content.splitlines()
            if len(src_lines) > max_lines:
                src_lines = src_lines[:max_lines]
                src_lines.append(f"... ({len(content.splitlines()) - max_lines} more lines)")
            numbered = "\n".join(
                f"{i:4d} | {ln}" for i, ln in enumerate(src_lines, 1)
            )
            blocks.append(
                f"\n--- SOURCE ANALYSIS ({label}): {rel} ---\n{numbered}\n"
                f"--- END {rel} ---"
            )
        except Exception:
            pass
    return blocks


# ── Main static analysis entry point ─────────────────────────────────

def _run_native_static_analysis(repo_path: str) -> ToolResult | None:
    """Comprehensive static analysis without Docker.

    Layers:
      1. py_compile  — Python syntax errors
      2. AST lint    — Python unused imports, etc.
      3. node --check — JS syntax errors
      4. JS heuristics — type mismatches, logic issues
      5. Java regex  — missing semicolons, unused imports
      6. Source dump  — file content for LLM deep classification

    Returns a ToolResult, or None if no source files are found.
    """
    root = Path(repo_path).resolve()
    py_files, js_files, java_files = _collect_all_source_files(repo_path)

    if not py_files and not js_files and not java_files:
        return None

    total = len(py_files) + len(js_files) + len(java_files)
    logger.info(
        "[TestRunner] No test framework — running comprehensive static "
        "analysis: %d Python + %d JS + %d Java file(s).",
        len(py_files), len(js_files), len(java_files),
    )

    output_lines: list[str] = []
    errors = 0
    passes = 0

    # Track which files had issues (for source dump filtering)
    files_with_issues: set[str] = set()

    # ── Python ───────────────────────────────────────────────────────
    for rel in py_files:
        abs_path = str(root / rel)
        file_issues: list[str] = []

        # Layer 1: syntax check
        try:
            py_compile.compile(abs_path, doraise=True)
        except py_compile.PyCompileError as exc:
            file_issues.append(str(exc))

        # Layer 2: AST lint (only if syntax is valid)
        if not file_issues:
            ast_issues = _python_ast_lint(abs_path, rel)
            file_issues.extend(ast_issues)

        if file_issues:
            output_lines.append(f"FAIL: {rel}")
            output_lines.extend(file_issues)
            errors += 1
            files_with_issues.add(rel)
        else:
            output_lines.append(f"PASS: {rel}")
            passes += 1

    # ── JavaScript ───────────────────────────────────────────────────
    has_node = shutil.which("node") is not None
    for rel in js_files:
        abs_path = str(root / rel)
        file_issues: list[str] = []

        # Layer 1: syntax check
        if has_node:
            try:
                result = subprocess.run(
                    ["node", "--check", abs_path],
                    capture_output=True, text=True, timeout=10,
                )
                if result.returncode != 0:
                    file_issues.append(result.stderr.strip())
            except Exception as exc:
                file_issues.append(str(exc))

        # Layer 2: heuristic analysis
        deep_issues = _js_deep_check(abs_path, rel)
        file_issues.extend(deep_issues)

        if file_issues:
            output_lines.append(f"FAIL: {rel}")
            output_lines.extend(file_issues)
            errors += 1
            files_with_issues.add(rel)
        else:
            output_lines.append(f"PASS: {rel}")
            passes += 1

    # ── Java ─────────────────────────────────────────────────────────
    for rel in java_files:
        abs_path = str(root / rel)
        file_issues = _java_static_check(abs_path, rel)

        if file_issues:
            output_lines.append(f"FAIL: {rel}")
            output_lines.extend(file_issues)
            errors += 1
            files_with_issues.add(rel)
        else:
            output_lines.append(f"PASS: {rel}")
            passes += 1

    # ── Source dump for LLM deep analysis (all files) ────────────────
    # Include ALL source files so the LLM classifier can catch logic
    # errors, type errors, and other issues not found by static checks.
    all_source_files = py_files + js_files + java_files
    source_blocks = _dump_source_for_llm(root, all_source_files, "ALL")
    output_lines.extend(source_blocks)

    # ── Summary ──────────────────────────────────────────────────────
    output_lines.append(f"\n{passes} passed, {errors} failed")
    test_output = "\n".join(output_lines)
    local_all_passed = errors == 0

    return ToolResult(
        tool_name="test_runner",
        status="success" if local_all_passed else "failure",
        summary=(
            f"Static analysis: {passes} passed, {errors} failed "
            f"out of {total} file(s)."
        ),
        outputs={
            "test_output": test_output,
            "exit_code": 1 if errors else 0,
            "passing_suites": passes,
            "failing_suites": errors,
            "local_all_passed": local_all_passed,
            "all_passed": False,  # only CI can confirm
            "test_results": [{
                "command": "static_analysis",
                "exit_code": 1 if errors else 0,
                "stdout": test_output,
                "stderr": "",
                "timed_out": False,
                "duration_s": 0.0,
                "success": local_all_passed,
            }],
            "test_commands": ["static_analysis"],
            "_static_analysis_fallback": True,
        },
    )


class TestRunnerTool(AgentTool):
    """Runs all discovered test suites and returns structured logs."""

    name = "test_runner"
    description = (
        "Discovers test frameworks in the repository, executes all test "
        "suites inside an isolated Docker sandbox, and returns raw logs, "
        "exit codes, and pass/fail counts."
    )
    input_keys = ["repo_path"]
    output_keys = [
        "test_output",
        "exit_code",
        "passing_suites",
        "failing_suites",
        "local_all_passed",
        "all_passed",
        "test_results",
        "test_commands",
    ]

    async def execute(self, state: dict[str, Any]) -> ToolResult:
        repo_path = state.get("repo_path", ".")

        logger.info(
            "[TestRunner] === EXECUTE START === | repo_path=%s | exists=%s",
            repo_path, Path(repo_path).exists(),
        )

        # Log what we see at repo_path
        root = Path(repo_path).resolve()
        if root.is_dir():
            top_level = sorted(os.listdir(root))
            logger.info(
                "[TestRunner] Top-level entries in repo (%d): %s",
                len(top_level), top_level[:30],
            )
        else:
            logger.error(
                "[TestRunner] repo_path is NOT a valid directory: %s", root
            )

        # ── Discover / reuse test commands ───────────────────
        commands: list[str] = state.get("test_commands", [])
        if not commands:
            logger.info("[TestRunner] No cached test_commands — running discovery…")
            discovery = discover_test_commands(repo_path)
            commands = discovery.commands
            logger.info(
                "[TestRunner] Discovery result: commands=%s | frameworks=%s",
                commands, [f.to_dict() for f in discovery.frameworks],
            )
        else:
            logger.info("[TestRunner] Reusing cached test_commands: %s", commands)

        if not commands:
            # ── Fallback: native static analysis (no Docker) ─────────
            logger.info(
                "[TestRunner] No test commands found — falling back to "
                "native static analysis."
            )
            # Log all source files we can find
            py_files, js_files, java_files = _collect_all_source_files(repo_path)
            logger.info(
                "[TestRunner] Source files found for static analysis: "
                "%d Python, %d JS, %d Java",
                len(py_files), len(js_files), len(java_files),
            )
            if py_files:
                logger.info("[TestRunner]   Python files: %s", py_files[:20])
            if js_files:
                logger.info("[TestRunner]   JS files: %s", js_files[:20])
            if java_files:
                logger.info("[TestRunner]   Java files: %s", java_files[:20])

            static_result = _run_native_static_analysis(repo_path)
            if static_result is not None:
                logger.info(
                    "[TestRunner] Static analysis complete: %s",
                    static_result.summary,
                )
                return static_result

            logger.warning(
                "[TestRunner] No test framework AND no source files detected "
                "in %s. The repo may be empty or have an unexpected structure.",
                repo_path,
            )
            return ToolResult(
                tool_name=self.name,
                status="skipped",
                summary="No test framework or source files detected.",
                outputs={
                    "local_all_passed": False,
                    "all_passed": False,
                    "test_output": "",
                    "test_commands": [],
                    "exit_code": 0,
                    "passing_suites": 0,
                    "failing_suites": 0,
                    "test_results": [],
                },
            )

        # ── Execute tests in sandbox (Docker) or locally (fallback) ───
        # Try Docker; if the daemon is unreachable, fall back to local subprocess.
        try:
            executor = _get_executor()
            # Probe Docker connectivity before committing to sandbox run
            executor.client.ping()
        except Exception as docker_exc:
            logger.warning(
                "[TestRunner] Docker unavailable (%s) — running tests locally "
                "via subprocess (no sandbox isolation).",
                docker_exc,
            )
            return _run_local_subprocess_tests(repo_path, commands)

        executor = _get_executor()
        install_deps = True

        combined_stdout = ""
        combined_stderr = ""
        max_exit = 0
        passing = 0
        failing = 0
        test_results: list[dict[str, Any]] = []

        for cmd in commands:
            # Auto-install runner if likely missing from requirements.txt
            if cmd.strip() == "pytest":
                cmd = "pytest"
            
            logger.info("[TestRunner] executing: %s", cmd)
            result = await executor.run_tests(
                repo_path=repo_path,
                test_command=cmd,
                install_deps=install_deps,
            )

            combined_stdout += result.stdout + "\n"
            combined_stderr += result.stderr + "\n"
            max_exit = max(max_exit, result.exit_code)

            test_results.append({
                "command": cmd,
                "exit_code": result.exit_code,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "timed_out": result.timed_out,
                "duration_s": result.duration_s,
                "success": result.success,
            })

            if result.success:
                passing += 1
            else:
                failing += 1

        local_all_passed = max_exit == 0
        test_output = combined_stdout
        if combined_stderr.strip():
            test_output += "\n--- STDERR ---\n" + combined_stderr

        return ToolResult(
            tool_name=self.name,
            status="success" if local_all_passed else "failure",
            summary=f"Ran {len(commands)} suite(s): {passing} passed, {failing} failed.",
            outputs={
                "test_output": test_output,
                "exit_code": max_exit,
                "passing_suites": passing,
                "failing_suites": failing,
                "local_all_passed": local_all_passed,
                "all_passed": False,  # only CI can set this to True
                "test_results": test_results,
                "test_commands": commands,
            },
        )


def _is_docker_available() -> bool:
    """Return True if Docker daemon is reachable."""
    try:
        from sandbox.executor import SandboxExecutor
        client = SandboxExecutor().client  # triggers docker.from_env()
        client.ping()
        return True
    except Exception:
        return False


def _get_executor():
    """Lazy import to avoid hard Docker dependency during testing."""
    _root = Path(__file__).resolve().parents[2]
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))
    from sandbox.executor import SandboxExecutor
    return SandboxExecutor()


def _run_local_subprocess_tests(
    repo_path: str,
    commands: list[str],
) -> ToolResult:
    """Run test commands directly via subprocess (no Docker).

    Used as a fallback when the Docker sandbox is unavailable.
    Installs dependencies from requirements.txt in a temp venv-like
    context (using the current Python interpreter), then runs each
    test command.
    """
    root = Path(repo_path).resolve()

    # Install dependencies first if requirements.txt exists
    req_file = root / "requirements.txt"
    if req_file.is_file():
        logger.info("[TestRunner/local] Installing deps from requirements.txt")
        try:
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "-q", "-r", str(req_file)],
                cwd=str(root),
                capture_output=True,
                timeout=120,
            )
        except Exception as exc:
            logger.warning("[TestRunner/local] pip install failed: %s", exc)

    combined_stdout = ""
    combined_stderr = ""
    max_exit = 0
    passing = 0
    failing = 0
    test_results: list[dict] = []

    for cmd in commands:
        logger.info("[TestRunner/local] Running: %s", cmd)
        try:
            # Resolve command: replace bare 'pytest' with sys.executable -m pytest
            if cmd.strip() == "pytest":
                run_cmd = [sys.executable, "-m", "pytest", "--tb=short", "-v"]
            elif cmd.strip().startswith("python -m unittest"):
                parts = cmd.strip().split()
                run_cmd = [sys.executable] + parts[1:]
            else:
                run_cmd = ["sh", "-c", cmd]

            result = subprocess.run(
                run_cmd,
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=120,
            )
            stdout = result.stdout
            stderr = result.stderr
            exit_code = result.returncode
            timed_out = False
        except subprocess.TimeoutExpired:
            stdout = ""
            stderr = "Test run timed out after 120s"
            exit_code = 1
            timed_out = True
        except Exception as exc:
            stdout = ""
            stderr = str(exc)
            exit_code = 1
            timed_out = False

        combined_stdout += stdout + "\n"
        combined_stderr += stderr + "\n"
        max_exit = max(max_exit, exit_code)

        test_results.append({
            "command": cmd,
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "timed_out": timed_out,
            "duration_s": 0.0,
            "success": exit_code == 0,
        })

        if exit_code == 0:
            passing += 1
        else:
            failing += 1

    local_all_passed = max_exit == 0
    test_output = combined_stdout
    if combined_stderr.strip():
        test_output += "\n--- STDERR ---\n" + combined_stderr

    logger.info(
        "[TestRunner/local] Done: %d passed, %d failed (exit=%d)",
        passing, failing, max_exit,
    )

    return ToolResult(
        tool_name="test_runner",
        status="success" if local_all_passed else "failure",
        summary=f"[LOCAL] Ran {len(commands)} suite(s): {passing} passed, {failing} failed.",
        outputs={
            "test_output": test_output,
            "exit_code": max_exit,
            "passing_suites": passing,
            "failing_suites": failing,
            "local_all_passed": local_all_passed,
            "all_passed": False,  # only CI can set this
            "test_results": test_results,
            "test_commands": commands,
        },
    )
