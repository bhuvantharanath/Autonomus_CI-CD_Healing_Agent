"""CommitPushTool — commits fixes with [AI-AGENT] prefix and pushes to branch.

Tool 6 in the CI-driven reasoning loop. Stages all changes, creates a
commit with a structured message, and pushes to the AI-fix branch.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path
from typing import Any

from agents.tools.registry import AgentTool, ToolResult

logger = logging.getLogger(__name__)


class CommitPushTool(AgentTool):
    """Commits patched files and pushes to the AI-fix branch."""

    name = "commit_push"
    description = (
        "Stages all modified files, commits with '[AI-AGENT]' prefix, "
        "and pushes to the current AI-fix branch on origin."
    )
    input_keys = ["repo_path", "branch"]
    output_keys = ["commit_sha", "commit_message", "push_status"]

    async def execute(self, state: dict[str, Any]) -> ToolResult:
        repo_path = Path(state.get("repo_path", "."))
        branch = state.get("branch", "")
        iteration = state.get("_current_iteration", 1)
        applied_count = state.get("applied_count", 0)

        logger.info(
            "[CommitPush] === EXECUTE START === | repo_path=%s | branch=%s | "
            "iteration=%d | applied_count=%d | path_exists=%s",
            repo_path, branch, iteration, applied_count, repo_path.exists(),
        )

        # Log git status before committing
        try:
            pre_status = _git(["status", "--short"], cwd=repo_path).stdout.strip()
            logger.info(
                "[CommitPush] Git status before commit:\n%s",
                pre_status or "(clean - no changes)",
            )
        except Exception as e:
            logger.warning("[CommitPush] Could not get git status: %s", e)

        if not branch:
            return ToolResult(
                tool_name=self.name,
                status="failure",
                summary="No branch name in state — cannot push.",
                outputs={"commit_sha": "", "commit_message": "", "push_status": "failed"},
                errors=["Missing 'branch' in workflow state."],
            )

        # ── Build batched commit message ──────────────────────────────
        applied_patches = state.get("applied_patches", [])
        n_patches = len(applied_patches)

        patch_summaries = []
        for p in applied_patches:
            desc = p.get("description", "fix")
            f = Path(p.get("file", "unknown")).name
            patch_summaries.append(f"  - {f}: {desc}")

        header = f"[AI-AGENT] Iteration {iteration}: {n_patches} fix(es) applied"
        # Include up to 8 individual summaries; truncate if more
        body_lines = patch_summaries[:8]
        if n_patches > 8:
            body_lines.append(f"  ... and {n_patches - 8} more")
        commit_msg = header + ("\n\n" + "\n".join(body_lines) if body_lines else "")

        try:
            # ── Stage ────────────────────────────────────────────────
            _git(["add", "-A"], cwd=repo_path)

            # Check if there's anything to commit
            status_out = _git(["status", "--porcelain"], cwd=repo_path).stdout.strip()
            if not status_out:
                return ToolResult(
                    tool_name=self.name,
                    status="skipped",
                    summary="No changes to commit (working tree clean).",
                    outputs={
                        "commit_sha": "",
                        "commit_message": commit_msg,
                        "push_status": "skipped",
                    },
                )

            # ── Commit ───────────────────────────────────────────────
            _git(["commit", "-m", commit_msg], cwd=repo_path)
            sha = _git(
                ["rev-parse", "--short", "HEAD"], cwd=repo_path
            ).stdout.strip()

            # ── Push ─────────────────────────────────────────────────
            try:
                _git(["push", "-u", "origin", branch], cwd=repo_path)
                push_status = "success"
                logger.info("Committed %s and pushed to %s", sha, branch)
            except subprocess.CalledProcessError as push_exc:
                push_err = push_exc.stderr.strip() if push_exc.stderr else str(push_exc)
                push_status = "push_failed"
                logger.warning(
                    "Commit %s succeeded but push failed: %s — "
                    "fixes are applied locally but not pushed.",
                    sha, push_err,
                )

            return ToolResult(
                tool_name=self.name,
                status="success" if push_status == "success" else "partial",
                summary=(
                    f"Committed {sha} and pushed to {branch}."
                    if push_status == "success"
                    else f"Committed {sha} locally but push to {branch} failed."
                ),
                outputs={
                    "commit_sha": sha,
                    "commit_message": commit_msg,
                    "push_status": push_status,
                },
                errors=[push_err] if push_status == "push_failed" else [],
            )

        except subprocess.CalledProcessError as exc:
            err = exc.stderr.strip() if exc.stderr else str(exc)
            logger.error("Git operation failed: %s", err)
            return ToolResult(
                tool_name=self.name,
                status="failure",
                summary=f"Git failed: {err}",
                outputs={
                    "commit_sha": "",
                    "commit_message": commit_msg,
                    "push_status": "failed",
                },
                errors=[err],
            )
        except Exception as exc:
            logger.exception("CommitPush failed")
            return ToolResult(
                tool_name=self.name,
                status="failure",
                summary=str(exc),
                outputs={
                    "commit_sha": "",
                    "commit_message": "",
                    "push_status": "failed",
                },
                errors=[str(exc)],
            )


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    """Run a git command."""
    cmd = ["git"] + args
    logger.debug("git %s  (cwd=%s)", " ".join(args), cwd)
    result = subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode, cmd, result.stdout, result.stderr
        )
    return result
