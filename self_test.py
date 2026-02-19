#!/usr/bin/env python3
"""Determinism self-test — runs the agent N times and verifies identical output.

Steps
-----
1. Run the agent pipeline 3 times on the same repository.
2. Strip non-deterministic fields (timestamps, runtime) from each results.json.
3. Compare all runs — they must produce byte-identical canonical JSON.
4. Run ``validate_output.py`` on each output.
5. Print **PASS** only when every run is identical AND valid.

Usage::

    # Against a real repo (requires clone + CI access):
    python3 self_test.py --repo-url https://github.com/org/repo \\
                         --repo-path /tmp/test-repo \\
                         --team ALPHA --leader ALICE

    # Quick smoke test with a mock (no network, no Docker):
    python3 self_test.py --mock
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

# Ensure project root is importable
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

RUNS = 3
RESULTS_FILENAME = "results.json"

# ── Timestamp / runtime fields to strip before comparison ────────────

_IGNORE_TOP = {"generated_at", "runtime_seconds"}
_IGNORE_CI = {"timestamp"}


def _strip_nondeterministic(obj: dict[str, Any]) -> dict[str, Any]:
    """Return a deep copy of *obj* with volatile fields removed."""
    out = {k: v for k, v in obj.items() if k not in _IGNORE_TOP}

    # Strip timestamps inside ci_timeline entries
    if "ci_timeline" in out:
        out["ci_timeline"] = [
            {k: v for k, v in entry.items() if k not in _IGNORE_CI}
            for entry in out["ci_timeline"]
        ]

    # Strip runtime from nested score (it's derived from runtime_seconds)
    if "score" in out and isinstance(out["score"], dict):
        out["score"] = dict(out["score"])  # shallow copy

    return out


def _canonical_json(obj: dict[str, Any]) -> str:
    """Serialise to deterministic JSON (sorted keys, no trailing whitespace)."""
    return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False)


# ── Mock pipeline for --mock mode ────────────────────────────────────

def _build_mock_result(run_idx: int) -> dict[str, Any]:
    """Simulate a deterministic results.json without any real agent run."""
    from shared.results_exporter import build_results
    from agents.run_memory import RunMemory

    memory = RunMemory()
    # Fake a classified failure
    memory.append_failures(1, [
        {"file": "app/main.py", "line": 10, "bug_type": "SYNTAX",
         "message": "expected ':'"},
    ])
    # Fake an applied fix
    memory.append_fixes(1, [
        {"file": "app/main.py", "bug": {"file": "app/main.py", "line": 10},
         "description": "[AI-AGENT] add missing colon"},
    ], commit_hash="abc1234")
    # Fake a CI run
    memory.append_ci_run(1, "success")

    return build_results(
        memory=memory,
        repo_url="https://github.com/test-org/test-repo",
        branch="ALPHA_ALICE_AI_Fix",
        team_name="ALPHA",
        leader_name="ALICE",
        runtime_seconds=42.0,
    )


async def _run_pipeline_once(
    repo_path: str,
    repo_url: str,
    team_name: str,
    leader_name: str,
    output_path: Path,
    max_iterations: int,
) -> dict[str, Any]:
    """Run the real pipeline and capture its results.json."""
    from shared.results_exporter import export_results
    from agents.reasoning_loop import run_reasoning_loop, ReasoningLoopResult
    from agents.repo_analysis import RepoAnalysisAgent
    from agents.base import AgentResult
    import time

    branch = f"{team_name}_{leader_name}_AI_Fix"

    context: dict[str, Any] = {
        "repo_path": repo_path,
        "repo_url": repo_url,
        "branch": branch,
        "team_name": team_name,
        "leader_name": leader_name,
    }

    pipeline_start = time.monotonic()

    # Repo analysis
    repo_agent = RepoAnalysisAgent()
    repo_result: AgentResult = await repo_agent.run(context)
    if repo_result.details:
        context.update(repo_result.details)

    # Reasoning loop
    loop_result: ReasoningLoopResult = await run_reasoning_loop(
        repo_path=repo_path,
        max_iterations=max_iterations,
        config=context,
    )

    runtime_seconds = time.monotonic() - pipeline_start

    # Export results
    if loop_result._memory_ref is not None:
        return export_results(
            memory=loop_result._memory_ref,
            repo_url=repo_url,
            branch=branch,
            team_name=team_name,
            leader_name=leader_name,
            runtime_seconds=runtime_seconds,
            output_path=output_path,
        )

    return {}


# ── Validation via validate_output.py ────────────────────────────────

def _run_validator(path: Path) -> tuple[bool, str]:
    """Run validate_output.py on *path*. Returns (passed, output)."""
    proc = subprocess.run(
        [sys.executable, str(_ROOT / "validate_output.py"), str(path)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    output = (proc.stdout + proc.stderr).strip()
    return proc.returncode == 0, output


# ── Main ─────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Determinism self-test")
    parser.add_argument("--repo-url", default="", help="GitHub repo URL")
    parser.add_argument("--repo-path", default="", help="Local clone path")
    parser.add_argument("--team", default="ALPHA", help="Team name")
    parser.add_argument("--leader", default="ALICE", help="Leader name")
    parser.add_argument("--max-iterations", type=int, default=5)
    parser.add_argument(
        "--mock", action="store_true",
        help="Use mock data instead of running the real pipeline",
    )
    parser.add_argument(
        "--runs", type=int, default=RUNS,
        help=f"Number of runs to compare (default {RUNS})",
    )
    args = parser.parse_args()

    n_runs = args.runs
    tmpdir = Path(tempfile.mkdtemp(prefix="self_test_"))
    results: list[dict[str, Any]] = []
    result_paths: list[Path] = []
    errors: list[str] = []

    print(f"=== Determinism Self-Test ({n_runs} runs) ===\n")

    # ── Step 1: Run agent N times ────────────────────────────────────
    for i in range(1, n_runs + 1):
        out_path = tmpdir / f"results_run{i}.json"
        print(f"Run {i}/{n_runs} ...", end=" ", flush=True)

        try:
            if args.mock:
                data = _build_mock_result(i)
                out_path.write_text(json.dumps(data, indent=2) + "\n")
            else:
                if not args.repo_path:
                    errors.append("--repo-path is required for real runs")
                    break
                data = asyncio.run(_run_pipeline_once(
                    repo_path=args.repo_path,
                    repo_url=args.repo_url,
                    team_name=args.team,
                    leader_name=args.leader,
                    output_path=out_path,
                    max_iterations=args.max_iterations,
                ))
        except Exception as exc:
            errors.append(f"Run {i} failed: {exc}")
            print("ERROR")
            continue

        results.append(data)
        result_paths.append(out_path)
        print("OK")

    if errors:
        print(f"\n--- Errors ---")
        for e in errors:
            print(f"  ! {e}")
        print("\nFAIL")
        return 1

    if len(results) < 2:
        print("\nNeed at least 2 successful runs to compare.")
        print("\nFAIL")
        return 1

    # ── Step 2: Compare outputs ignoring timestamps ──────────────────
    print(f"\nComparing {n_runs} outputs (ignoring timestamps/runtime) ...")

    stripped = [_strip_nondeterministic(r) for r in results]
    canonical = [_canonical_json(s) for s in stripped]

    identical = all(c == canonical[0] for c in canonical[1:])
    if identical:
        print("  All outputs are identical.")
    else:
        print("  MISMATCH detected between runs!\n")
        # Show first diff
        for i in range(1, len(canonical)):
            if canonical[i] != canonical[0]:
                print(f"  --- Run 1 vs Run {i + 1} ---")
                _print_diff(canonical[0], canonical[i])
                break
        print("\nFAIL")
        return 1

    # ── Step 3: Validate each output ─────────────────────────────────
    print(f"\nValidating {n_runs} outputs via validate_output.py ...")
    all_valid = True
    for i, path in enumerate(result_paths, 1):
        passed, output = _run_validator(path)
        status = "PASS" if passed else "FAIL"
        print(f"  Run {i}: {status}  {output}")
        if not passed:
            all_valid = False

    # ── Step 4: Final verdict ────────────────────────────────────────
    print()
    if identical and all_valid:
        print("PASS")
        return 0
    else:
        reasons = []
        if not identical:
            reasons.append("outputs differ")
        if not all_valid:
            reasons.append("validation failed")
        print(f"FAIL ({', '.join(reasons)})")
        return 1


def _print_diff(a: str, b: str) -> None:
    """Print a simple line-by-line diff between two JSON strings."""
    a_lines = a.splitlines()
    b_lines = b.splitlines()
    max_lines = max(len(a_lines), len(b_lines))
    shown = 0
    for idx in range(max_lines):
        la = a_lines[idx] if idx < len(a_lines) else "<missing>"
        lb = b_lines[idx] if idx < len(b_lines) else "<missing>"
        if la != lb:
            print(f"    L{idx + 1}:")
            print(f"      run1: {la}")
            print(f"      run2: {lb}")
            shown += 1
            if shown >= 10:
                print("    ... (truncated)")
                break


if __name__ == "__main__":
    sys.exit(main())
