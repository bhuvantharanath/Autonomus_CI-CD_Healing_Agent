"""Repo Analysis Agent – scans repository structure, languages, and dependencies."""

from __future__ import annotations

import os
import logging
from pathlib import Path
from typing import Any

from agents.base import AgentResult, BaseAgent

logger = logging.getLogger(__name__)


class RepoAnalysisAgent(BaseAgent):
    """Analyzes repository structure, tech stack, and code metrics."""

    name = "repo_analysis"

    async def run(self, context: dict[str, Any]) -> AgentResult:
        repo_path = Path(context.get("repo_path", "."))
        logger.info(
            "[RepoAnalysis] Starting scan | repo_path=%s | exists=%s | is_dir=%s",
            repo_path, repo_path.exists(), repo_path.is_dir(),
        )

        if not repo_path.exists():
            logger.error(
                "[RepoAnalysis] ABORT — repo path does NOT exist: %s", repo_path
            )
            return AgentResult(
                agent_name=self.name,
                status="failure",
                errors=[f"Repository path does not exist: {repo_path}"],
            )

        # List top-level contents for diagnostics
        if repo_path.is_dir():
            top_level = sorted(os.listdir(repo_path))
            logger.info(
                "[RepoAnalysis] Top-level entries (%d): %s",
                len(top_level), top_level[:30],
            )
        else:
            logger.error(
                "[RepoAnalysis] repo_path is NOT a directory: %s", repo_path
            )

        structure = self._scan_structure(repo_path)
        languages = self._detect_languages(repo_path)
        dep_files = self._find_dependency_files(repo_path)

        logger.info(
            "[RepoAnalysis] Scan complete | files=%d | dirs=%d | "
            "languages=%s | dep_files=%s",
            structure["total_files"],
            structure["total_dirs"],
            languages,
            dep_files,
        )

        return AgentResult(
            agent_name=self.name,
            status="success",
            summary=f"Scanned {structure['total_files']} files across {structure['total_dirs']} directories.",
            details={
                "structure": structure,
                "languages": languages,
                "dependency_files": dep_files,
            },
        )

    def _scan_structure(self, root: Path) -> dict:
        total_files = 0
        total_dirs = 0
        for dirpath, dirnames, filenames in os.walk(root):
            # Skip hidden/venv dirs
            dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d not in ("node_modules", "__pycache__", "venv", ".venv"))
            total_dirs += 1
            total_files += len(filenames)
        return {"total_files": total_files, "total_dirs": total_dirs}

    def _detect_languages(self, root: Path) -> dict[str, int]:
        ext_map: dict[str, int] = {}
        for p in sorted(root.rglob("*")):
            if p.is_file() and p.suffix:
                ext_map[p.suffix] = ext_map.get(p.suffix, 0) + 1
        return dict(sorted(ext_map.items(), key=lambda x: -x[1])[:10])

    def _find_dependency_files(self, root: Path) -> list[str]:
        markers = [
            "requirements.txt", "pyproject.toml", "setup.py", "Pipfile",
            "package.json", "pom.xml", "build.gradle", "Cargo.toml", "go.mod",
        ]
        found = []
        for m in markers:
            hits = sorted(root.rglob(m))
            found.extend(str(h.relative_to(root)) for h in hits)
        return found
