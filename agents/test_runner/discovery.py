"""Test Discovery – analyzes repository structure to detect test frameworks
and returns the exact command(s) needed to run them.

Supported frameworks
────────────────────
Python:  pytest, unittest
Node.js: jest, vitest, mocha (+ generic ``npm test``)

Usage::

    from agents.test_runner.discovery import discover_test_commands

    commands = discover_test_commands("/path/to/repo")
    # e.g. ["pytest", "npx vitest run"]
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ── Result dataclass ─────────────────────────────────────────────────

@dataclass
class TestFrameworkMatch:
    """One detected test framework."""

    framework: str        # e.g. "pytest", "jest", "vitest", "unittest"
    command: str           # command to execute
    confidence: float      # 0.0 – 1.0
    evidence: list[str] = field(default_factory=list)  # why we think so

    def to_dict(self) -> dict[str, Any]:
        return {
            "framework": self.framework,
            "command": self.command,
            "confidence": round(self.confidence, 2),
            "evidence": self.evidence,
        }


@dataclass
class DiscoveryResult:
    """Aggregated discovery output."""

    commands: list[str]
    frameworks: list[TestFrameworkMatch]

    def to_dict(self) -> dict[str, Any]:
        return {
            "commands": self.commands,
            "frameworks": [f.to_dict() for f in self.frameworks],
        }


# ── Ignore list ──────────────────────────────────────────────────────

_SKIP_DIRS = {
    "node_modules", "__pycache__", ".git", ".venv", "venv",
    "env", ".tox", ".mypy_cache", ".pytest_cache", "dist",
    "build", ".next", "coverage",
}


# ── Public API ───────────────────────────────────────────────────────

def discover_test_commands(repo_path: str | Path) -> DiscoveryResult:
    """Scan *repo_path* and return the best test commands to run.

    The function inspects config files, dependency lists, directory
    naming conventions, and file contents to detect which test
    frameworks are present.
    """
    root = Path(repo_path).resolve()
    if not root.is_dir():
        logger.warning("Repository path does not exist: %s", root)
        return DiscoveryResult(commands=[], frameworks=[])

    ctx = _build_scan_context(root)

    detectors = [
        _detect_pytest,
        _detect_unittest,
        _detect_jest,
        _detect_vitest,
    ]

    matches: list[TestFrameworkMatch] = []
    for detector in detectors:
        match = detector(root, ctx)
        if match is not None:
            matches.append(match)

    # Sort by confidence (highest first) and deduplicate commands
    matches.sort(key=lambda m: -m.confidence)
    seen_commands: set[str] = set()
    unique: list[TestFrameworkMatch] = []
    for m in matches:
        if m.command not in seen_commands:
            seen_commands.add(m.command)
            unique.append(m)

    commands = [m.command for m in unique]
    logger.info("Discovered %d framework(s) in %s: %s", len(unique), root, commands)

    return DiscoveryResult(commands=commands, frameworks=unique)


# ── Scan context (cached file lookups) ───────────────────────────────

@dataclass
class _ScanContext:
    """Pre-collected file info so detectors don't re-walk the tree."""

    root: Path
    all_files: list[Path]
    py_files: list[Path]
    js_ts_files: list[Path]
    config_files: dict[str, Path]          # filename → path
    package_json: dict[str, Any] | None
    pyproject_toml_text: str | None
    requirements_txt_text: str | None


def _build_scan_context(root: Path) -> _ScanContext:
    all_files: list[Path] = []
    py_files: list[Path] = []
    js_ts_files: list[Path] = []
    config_files: dict[str, Path] = {}

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS and not d.startswith("."))
        for fn in sorted(filenames):
            p = Path(dirpath) / fn
            all_files.append(p)
            if p.suffix == ".py":
                py_files.append(p)
            elif p.suffix in (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"):
                js_ts_files.append(p)
            # Track well-known config files at any depth
            if fn in (
                "package.json", "pyproject.toml", "requirements.txt",
                "setup.cfg", "pytest.ini", "tox.ini", "conftest.py",
                "jest.config.js", "jest.config.ts", "jest.config.mjs", "jest.config.cjs",
                "vitest.config.js", "vitest.config.ts", "vitest.config.mts",
                "vite.config.ts", "vite.config.js",
                ".nycrc", ".mocharc.yml", ".mocharc.json",
            ):
                config_files.setdefault(fn, p)

    logger.info(
        "[Discovery] Scan context built | total_files=%d | py=%d | js_ts=%d "
        "| config_files=%s",
        len(all_files), len(py_files), len(js_ts_files),
        list(config_files.keys()),
    )

    if not all_files:
        logger.warning(
            "[Discovery] WARNING — no files found under %s! "
            "The cloned repo may be empty or the path may be wrong.",
            root,
        )

    # Parse package.json
    pkg_json: dict[str, Any] | None = None
    pkg_path = config_files.get("package.json") or (root / "package.json")
    if pkg_path.is_file():
        try:
            pkg_json = json.loads(pkg_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    # Read pyproject.toml
    pyproject_text: str | None = None
    pyproject_path = config_files.get("pyproject.toml") or (root / "pyproject.toml")
    if pyproject_path.is_file():
        try:
            pyproject_text = pyproject_path.read_text(encoding="utf-8")
        except Exception:
            pass

    # Read requirements.txt
    req_text: str | None = None
    req_path = config_files.get("requirements.txt") or (root / "requirements.txt")
    if req_path.is_file():
        try:
            req_text = req_path.read_text(encoding="utf-8")
        except Exception:
            pass

    return _ScanContext(
        root=root,
        all_files=all_files,
        py_files=py_files,
        js_ts_files=js_ts_files,
        config_files=config_files,
        package_json=pkg_json,
        pyproject_toml_text=pyproject_text,
        requirements_txt_text=req_text,
    )


# ── Individual detectors ─────────────────────────────────────────────


def _detect_pytest(root: Path, ctx: _ScanContext) -> TestFrameworkMatch | None:
    """Detect pytest via config files, deps, and test file patterns."""
    evidence: list[str] = []
    confidence = 0.0

    # 1) conftest.py exists
    if "conftest.py" in ctx.config_files:
        evidence.append("conftest.py found")
        confidence += 0.4

    # 2) pytest.ini or [tool.pytest] in pyproject.toml
    if "pytest.ini" in ctx.config_files:
        evidence.append("pytest.ini found")
        confidence += 0.3
    if ctx.pyproject_toml_text and "[tool.pytest" in ctx.pyproject_toml_text:
        evidence.append("[tool.pytest] section in pyproject.toml")
        confidence += 0.3

    # 3) setup.cfg with [tool:pytest]
    setup_cfg = ctx.config_files.get("setup.cfg")
    if setup_cfg and setup_cfg.is_file():
        try:
            text = setup_cfg.read_text(encoding="utf-8")
            if "[tool:pytest]" in text:
                evidence.append("[tool:pytest] in setup.cfg")
                confidence += 0.3
        except Exception:
            pass

    # 4) pytest in requirements.txt or pyproject.toml deps
    if ctx.requirements_txt_text and re.search(r"(?m)^pytest\b", ctx.requirements_txt_text):
        evidence.append("pytest listed in requirements.txt")
        confidence += 0.3
    if ctx.pyproject_toml_text and "pytest" in ctx.pyproject_toml_text:
        evidence.append("pytest referenced in pyproject.toml")
        confidence += 0.2

    # 5) test_*.py or *_test.py files exist
    test_files = [
        p for p in ctx.py_files
        if p.name.startswith("test_") or p.name.endswith("_test.py")
    ]
    if test_files:
        evidence.append(f"{len(test_files)} test file(s) matching test_*.py / *_test.py")
        confidence += 0.2

    # 6) tests/ directory
    if (root / "tests").is_dir() or (root / "test").is_dir():
        evidence.append("tests/ directory found")
        confidence += 0.1

    if confidence < 0.3:
        return None

    return TestFrameworkMatch(
        framework="pytest",
        command="pytest",
        confidence=min(confidence, 1.0),
        evidence=evidence,
    )


def _detect_unittest(root: Path, ctx: _ScanContext) -> TestFrameworkMatch | None:
    """Detect Python unittest via import patterns in test files."""
    evidence: list[str] = []
    confidence = 0.0

    unittest_files: list[str] = []
    for p in ctx.py_files:
        if not (p.name.startswith("test_") or p.name.endswith("_test.py")):
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if re.search(r"(?m)^\s*(import unittest|from unittest)", text):
            unittest_files.append(str(p.relative_to(root)))

    if unittest_files:
        evidence.append(f"{len(unittest_files)} file(s) import unittest")
        confidence += 0.3 + min(len(unittest_files) * 0.1, 0.3)

    # If pytest was NOT detected but unittest files exist → higher confidence
    has_pytest_config = (
        "conftest.py" in ctx.config_files
        or "pytest.ini" in ctx.config_files
        or (ctx.pyproject_toml_text and "[tool.pytest" in ctx.pyproject_toml_text)
    )
    if unittest_files and not has_pytest_config:
        evidence.append("no pytest config found – unittest is primary framework")
        confidence += 0.2

    if confidence < 0.3:
        return None

    return TestFrameworkMatch(
        framework="unittest",
        command="python -m unittest discover -s tests -v",
        confidence=min(confidence, 1.0),
        evidence=evidence,
    )


def _detect_jest(root: Path, ctx: _ScanContext) -> TestFrameworkMatch | None:
    """Detect Jest via config files, package.json deps/scripts, and test files."""
    evidence: list[str] = []
    confidence = 0.0

    # 1) jest.config.* file
    jest_configs = [k for k in ctx.config_files if k.startswith("jest.config")]
    if jest_configs:
        evidence.append(f"Config file: {jest_configs[0]}")
        confidence += 0.5

    pkg = ctx.package_json
    if pkg:
        all_deps = {
            **pkg.get("dependencies", {}),
            **pkg.get("devDependencies", {}),
        }

        # 2) jest in dependencies
        if "jest" in all_deps:
            evidence.append("jest in package.json dependencies")
            confidence += 0.4

        # 3) @jest/core or ts-jest
        if "ts-jest" in all_deps or "@jest/core" in all_deps:
            evidence.append("ts-jest or @jest/core in deps")
            confidence += 0.2

        # 4) "test" script contains "jest"
        scripts = pkg.get("scripts", {})
        test_script = scripts.get("test", "")
        if "jest" in test_script.lower():
            evidence.append(f'package.json scripts.test = "{test_script}"')
            confidence += 0.3

        # 5) jest key in package.json (inline config)
        if "jest" in pkg:
            evidence.append("jest config block in package.json")
            confidence += 0.3

    # 6) *.test.js / *.spec.js files
    test_files = [
        p for p in ctx.js_ts_files
        if ".test." in p.name or ".spec." in p.name
    ]
    if test_files:
        evidence.append(f"{len(test_files)} test/spec file(s)")
        confidence += 0.15

    # 7) __tests__/ directory
    if (root / "__tests__").is_dir():
        evidence.append("__tests__/ directory found")
        confidence += 0.1

    if confidence < 0.3:
        return None

    # Prefer npm test if scripts.test mentions jest
    cmd = "npx jest"
    if pkg:
        test_script = pkg.get("scripts", {}).get("test", "")
        if "jest" in test_script.lower():
            cmd = "npm test"

    return TestFrameworkMatch(
        framework="jest",
        command=cmd,
        confidence=min(confidence, 1.0),
        evidence=evidence,
    )


def _detect_vitest(root: Path, ctx: _ScanContext) -> TestFrameworkMatch | None:
    """Detect Vitest via config files, package.json deps/scripts."""
    evidence: list[str] = []
    confidence = 0.0

    # 1) vitest.config.* file
    vitest_configs = [k for k in ctx.config_files if k.startswith("vitest.config")]
    if vitest_configs:
        evidence.append(f"Config file: {vitest_configs[0]}")
        confidence += 0.5

    # 2) Check vite.config for vitest plugin / test block
    for vite_name in ("vite.config.ts", "vite.config.js"):
        vite_cfg = ctx.config_files.get(vite_name)
        if vite_cfg and vite_cfg.is_file():
            try:
                text = vite_cfg.read_text(encoding="utf-8", errors="replace")
                if "vitest" in text.lower() or "/// <reference types=\"vitest\"" in text:
                    evidence.append(f"vitest reference in {vite_name}")
                    confidence += 0.4
                if re.search(r"test\s*:", text):
                    evidence.append(f"test config block in {vite_name}")
                    confidence += 0.2
            except Exception:
                pass

    pkg = ctx.package_json
    if pkg:
        all_deps = {
            **pkg.get("dependencies", {}),
            **pkg.get("devDependencies", {}),
        }

        # 3) vitest in dependencies
        if "vitest" in all_deps:
            evidence.append("vitest in package.json dependencies")
            confidence += 0.5

        # 4) "test" script contains "vitest"
        scripts = pkg.get("scripts", {})
        test_script = scripts.get("test", "")
        if "vitest" in test_script.lower():
            evidence.append(f'package.json scripts.test = "{test_script}"')
            confidence += 0.3

    # 5) *.test.ts / *.spec.ts alongside vite config
    if vitest_configs or any("vitest" in str(e) for e in evidence):
        test_files = [
            p for p in ctx.js_ts_files
            if ".test." in p.name or ".spec." in p.name
        ]
        if test_files:
            evidence.append(f"{len(test_files)} test/spec file(s)")
            confidence += 0.1

    if confidence < 0.3:
        return None

    # Prefer npm test if scripts.test mentions vitest
    cmd = "npx vitest run"
    if pkg:
        test_script = pkg.get("scripts", {}).get("test", "")
        if "vitest" in test_script.lower():
            cmd = "npm test"

    return TestFrameworkMatch(
        framework="vitest",
        command=cmd,
        confidence=min(confidence, 1.0),
        evidence=evidence,
    )
