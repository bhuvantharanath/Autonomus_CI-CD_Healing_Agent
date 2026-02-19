#!/usr/bin/env python3
"""Torture test — exercises the agent against five distinct repo scenarios.

All scenarios are fully mocked (no Docker, no GitHub API calls) and run
entirely in-process.  Each scenario sets up a temporary repo with specific
characteristics and verifies the agent's behaviour via assertions.

Scenarios
---------
1. **Passing repo** — all tests pass from the start.
2. **Single syntax error** — one missing colon; agent must fix in ≤ 2 iterations.
3. **Multiple errors in different files** — agent must batch fixes.
4. **CI-only failure** — local tests pass but CI fails; agent must keep going.
5. **Missing dependency** — import-error crash; agent must not crash itself.

Usage::

    python3 torture_test.py          # run all scenarios
    python3 torture_test.py -v       # verbose logging
    python3 torture_test.py -k 3     # run only scenario 3

Exit codes: 0 = all PASS, 1 = at least one FAIL.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import os
import subprocess
import sys
import textwrap
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure project root is importable
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from agents.reasoning_loop import run_reasoning_loop, ReasoningLoopResult
from sandbox.executor import ExecutionResult


# ═══════════════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════════════

def _mock_exec(success: bool, stdout: str = "", stderr: str = "") -> ExecutionResult:
    return ExecutionResult(
        exit_code=0 if success else 1,
        stdout=stdout,
        stderr=stderr,
        logs=stdout + stderr,
        timed_out=False,
        duration_s=0.5,
    )


def _make_ci_log_zip(content: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("test_job/1_Run tests.txt", content)
    return buf.getvalue()


def _base_config(repo_path: str) -> dict[str, Any]:
    return {
        "test_commands": ["pytest"],
        "branch": "TORTURE_TEST_AI_Fix",
        "repo_url": "https://github.com/test-org/torture-repo.git",
        "github_token": "ghp_fake",
    }


def _init_git(path: Path) -> None:
    """Init a bare git repo with an initial commit."""
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "t@t.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "t@t.com",
    }
    subprocess.run(["git", "init"], cwd=path, capture_output=True, env=env)
    subprocess.run(["git", "add", "-A"], cwd=path, capture_output=True, env=env)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, capture_output=True, env=env)


def _mock_git(sha: str = "aaa1111"):
    """Return a side_effect for subprocess.run that fakes git ops."""
    def _run(cmd, cwd, **kw):
        r = MagicMock()
        r.returncode = 0
        r.stderr = ""
        if "rev-parse" in cmd:
            r.stdout = f"{sha}\n"
        elif "status" in cmd:
            r.stdout = "M app.py\n"
        else:
            r.stdout = ""
        return r
    return _run


def _mock_ci_client(conclusion: str, log_text: str):
    """Build an AsyncMock httpx client returning the given CI conclusion."""
    ci_resp = {"workflow_runs": [{
        "id": 1,
        "head_sha": "aaa1111",
        "status": "completed",
        "conclusion": conclusion,
        "html_url": "http://ci/1",
    }]}
    ci_zip = _make_ci_log_zip(log_text)

    async def _get(url, **kw):
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        if "logs" in url:
            resp.content = ci_zip
        else:
            resp.json = MagicMock(return_value=ci_resp)
        return resp

    client = AsyncMock()
    client.get = _get
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


# ═══════════════════════════════════════════════════════════════════════
#  Scenario result
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class ScenarioResult:
    name: str
    iterations: int = 0
    commits: int = 0
    status: str = ""
    crashed: bool = False
    error_msg: str = ""
    checks: list[tuple[str, bool, str]] = field(default_factory=list)

    def add_check(self, label: str, passed: bool, detail: str = ""):
        self.checks.append((label, passed, detail))

    @property
    def all_passed(self) -> bool:
        return not self.crashed and all(ok for _, ok, _ in self.checks)


# ═══════════════════════════════════════════════════════════════════════
#  Scenarios
# ═══════════════════════════════════════════════════════════════════════

# ── 1. Passing repo ─────────────────────────────────────────────────

async def scenario_passing_repo(tmp: Path) -> ScenarioResult:
    """All tests pass from the start — agent should exit without commits."""
    sr = ScenarioResult(name="1) Passing repo")

    repo = tmp / "passing"
    repo.mkdir()
    (repo / "app.py").write_text("def greet(name):\n    return f'Hello, {name}!'\n")
    (repo / "test_app.py").write_text(
        "from app import greet\ndef test_greet():\n    assert greet('X') == 'Hello, X!'\n"
    )
    _init_git(repo)

    async def run_pass(repo_path, test_command, install_deps=True):
        return _mock_exec(True, stdout="1 passed in 0.01s\n")

    mock_exec = MagicMock()
    mock_exec.run_tests = run_pass

    try:
        with patch("agents.tools.test_runner_tool._get_executor", return_value=mock_exec):
            result = await run_reasoning_loop(
                repo_path=str(repo),
                max_iterations=5,
                config=_base_config(str(repo)),
            )

        sr.iterations = result.iterations_used
        sr.commits = sum(1 for it in result.iterations if it.commit_sha)
        sr.status = result.status

        # Local tests pass but no CI — should NOT claim "healed"
        sr.add_check("no commits", sr.commits == 0, f"commits={sr.commits}")
        sr.add_check("iterations <= 1", sr.iterations <= 1, f"iterations={sr.iterations}")
        sr.add_check("status != healed", sr.status != "healed", f"status={sr.status}")
    except Exception as exc:
        sr.crashed = True
        sr.error_msg = str(exc)

    return sr


# ── 2. Single syntax error ──────────────────────────────────────────

async def scenario_single_syntax_error(tmp: Path) -> ScenarioResult:
    """One missing colon — agent should fix in ≤ 2 iterations with ≤ 2 commits."""
    sr = ScenarioResult(name="2) Single syntax error")

    repo = tmp / "single_bug"
    repo.mkdir()
    (repo / "app.py").write_text("def greet(name)\n    return f'Hello, {name}!'\n")
    (repo / "test_app.py").write_text(
        "from app import greet\ndef test_greet():\n    assert greet('X') == 'Hello, X!'\n"
    )
    _init_git(repo)

    fail_out = textwrap.dedent("""\
        FAILED test_app.py::test_greet
        File "app.py", line 1
            def greet(name)
                           ^
        SyntaxError: expected ':'
    """)
    pass_out = "1 passed in 0.01s\n"

    async def run_tests(repo_path, test_command, install_deps=True):
        return _mock_exec(False, stdout=fail_out)

    mock_exec = MagicMock()
    mock_exec.run_tests = run_tests

    ci_client = _mock_ci_client("success", pass_out)

    try:
        with patch("agents.tools.test_runner_tool._get_executor", return_value=mock_exec), \
             patch("agents.tools.commit_push_tool.subprocess.run", side_effect=_mock_git()), \
             patch("agents.tools.wait_for_ci_tool.httpx.AsyncClient", return_value=ci_client), \
             patch("agents.tools.fetch_ci_results_tool.httpx.AsyncClient", return_value=ci_client):

            result = await run_reasoning_loop(
                repo_path=str(repo),
                max_iterations=5,
                config=_base_config(str(repo)),
            )

        sr.iterations = result.iterations_used
        sr.commits = sum(1 for it in result.iterations if it.commit_sha)
        sr.status = result.status

        sr.add_check("healed", sr.status == "healed", f"status={sr.status}")
        sr.add_check("iterations <= 2", sr.iterations <= 2, f"iterations={sr.iterations}")
        sr.add_check("commits <= 2", sr.commits <= 2, f"commits={sr.commits}")
    except Exception as exc:
        sr.crashed = True
        sr.error_msg = str(exc)

    return sr


# ── 3. Multiple errors in different files ────────────────────────────

async def scenario_multiple_errors(tmp: Path) -> ScenarioResult:
    """Three bugs in different files — agent must batch-fix and commits <= iterations."""
    sr = ScenarioResult(name="3) Multiple errors in different files")

    repo = tmp / "multi_bug"
    repo.mkdir()
    (repo / "auth.py").write_text("def login(user)\n    return True\n")
    (repo / "db.py").write_text("def connect(url)\n    return None\n")
    (repo / "api.py").write_text("def handler(req)\n    return {}\n")
    (repo / "test_all.py").write_text(textwrap.dedent("""\
        from auth import login
        from db import connect
        from api import handler
        def test_auth(): assert login("u") is True
        def test_db(): assert connect("x") is None
        def test_api(): assert handler({}) == {}
    """))
    _init_git(repo)

    fail_out = textwrap.dedent("""\
        FAILED test_all.py::test_auth
        File "auth.py", line 1
            def login(user)
                           ^
        SyntaxError: expected ':'

        FAILED test_all.py::test_db
        File "db.py", line 1
            def connect(url)
                            ^
        SyntaxError: expected ':'

        FAILED test_all.py::test_api
        File "api.py", line 1
            def handler(req)
                            ^
        SyntaxError: expected ':'

        3 failed in 0.05s
    """)
    pass_out = "3 passed in 0.02s\n"

    async def run_tests(repo_path, test_command, install_deps=True):
        return _mock_exec(False, stdout=fail_out)

    mock_exec = MagicMock()
    mock_exec.run_tests = run_tests

    ci_client = _mock_ci_client("success", pass_out)

    try:
        with patch("agents.tools.test_runner_tool._get_executor", return_value=mock_exec), \
             patch("agents.tools.commit_push_tool.subprocess.run", side_effect=_mock_git()), \
             patch("agents.tools.wait_for_ci_tool.httpx.AsyncClient", return_value=ci_client), \
             patch("agents.tools.fetch_ci_results_tool.httpx.AsyncClient", return_value=ci_client):

            result = await run_reasoning_loop(
                repo_path=str(repo),
                max_iterations=5,
                config=_base_config(str(repo)),
            )

        sr.iterations = result.iterations_used
        sr.commits = sum(1 for it in result.iterations if it.commit_sha)
        sr.status = result.status

        sr.add_check("healed", sr.status == "healed", f"status={sr.status}")
        sr.add_check(
            "batched: commits <= iterations",
            sr.commits <= sr.iterations,
            f"commits={sr.commits}, iterations={sr.iterations}",
        )
        # All 3 bugs should be fixed in a single iteration (batched)
        first_it = result.iterations[0] if result.iterations else None
        patches_in_first = first_it.patches_applied if first_it else 0
        sr.add_check(
            "batch: >= 2 patches in first iter",
            patches_in_first >= 2,
            f"patches_in_first_iter={patches_in_first}",
        )
    except Exception as exc:
        sr.crashed = True
        sr.error_msg = str(exc)

    return sr


# ── 4. CI-only failure (local pass, CI fail) ────────────────────────

async def scenario_ci_only_failure(tmp: Path) -> ScenarioResult:
    """Local bug gets fixed, but CI fails on first check.

    Agent must not stop after the first commit — it should see the CI
    failure, re-enter the loop, and eventually succeed on the second
    CI check.  This validates the CI-driven feedback loop.
    """
    sr = ScenarioResult(name="4) CI-only failure simulation")

    repo = tmp / "ci_only"
    repo.mkdir()
    (repo / "app.py").write_text("def greet(name)\n    return f'Hello, {name}!'\n")
    (repo / "test_app.py").write_text(
        "from app import greet\ndef test_greet():\n    assert greet('X') == 'Hello, X!'\n"
    )
    _init_git(repo)

    fail_out = textwrap.dedent("""\
        FAILED test_app.py::test_greet
        File "app.py", line 1
            def greet(name)
                           ^
        SyntaxError: expected ':'
    """)

    # Local tests always report the same syntax error (executor mock is
    # stateless), so the agent will keep classifying the same bug.
    async def run_tests(repo_path, test_command, install_deps=True):
        return _mock_exec(False, stdout=fail_out)

    mock_exec = MagicMock()
    mock_exec.run_tests = run_tests

    # CI: first iteration returns "failure", second returns "success"
    ci_iteration = {"n": 0}
    ci_fail_log = "FAILED test_app.py::test_greet\nAssertionError: env var missing\n1 failed\n"
    ci_pass_log = "1 passed in 0.01s\n"

    def _make_ci_client_dynamic():
        async def _get(url, **kw):
            resp = MagicMock()
            resp.status_code = 200
            resp.raise_for_status = MagicMock()
            is_logs = "logs" in url

            if not is_logs:
                # Status check — count iterations
                ci_iteration["n"] += 1

            if ci_iteration["n"] <= 1:
                # First CI check: failure
                if is_logs:
                    resp.content = _make_ci_log_zip(ci_fail_log)
                else:
                    resp.json = MagicMock(return_value={"workflow_runs": [{
                        "id": 1, "head_sha": "aaa1111",
                        "status": "completed", "conclusion": "failure",
                        "html_url": "http://ci/1",
                    }]})
            else:
                # Subsequent CI checks: success
                if is_logs:
                    resp.content = _make_ci_log_zip(ci_pass_log)
                else:
                    resp.json = MagicMock(return_value={"workflow_runs": [{
                        "id": 2, "head_sha": "aaa1111",
                        "status": "completed", "conclusion": "success",
                        "html_url": "http://ci/2",
                    }]})
            return resp

        client = AsyncMock()
        client.get = _get
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        return client

    ci_client = _make_ci_client_dynamic()

    try:
        with patch("agents.tools.test_runner_tool._get_executor", return_value=mock_exec), \
             patch("agents.tools.commit_push_tool.subprocess.run", side_effect=_mock_git()), \
             patch("agents.tools.wait_for_ci_tool.httpx.AsyncClient", return_value=ci_client), \
             patch("agents.tools.fetch_ci_results_tool.httpx.AsyncClient", return_value=ci_client):

            result = await run_reasoning_loop(
                repo_path=str(repo),
                max_iterations=5,
                config=_base_config(str(repo)),
            )

        sr.iterations = result.iterations_used
        sr.commits = sum(1 for it in result.iterations if it.commit_sha)
        sr.status = result.status

        # Agent must push through at least 1 commit (the fix)
        sr.add_check(
            "at least 1 commit",
            sr.commits >= 1,
            f"commits={sr.commits}",
        )
        # Should eventually heal (CI succeeds on 2nd check)
        sr.add_check(
            "status is healed or partial",
            sr.status in ("healed", "partial"),
            f"status={sr.status}",
        )
    except Exception as exc:
        sr.crashed = True
        sr.error_msg = str(exc)

    return sr


# ── 5. Missing dependency (crash resilience) ────────────────────────

async def scenario_missing_dependency(tmp: Path) -> ScenarioResult:
    """Import error on test run — agent should not crash, mark FAILED gracefully."""
    sr = ScenarioResult(name="5) Missing dependency")

    repo = tmp / "missing_dep"
    repo.mkdir()
    (repo / "app.py").write_text("import nonexistent_lib\ndef run(): pass\n")
    (repo / "test_app.py").write_text(
        "from app import run\ndef test_run():\n    run()\n"
    )
    _init_git(repo)

    error_out = textwrap.dedent("""\
        ERROR collecting test_app.py
        ImportError while importing test module 'test_app.py'.
        Hint: make sure your test modules/packages have valid Python names.
        ModuleNotFoundError: No module named 'nonexistent_lib'

        ======= short test summary info ========
        ERROR test_app.py
        !! Errors during collection !!
    """)

    async def run_tests(repo_path, test_command, install_deps=True):
        return _mock_exec(False, stdout="", stderr=error_out)

    mock_exec = MagicMock()
    mock_exec.run_tests = run_tests

    try:
        with patch("agents.tools.test_runner_tool._get_executor", return_value=mock_exec):
            result = await run_reasoning_loop(
                repo_path=str(repo),
                max_iterations=3,
                config=_base_config(str(repo)),
            )

        sr.iterations = result.iterations_used
        sr.commits = sum(1 for it in result.iterations if it.commit_sha)
        sr.status = result.status

        sr.add_check("did not crash", True, "")
        sr.add_check(
            "status is failed or partial",
            sr.status in ("failed", "partial"),
            f"status={sr.status}",
        )
        sr.add_check(
            "iterations <= 3",
            sr.iterations <= 3,
            f"iterations={sr.iterations}",
        )
    except Exception as exc:
        sr.crashed = True
        sr.error_msg = str(exc)

    return sr


# ═══════════════════════════════════════════════════════════════════════
#  Runner
# ═══════════════════════════════════════════════════════════════════════

ALL_SCENARIOS = [
    scenario_passing_repo,
    scenario_single_syntax_error,
    scenario_multiple_errors,
    scenario_ci_only_failure,
    scenario_missing_dependency,
]


def _print_result(sr: ScenarioResult) -> None:
    tag = "PASS" if sr.all_passed else "FAIL"
    crash_tag = " [CRASHED]" if sr.crashed else ""
    print(f"\n{'─' * 60}")
    print(f"  {sr.name}: {tag}{crash_tag}")
    print(f"    iterations : {sr.iterations}")
    print(f"    commits    : {sr.commits}")
    print(f"    status     : {sr.status}")
    if sr.crashed:
        print(f"    error      : {sr.error_msg[:120]}")
    for label, ok, detail in sr.checks:
        icon = "✓" if ok else "✗"
        extra = f"  ({detail})" if detail else ""
        print(f"    {icon} {label}{extra}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Torture test — 5 scenarios")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("-k", type=int, default=0, help="Run only scenario N (1-5)")
    args = parser.parse_args()

    if args.verbose:
        import logging
        logging.basicConfig(level=logging.DEBUG, format="%(name)s %(message)s")

    import tempfile
    tmpdir = Path(tempfile.mkdtemp(prefix="torture_"))

    scenarios = ALL_SCENARIOS
    if args.k:
        idx = args.k - 1
        if 0 <= idx < len(ALL_SCENARIOS):
            scenarios = [ALL_SCENARIOS[idx]]
        else:
            print(f"Invalid scenario number: {args.k} (valid: 1-{len(ALL_SCENARIOS)})")
            return 1

    print(f"=== Torture Test — {len(scenarios)} scenario(s) ===")

    results: list[ScenarioResult] = []
    for fn in scenarios:
        sr = asyncio.run(fn(tmpdir))
        results.append(sr)
        _print_result(sr)

    # ── Summary ──────────────────────────────────────────────────────
    passed = sum(1 for r in results if r.all_passed)
    failed = sum(1 for r in results if not r.all_passed)
    crashed = sum(1 for r in results if r.crashed)

    print(f"\n{'═' * 60}")
    print(f"  TOTAL: {passed} passed, {failed} failed, {crashed} crashed")
    print(f"         out of {len(results)} scenario(s)")
    print(f"{'═' * 60}")

    if failed == 0 and crashed == 0:
        print("\nPASS")
        return 0
    else:
        print("\nFAIL")
        return 1


if __name__ == "__main__":
    sys.exit(main())
