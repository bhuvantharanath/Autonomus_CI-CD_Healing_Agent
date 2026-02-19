"""GitHub service – clone repos, create branches, commit & push fixes.

Uses PyGithub for the GitHub API and subprocess (git CLI) for local
repository operations.
"""

from __future__ import annotations

import logging
import os
import time
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from github import Github, GithubException

from app.config import settings

logger = logging.getLogger(__name__)


# ── Branch-name helpers ──────────────────────────────────────────────

def build_branch_name(team_name: str, leader_name: str) -> str:
    """Build a branch name from the team name and leader's name.

    Rules:
    - All UPPERCASE
    - Spaces → underscores
    - Strip non-alphanumeric (except underscore)
    - Format: TEAM_NAME_LEADER_NAME_AI_Fix

    Examples:
        ("RIFT ORGANISERS", "Saiyam Kumar")  → "RIFT_ORGANISERS_SAIYAM_KUMAR_AI_Fix"
        ("Code Warriors", "John Doe")        → "CODE_WARRIORS_JOHN_DOE_AI_Fix"
    """
    def _clean(raw: str) -> str:
        s = raw.strip().upper()
        s = s.replace(" ", "_").replace("-", "_")
        s = re.sub(r"[^A-Z0-9_]", "", s)
        s = re.sub(r"_+", "_", s).strip("_")
        return s

    team = _clean(team_name)
    leader = _clean(leader_name)
    name = f"{team}_{leader}_AI_Fix"
    return name


# ── Git CLI wrapper ──────────────────────────────────────────────────

def _run_git(args: list[str], cwd: str | Path) -> subprocess.CompletedProcess:
    """Run a git command and return the result."""
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
        logger.error("git %s failed: %s", " ".join(args), result.stderr.strip())
        raise GitCommandError(cmd, result.returncode, result.stderr.strip())
    return result


class GitCommandError(Exception):
    """Raised when a git subprocess exits with a non-zero code."""

    def __init__(self, cmd: list[str], code: int, stderr: str):
        self.cmd = cmd
        self.code = code
        self.stderr = stderr
        super().__init__(f"git {' '.join(cmd[1:])} failed (exit {code}): {stderr}")


# ── GitHub service class ─────────────────────────────────────────────

class GitHubService:
    """High-level helper for cloning, branching, committing, and pushing."""

    def __init__(self, token: str | None = None):
        self.token = token or settings.GITHUB_TOKEN
        self._gh: Github | None = None

    # -- PyGithub client (lazy) ----------------------------------------

    @property
    def gh(self) -> Github:
        if self._gh is None:
            if not self.token:
                raise ValueError("GITHUB_TOKEN is not set")
            self._gh = Github(self.token)
        return self._gh

    # -- Clone ----------------------------------------------------------

    def clone(self, repo_url: str, dest: str | Path | None = None) -> Path:
        """Clone *repo_url* into a temporary directory (or *dest*).

        If a token is available the URL is rewritten to use HTTPS auth so
        private repos work out of the box.
        """
        if dest is None:
            dest = Path(tempfile.mkdtemp(prefix="heal_"))
        else:
            dest = Path(dest)
            dest.mkdir(parents=True, exist_ok=True)

        auth_url = self._authenticated_url(repo_url)
        logger.info(
            "[GitHubService] Cloning repo | url=%s | dest=%s",
            repo_url, dest,
        )
        _run_git(["clone", auth_url, str(dest)], cwd=dest.parent)
        logger.info("Cloned %s → %s", repo_url, dest)

        # Log what we got after clone
        import os
        if dest.is_dir():
            top_level = sorted(os.listdir(dest))
            logger.info(
                "[GitHubService] Post-clone contents (%d entries): %s",
                len(top_level), top_level[:30],
            )
        else:
            logger.error(
                "[GitHubService] Clone dir does NOT exist after clone: %s",
                dest,
            )

        return dest

    # -- Fork -----------------------------------------------------------

    def fork_repo(self, repo_url: str) -> str:
        """Fork the repository into the authenticated user's account.

        Returns the clone URL of the fork.  If the fork already exists,
        returns it immediately.
        """
        owner_repo = self._extract_owner_repo(repo_url)
        try:
            source_repo = self.gh.get_repo(owner_repo)
        except GithubException as exc:
            raise GitCommandError(
                ["github", "get_repo"], 1,
                f"Cannot access {owner_repo}: {exc}",
            )

        user = self.gh.get_user()
        fork_full_name = f"{user.login}/{source_repo.name}"

        # Check if fork already exists
        try:
            existing = self.gh.get_repo(fork_full_name)
            clone_url = existing.clone_url
            logger.info("Fork already exists: %s", clone_url)
            return clone_url
        except GithubException:
            pass

        # Create fork
        try:
            fork = user.create_fork(source_repo)
            clone_url = fork.clone_url
            logger.info("Forked %s → %s", owner_repo, clone_url)
            return clone_url
        except GithubException as exc:
            raise GitCommandError(
                ["github", "create_fork"], 1,
                f"Failed to fork {owner_repo}: {exc}",
            )

    def wait_for_fork_ready(self, fork_url: str, timeout: int = 30) -> bool:
        """Poll the GitHub API until the fork is accessible.

        GitHub can take 5-30 seconds to fully propagate a new fork.
        Returns True if the fork is ready, False if timeout exceeded.
        """
        fork_owner_repo = self._extract_owner_repo(fork_url)
        start = time.monotonic()
        interval = 3

        while (time.monotonic() - start) < timeout:
            try:
                repo = self.gh.get_repo(fork_owner_repo)
                # Verify it's not empty — check default branch exists
                if repo.default_branch:
                    logger.info(
                        "Fork %s is ready (waited %.1fs)",
                        fork_owner_repo, time.monotonic() - start,
                    )
                    return True
            except GithubException:
                pass
            logger.info(
                "Waiting for fork %s to propagate (%.0fs/%ds)…",
                fork_owner_repo, time.monotonic() - start, timeout,
            )
            time.sleep(interval)
            interval = min(interval + 2, 10)

        logger.warning("Fork %s not ready after %ds", fork_owner_repo, timeout)
        return False

    def verify_branch_exists(self, repo_url: str, branch: str) -> bool:
        """Check if a branch exists on a remote GitHub repo."""
        owner_repo = self._extract_owner_repo(repo_url)
        try:
            repo = self.gh.get_repo(owner_repo)
            repo.get_branch(branch)
            logger.info("Branch '%s' verified on %s", branch, owner_repo)
            return True
        except GithubException:
            logger.warning("Branch '%s' NOT found on %s", branch, owner_repo)
            return False

    def can_push(self, repo_url: str) -> bool:
        """Check if the authenticated user has push access to the repo."""
        owner_repo = self._extract_owner_repo(repo_url)
        try:
            repo = self.gh.get_repo(owner_repo)
            perms = repo.permissions
            return perms is not None and (perms.push or perms.admin)
        except GithubException:
            return False

    @staticmethod
    def _extract_owner_repo(url: str) -> str:
        """Extract 'owner/repo' from a GitHub URL."""
        # https://github.com/owner/repo.git → owner/repo
        url = url.rstrip("/")
        if url.endswith(".git"):
            url = url[:-4]
        parts = url.split("github.com/")
        if len(parts) == 2:
            return parts[1]
        raise ValueError(f"Cannot extract owner/repo from: {url}")

    # -- Branch ---------------------------------------------------------

    def create_branch(self, repo_dir: str | Path, team_name: str, leader_name: str) -> str:
        """Create and checkout a new branch using the naming convention."""
        branch = build_branch_name(team_name, leader_name)
        logger.info(
            "[GitHubService] Creating branch '%s' in %s", branch, repo_dir
        )
        _run_git(["checkout", "-b", branch], cwd=repo_dir)
        logger.info("Created branch %s in %s", branch, repo_dir)

        # Verify the repo is still valid
        import os
        top = sorted(os.listdir(repo_dir))
        logger.info(
            "[GitHubService] After branch checkout, dir has %d entries: %s",
            len(top), top[:20],
        )
        return branch

    # -- Commit ---------------------------------------------------------

    def commit(
        self,
        repo_dir: str | Path,
        message: str,
        add_all: bool = True,
    ) -> str:
        """Stage changes and commit with the [AI-AGENT] prefix.

        Returns the short commit SHA.
        """
        if add_all:
            _run_git(["add", "-A"], cwd=repo_dir)

        full_message = f"[AI-AGENT] {message}"
        _run_git(["commit", "-m", full_message], cwd=repo_dir)

        sha = _run_git(
            ["rev-parse", "--short", "HEAD"], cwd=repo_dir
        ).stdout.strip()
        logger.info("Committed %s: %s", sha, full_message)
        return sha

    # -- Push -----------------------------------------------------------

    def push(self, repo_dir: str | Path, branch: str) -> None:
        """Push *branch* to origin."""
        _run_git(["push", "-u", "origin", branch], cwd=repo_dir)
        logger.info("Pushed branch %s", branch)

    # -- Pull Request ---------------------------------------------------

    def create_pull_request(
        self,
        original_repo_url: str,
        fork_repo_url: str | None,
        branch: str,
        title: str | None = None,
        body: str | None = None,
        max_retries: int = 3,
    ) -> dict:
        """Create a Pull Request from the AI-fix branch to the original repo.

        If the code was pushed to a fork, the PR head is 'fork_owner:branch'.
        If pushed directly to the original repo, the PR head is just 'branch'.

        Includes retry logic with exponential backoff to handle GitHub
        propagation delays (especially after fork + push).

        Returns a dict with pr_number, pr_url, and pr_state.
        """
        original_owner_repo = self._extract_owner_repo(original_repo_url)
        target_repo = self.gh.get_repo(original_owner_repo)
        default_branch = target_repo.default_branch

        # Determine head reference
        is_fork = fork_repo_url and fork_repo_url != original_repo_url
        if is_fork:
            fork_owner_repo = self._extract_owner_repo(fork_repo_url)
            fork_owner = fork_owner_repo.split("/")[0]
            head = f"{fork_owner}:{branch}"
        else:
            head = branch

        if title is None:
            title = f"[AI-AGENT] Automated fixes on branch {branch}"
        if body is None:
            body = (
                f"## Automated Self-Healing Fixes\n\n"
                f"This PR was created automatically by the AI DevOps Agent.\n\n"
                f"- **Branch:** `{branch}`\n"
                f"- All commits are prefixed with `[AI-AGENT]`\n"
                f"- Fixes were detected, applied, and verified autonomously\n"
            )

        # If pushing to a fork, verify the branch exists on the fork first
        if is_fork:
            source_url = fork_repo_url
            logger.info(
                "[PR] Cross-fork PR: head=%s → base=%s:%s",
                head, original_owner_repo, default_branch,
            )
            # Wait for the branch to be visible on GitHub after push
            for wait_attempt in range(5):
                if self.verify_branch_exists(source_url, branch):
                    break
                logger.info(
                    "[PR] Branch '%s' not yet visible on fork, "
                    "waiting %ds (attempt %d/5)…",
                    branch, (wait_attempt + 1) * 3, wait_attempt + 1,
                )
                time.sleep((wait_attempt + 1) * 3)
            else:
                logger.warning(
                    "[PR] Branch '%s' still not visible after waiting — "
                    "attempting PR creation anyway.",
                    branch,
                )

        # Check if a PR already exists for this head
        try:
            existing_prs = target_repo.get_pulls(
                state="open", head=head,
            )
            for pr in existing_prs:
                logger.info("PR already exists: #%d %s", pr.number, pr.html_url)
                return {
                    "pr_number": pr.number,
                    "pr_url": pr.html_url,
                    "pr_state": pr.state,
                    "created": False,
                }
        except GithubException as exc:
            logger.warning("Error checking existing PRs: %s", exc)

        # Create PR with retry logic
        last_error = None
        for attempt in range(1, max_retries + 1):
            try:
                pr = target_repo.create_pull(
                    title=title,
                    body=body,
                    head=head,
                    base=default_branch,
                )
                logger.info(
                    "Created PR #%d: %s (attempt %d)",
                    pr.number, pr.html_url, attempt,
                )
                return {
                    "pr_number": pr.number,
                    "pr_url": pr.html_url,
                    "pr_state": pr.state,
                    "created": True,
                }
            except GithubException as exc:
                last_error = exc
                logger.warning(
                    "PR creation attempt %d/%d failed: %s",
                    attempt, max_retries, exc,
                )
                if attempt < max_retries:
                    backoff = attempt * 5  # 5s, 10s
                    logger.info("Retrying PR creation in %ds…", backoff)
                    time.sleep(backoff)

        logger.error(
            "[PR] All %d attempts failed. Last error: %s",
            max_retries, last_error,
        )
        return {
            "pr_number": None,
            "pr_url": None,
            "pr_state": "error",
            "error": str(last_error),
            "created": False,
        }

    # -- Full workflow shortcut ----------------------------------------

    def clone_branch_commit_push(
        self,
        repo_url: str,
        team_name: str,
        leader_name: str,
        commit_message: str,
        apply_changes: Optional[callable] = None,
    ) -> dict:
        """One-shot helper: clone → branch → (apply changes) → commit → push.

        Parameters:
            repo_url:       GitHub clone URL.
            team_name:      Team name for branch naming.
            leader_name:    Used to build the branch name.
            commit_message: Commit body (auto-prefixed with [AI-AGENT]).
            apply_changes:  Optional callable(repo_dir: Path) that modifies
                            files before commit.  If None, the caller is
                            expected to have made changes already.

        Returns a dict with clone_dir, branch, and commit_sha.
        """
        clone_dir = self.clone(repo_url)
        branch = self.create_branch(clone_dir, team_name, leader_name)

        if apply_changes is not None:
            apply_changes(clone_dir)

        sha = self.commit(clone_dir, commit_message)
        self.push(clone_dir, branch)

        return {
            "clone_dir": str(clone_dir),
            "branch": branch,
            "commit_sha": sha,
        }

    # -- Cleanup --------------------------------------------------------

    @staticmethod
    def cleanup(repo_dir: str | Path) -> None:
        """Remove a cloned repo directory."""
        shutil.rmtree(str(repo_dir), ignore_errors=True)

    # -- Repo metadata via PyGithub ------------------------------------

    def get_repo_info(self, owner_repo: str) -> dict:
        """Return basic metadata for *owner_repo* (e.g. 'octocat/Hello-World')."""
        try:
            repo = self.gh.get_repo(owner_repo)
            return {
                "full_name": repo.full_name,
                "default_branch": repo.default_branch,
                "language": repo.language,
                "open_issues": repo.open_issues_count,
                "stars": repo.stargazers_count,
                "private": repo.private,
            }
        except GithubException as exc:
            logger.error("PyGithub error for %s: %s", owner_repo, exc)
            raise

    # -- Internal -------------------------------------------------------

    def _authenticated_url(self, url: str) -> str:
        """Inject the token into an HTTPS GitHub URL for private repos."""
        if not self.token:
            return url
        # https://github.com/owner/repo.git → https://<token>@github.com/owner/repo.git
        if url.startswith("https://github.com"):
            return url.replace("https://github.com", f"https://{self.token}@github.com")
        return url
