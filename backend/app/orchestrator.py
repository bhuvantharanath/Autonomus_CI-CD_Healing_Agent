"""Background orchestrator – drives the CI-driven reasoning loop.

Workflow:
  1. Clone the repo via GitHubService
  2. Create a branch
  3. Run repo analysis (one-time)
  4. Run CI-driven reasoning loop (8 tools × up to 5 iterations)
     - Commit/push, CI wait, log fetch, verification all happen inside the loop
  5. Clean up temp directory
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

_MONOREPO_ROOT = Path(__file__).resolve().parents[2]
if str(_MONOREPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_MONOREPO_ROOT))

from agents.repo_analysis import RepoAnalysisAgent
from agents.reasoning_loop import run_reasoning_loop, ReasoningLoopResult
from agents.base import AgentResult
from shared.results_exporter import build_results, export_results

from app.store import RunState
from app.services.github_service import GitHubService, GitCommandError

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 5

# Map tool names emitted by the reasoning loop back to Phase names
_TOOL_TO_PHASE: dict[str, str] = {
    "test_runner":         "RUN_TESTS",
    "failure_classifier":  "CLASSIFY",
    "fix_planner":         "PLAN_FIX",
    "patch_applier":       "APPLY_PATCH",
    "commit_push":         "COMMIT_PUSH",
    "wait_for_ci":         "WAIT_FOR_CI",
    "fetch_ci_results":    "FETCH_CI_RESULTS",
    "verification":        "VERIFY",
}


async def execute_pipeline(state: RunState) -> None:
    """Run the full CI-driven pipeline, updating *state* as we go."""

    gh = GitHubService()
    clone_dir: Path | None = None
    pipeline_start = time.monotonic()

    context: dict[str, Any] = {
        "repo_url": state.repo_url,
        "repo_path": ".",
        "team_name": state.team_name,
        "leader_name": state.leader_name,
    }

    try:
        # ── 0. Check push access & fork if needed ─────────────────
        effective_repo_url = state.repo_url
        try:
            has_push = await asyncio.to_thread(gh.can_push, state.repo_url)
            if not has_push:
                state.push_progress(
                    "github", "started",
                    f"No push access to {state.repo_url} — forking…",
                )
                fork_url = await asyncio.to_thread(gh.fork_repo, state.repo_url)
                effective_repo_url = fork_url
                state.push_progress(
                    "github", "success",
                    f"Forked → {fork_url}",
                )
                # Wait for GitHub to propagate the fork (polls API)
                state.push_progress(
                    "github", "started",
                    "Waiting for fork to propagate on GitHub…",
                )
                fork_ready = await asyncio.to_thread(
                    gh.wait_for_fork_ready, fork_url, 30,
                )
                if not fork_ready:
                    state.push_progress(
                        "github", "warning",
                        "Fork may not be fully propagated — proceeding anyway.",
                    )
        except Exception as fork_exc:
            logger.warning("Fork check/creation failed: %s — will try pushing to original", fork_exc)

        context["repo_url"] = effective_repo_url

        # ── 1. Clone ──────────────────────────────────────────────
        state.push_progress("github", "started", f"Cloning {effective_repo_url}…")
        clone_dir = await asyncio.to_thread(gh.clone, effective_repo_url)
        context["repo_path"] = str(clone_dir)
        state.push_progress("github", "success", f"Cloned into {clone_dir}")
        # ── LOG: Inspect cloned repo contents ───────────────────
        _log_repo_contents(clone_dir, depth=2)
        # ── 2. Create branch ──────────────────────────────────────
        branch = await asyncio.to_thread(
            gh.create_branch, clone_dir, state.team_name, state.leader_name
        )
        context["branch"] = branch
        state.branch = branch
        state.push_progress("github", "success", f"Created branch {branch}")

        # ── 3. Repo analysis (one-time) ───────────────────────────
        repo_agent = RepoAnalysisAgent()
        state.push_progress(repo_agent.name, "started", "Scanning repository…")
        repo_result: AgentResult = await repo_agent.run(context)
        if repo_result.details:
            context.update(repo_result.details)
        state.push_progress(
            repo_agent.name, repo_result.status,
            repo_result.summary or "Repo analysis complete",
        )

        # ── 4. CI-driven reasoning loop ──────────────────────────
        state.max_iterations = MAX_ITERATIONS

        def progress_callback(agent: str, status: str, message: str) -> None:
            # Resolve the phase name and current iteration from the message
            phase = _TOOL_TO_PHASE.get(agent, agent.upper())
            iteration = state.current_iteration
            # The reasoning loop embeds "[iter N]" in messages — extract it
            if "[iter " in message:
                try:
                    iteration = int(message.split("[iter ")[1].split("]")[0])
                except (IndexError, ValueError):
                    pass
            state.update_step(phase, iteration, message)
            state.push_progress(agent, status, message)

        state.push_progress(
            "reasoning_loop", "started",
            f"Starting CI-driven reasoning loop (max {MAX_ITERATIONS} iterations)…",
        )

        loop_result: ReasoningLoopResult = await run_reasoning_loop(
            repo_path=context["repo_path"],
            max_iterations=MAX_ITERATIONS,
            config=context,
            on_progress=progress_callback,
        )

        runtime_seconds = time.monotonic() - pipeline_start
        state.runtime_seconds = runtime_seconds

        # Update live tracking from loop result
        state.update_iteration(
            iteration=loop_result.iterations_used,
            ci_status="PASSED" if loop_result.status == "healed" else "FAILED",
            failures=loop_result.total_bugs_found,
            fixes=loop_result.total_fixes_applied,
        )

        state.push_progress(
            "reasoning_loop",
            loop_result.status,
            f"Loop {loop_result.status}: "
            f"{loop_result.iterations_used} iteration(s), "
            f"{loop_result.total_fixes_applied} fix(es).",
        )

        # ── 5. Create Pull Request ────────────────────────────────
        if loop_result.total_fixes_applied > 0:
            try:
                state.push_progress("github", "started", "Creating Pull Request…")
                pr_result = await asyncio.to_thread(
                    gh.create_pull_request,
                    original_repo_url=state.repo_url,
                    fork_repo_url=effective_repo_url,
                    branch=context.get("branch", ""),
                    title=f"[AI-AGENT] {loop_result.total_fixes_applied} automated fix(es) — {state.team_name}",
                    body=(
                        f"## Automated Self-Healing Fixes\n\n"
                        f"**Team:** {state.team_name}\n"
                        f"**Leader:** {state.leader_name}\n"
                        f"**Branch:** `{context.get('branch', '')}` \n"
                        f"**Bugs detected:** {loop_result.total_bugs_found}\n"
                        f"**Fixes applied:** {loop_result.total_fixes_applied}\n"
                        f"**Iterations used:** {loop_result.iterations_used}/{MAX_ITERATIONS}\n\n"
                        f"All commits are prefixed with `[AI-AGENT]`.\n"
                    ),
                )
                pr_url = pr_result.get("pr_url", "")
                if pr_url:
                    state.push_progress("github", "success", f"PR created: {pr_url}")
                else:
                    state.push_progress("github", "warning", f"PR creation issue: {pr_result.get('error', 'unknown')}")
            except Exception as pr_exc:
                logger.warning("PR creation failed: %s", pr_exc)
                state.push_progress("github", "warning", f"PR creation failed: {pr_exc}")

        # ── 6. Export canonical results.json ──────────────────────
        memory_ref = loop_result._memory_ref
        # Collect git operation entries for inclusion in final results
        git_operations = [
            p for p in state.progress if p.get("agent") == "github"
        ]
        if memory_ref is not None:
            canonical = export_results(
                memory=memory_ref,
                repo_url=state.repo_url,
                branch=context.get("branch", "unknown"),
                team_name=state.team_name,
                leader_name=state.leader_name,
                runtime_seconds=runtime_seconds,
            )
            canonical["git_operations"] = git_operations
            state.final_results = canonical
        else:
            # Fallback: build a minimal results dict without RunMemory
            state.final_results = {
                "repository_url": state.repo_url,
                "branch": context.get("branch", "unknown"),
                "team_name": state.team_name,
                "leader_name": state.leader_name,
                "total_failures_detected": loop_result.total_bugs_found,
                "total_fixes_applied": loop_result.total_fixes_applied,
                "final_ci_status": "PASSED" if loop_result.status == "healed" else "FAILED",
                "runtime_seconds": round(runtime_seconds, 2),
                "failures_detected": [],
                "fixes": [],
                "ci_timeline": [],
                "git_operations": git_operations,
                "score": {"base": 100, "final_score": 100},
            }

        state.complete({
            "context": _serializable(context),
            "reasoning_loop": loop_result.to_dict(),
        })

    except GitCommandError as exc:
        logger.exception("Git operation failed")
        state.fail(f"Git error: {exc}")
    except Exception as exc:
        logger.exception("Pipeline failed")
        state.fail(str(exc))
    finally:
        if clone_dir is not None:
            gh.cleanup(clone_dir)


def _log_repo_contents(clone_dir: Path, depth: int = 2) -> None:
    """Walk the cloned repo and log its file tree (up to *depth* levels).

    This is critical for diagnosing "clone succeeds but files aren't found"
    issues.  Logs top-level entries and one level below, counting deeper files.
    """
    root = Path(clone_dir)
    if not root.is_dir():
        logger.error("[REPO-INSPECT] Clone dir does NOT exist: %s", root)
        return

    logger.info("[REPO-INSPECT] ═══ Cloned repo contents: %s ═══", root)

    total_files = 0
    total_dirs = 0
    file_extensions: dict[str, int] = {}

    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root)
        current_depth = len(rel_dir.parts)

        # Skip hidden dirs and common noise
        dirnames[:] = sorted(
            d for d in dirnames
            if not d.startswith(".") and d not in (
                "node_modules", "__pycache__", "venv", ".venv",
                ".git", "dist", "build", ".next"
            )
        )

        total_dirs += 1
        total_files += len(filenames)

        for fn in filenames:
            ext = Path(fn).suffix.lower()
            if ext:
                file_extensions[ext] = file_extensions.get(ext, 0) + 1

        # Only print detailed listing up to `depth` levels
        if current_depth < depth:
            indent = "  " * current_depth
            for d in dirnames:
                logger.info("[REPO-INSPECT] %s📁 %s/", indent, d)
            for f in filenames:
                logger.info("[REPO-INSPECT] %s📄 %s", indent, f)

    # Summary
    ext_summary = ", ".join(
        f"{ext}={cnt}" for ext, cnt in sorted(
            file_extensions.items(), key=lambda x: -x[1]
        )[:10]
    )
    logger.info(
        "[REPO-INSPECT] SUMMARY: %d files, %d dirs | extensions: %s",
        total_files, total_dirs, ext_summary or "(none)",
    )

    # Check for common project markers
    markers = [
        "requirements.txt", "pyproject.toml", "setup.py", "Pipfile",
        "package.json", "pom.xml", "build.gradle", "Cargo.toml",
        "go.mod", ".github/workflows",
    ]
    found_markers = []
    for m in markers:
        if (root / m).exists():
            found_markers.append(m)
    logger.info(
        "[REPO-INSPECT] Project markers found: %s",
        found_markers or "(none)",
    )

    # Check for test files
    test_files = []
    for p in root.rglob("*"):
        if p.is_file():
            name = p.name
            if (
                name.startswith("test_")
                or name.endswith("_test.py")
                or ".test." in name
                or ".spec." in name
                or "__tests__" in str(p)
            ):
                test_files.append(str(p.relative_to(root)))
    logger.info(
        "[REPO-INSPECT] Test files found (%d): %s",
        len(test_files), test_files[:20] if test_files else "(none)",
    )
    logger.info("[REPO-INSPECT] ═══ End repo inspection ═══")


def _serializable(obj: Any) -> Any:
    """Best-effort JSON-safe conversion."""
    if isinstance(obj, dict):
        return {k: _serializable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_serializable(i) for i in obj]
    if isinstance(obj, Path):
        return str(obj)
    try:
        import json
        json.dumps(obj)
        return obj
    except (TypeError, ValueError):
        return str(obj)
