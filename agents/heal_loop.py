"""Heal Loop — core retry orchestrator.

Cycles through:  Analyzer → Classifier → Fixer → Verifier
up to *max_iterations* times (default 5) until all tests pass
or the budget is exhausted.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from agents.analyzer import AnalyzerAgent
from agents.classifier import ClassifierAgent
from agents.fixer import CodeFixerAgent
from agents.verifier import VerifierAgent
from agents.base import AgentResult

logger = logging.getLogger(__name__)


# ── Result dataclasses ───────────────────────────────────────────────

@dataclass
class HealIteration:
    """Snapshot of one heal-loop iteration."""
    iteration: int
    analyzer: dict[str, Any]
    classifier: dict[str, Any]
    fixer: dict[str, Any]
    verifier: dict[str, Any]
    all_passed: bool
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "iteration": self.iteration,
            "analyzer": self.analyzer,
            "classifier": self.classifier,
            "fixer": self.fixer,
            "verifier": self.verifier,
            "all_passed": self.all_passed,
            "timestamp": self.timestamp,
        }


@dataclass
class HealLoopResult:
    """Aggregated result of the entire heal loop."""
    status: str                     # "healed" | "partial" | "failed"
    iterations_used: int
    max_iterations: int
    total_bugs_found: int
    total_fixes_applied: int
    iterations: list[HealIteration] = field(default_factory=list)
    final_test_output: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "iterations_used": self.iterations_used,
            "max_iterations": self.max_iterations,
            "total_bugs_found": self.total_bugs_found,
            "total_fixes_applied": self.total_fixes_applied,
            "iterations": [it.to_dict() for it in self.iterations],
        }


# Type for optional progress callback
ProgressCallback = Callable[[str, str, str], None] | None


# ── Main loop ────────────────────────────────────────────────────────

async def run_heal_loop(
    repo_path: str,
    max_iterations: int = 5,
    config: dict[str, Any] | None = None,
    on_progress: ProgressCallback = None,
) -> HealLoopResult:
    """Run the heal loop: analyze → classify → fix → verify → repeat.

    Args:
        repo_path:       Path to the repository being healed.
        max_iterations:  Maximum loop iterations (default 5).
        config:          Optional dict merged into agent context.
        on_progress:     Optional callback(agent_name, status, message).

    Returns:
        HealLoopResult with full iteration history.
    """

    context: dict[str, Any] = {
        "repo_path": repo_path,
        **(config or {}),
    }

    analyzer  = AnalyzerAgent()
    classifier = ClassifierAgent()
    fixer     = CodeFixerAgent()
    verifier  = VerifierAgent()

    iterations: list[HealIteration] = []
    total_bugs = 0
    total_fixes = 0
    total_commits = 0
    max_commits = 10
    prev_failure_keys: set[tuple[str, int, str]] = set()

    for i in range(1, max_iterations + 1):
        logger.info("═══ Heal Loop — iteration %d/%d ═══", i, max_iterations)
        _emit(on_progress, "heal_loop", "running", f"Starting iteration {i}/{max_iterations}")

        # Flag for dependency installation (only first iteration)
        context["_first_iteration"] = (i == 1)

        # ── 1. Analyze ───────────────────────────────────────────────
        _emit(on_progress, analyzer.name, "started", f"[iter {i}] Running tests…")
        analysis: AgentResult = await analyzer.run(context)
        context.update(analysis.details)
        _emit(on_progress, analyzer.name, analysis.status, analysis.summary)

        # Local tests passing is informational — final pass/fail
        # comes from CI. On the first iteration we proceed so the
        # pipeline at least triggers a CI run to confirm.
        local_passed = analysis.details.get("all_passed", False)
        if local_passed and i == 1:
            logger.info(
                "Local tests pass on first iteration — will proceed "
                "to CI for confirmation."
            )
        elif local_passed:
            logger.info(
                "Local tests pass — but deferring to CI for final verdict."
            )

        # ── 2. Classify ──────────────────────────────────────────────
        _emit(on_progress, classifier.name, "started", f"[iter {i}] Classifying errors…")
        classified: AgentResult = await classifier.run(context)
        context.update(classified.details)
        _emit(on_progress, classifier.name, classified.status, classified.summary)

        bugs_this_round = classified.details.get("classified_bugs", [])
        total_bugs += len(bugs_this_round)

        # ── Early stop: identical failures as previous iteration ─────
        current_keys = {
            (b.get("file", ""), b.get("line", 0), b.get("bug_type", ""))
            for b in bugs_this_round
        }
        if prev_failure_keys and current_keys == prev_failure_keys:
            logger.info(
                "No new failures detected (%d identical) — stopping early.",
                len(current_keys),
            )
            iterations.append(HealIteration(
                iteration=i,
                analyzer=analysis.to_dict(),
                classifier=classified.to_dict(),
                fixer={},
                verifier={},
                all_passed=False,
            ))
            break
        prev_failure_keys = current_keys

        if len(bugs_this_round) == 0:
            # If local tests passed AND classifier found 0 bugs, the repo
            # is already healthy — mark as passed.
            if local_passed:
                logger.info("Local tests pass and no bugs classified — repo is clean.")
                iterations.append(HealIteration(
                    iteration=i,
                    analyzer=analysis.to_dict(),
                    classifier=classified.to_dict(),
                    fixer={},
                    verifier={},
                    all_passed=True,
                ))
                break
            logger.info("No classifiable bugs — cannot auto-fix.")
            iterations.append(HealIteration(
                iteration=i,
                analyzer=analysis.to_dict(),
                classifier=classified.to_dict(),
                fixer={},
                verifier={},
                all_passed=False,
            ))
            break

        # ── 3. Fix ───────────────────────────────────────────────────
        _emit(on_progress, fixer.name, "started", f"[iter {i}] Applying fixes…")
        fixed: AgentResult = await fixer.run(context)
        context.update(fixed.details)
        _emit(on_progress, fixer.name, fixed.status, fixed.summary)

        fixes_applied = fixed.details.get("applied_count", 0)
        total_fixes += fixes_applied
        if fixes_applied > 0:
            total_commits += 1

        if fixes_applied == 0:
            logger.info("No fixes could be applied — stopping loop.")
            iterations.append(HealIteration(
                iteration=i,
                analyzer=analysis.to_dict(),
                classifier=classified.to_dict(),
                fixer=fixed.to_dict(),
                verifier={},
                all_passed=False,
            ))
            break

        # ── 4. Verify ────────────────────────────────────────────────
        _emit(on_progress, verifier.name, "started", f"[iter {i}] Verifying fixes…")
        verified: AgentResult = await verifier.run(context)
        _emit(on_progress, verifier.name, verified.status, verified.summary)

        # The verifier reports local results only — CI is authoritative.
        local_all_passed = verified.details.get("local_all_passed",
                                                 verified.details.get("all_passed", False))
        ci_confirmed = verified.details.get("ci_confirmed", False)

        # Prepare context for next iteration
        context["test_output"] = verified.details.get("verification_output", "")
        context["failing_suites"] = verified.details.get("failing_suites", 0)

        iterations.append(HealIteration(
            iteration=i,
            analyzer=analysis.to_dict(),
            classifier=classified.to_dict(),
            fixer=fixed.to_dict(),
            verifier=verified.to_dict(),
            all_passed=ci_confirmed,  # only True when CI passes
        ))

        if ci_confirmed:
            logger.info("CI confirmed all tests pass after iteration %d — healed!", i)
            break

        should_continue = verified.details.get("should_continue", False)
        if not should_continue and not local_all_passed:
            # Only stop if both CI and local indicate no progress.
            # If local passes but CI hasn't confirmed, keep going.
            logger.info("Verifier says stop — no further improvement expected.")
            break
        elif local_all_passed:
            logger.info(
                "Local tests pass but CI not confirmed — continuing loop."
            )

        # ── Commit budget guard ──────────────────────────────────────
        if total_commits >= max_commits:
            logger.info(
                "Commit budget exhausted (%d/%d) — stopping loop.",
                total_commits, max_commits,
            )
            break

    # ── Final status ─────────────────────────────────────────────────
    final_ci_passed = iterations[-1].all_passed if iterations else False

    if final_ci_passed:
        status = "healed"           # CI confirmed
    elif total_fixes > 0:
        status = "partial"          # fixes applied but CI not green
    else:
        status = "failed"

    result = HealLoopResult(
        status=status,
        iterations_used=len(iterations),
        max_iterations=max_iterations,
        total_bugs_found=total_bugs,
        total_fixes_applied=total_fixes,
        iterations=iterations,
        final_test_output=context.get("test_output", ""),
    )

    _emit(
        on_progress,
        "heal_loop",
        "completed",
        f"Heal loop {status}: {len(iterations)} iteration(s), "
        f"{total_bugs} bug(s) found, {total_fixes} fix(es) applied.",
    )

    return result


def _emit(
    callback: ProgressCallback,
    agent: str,
    status: str,
    message: str,
) -> None:
    """Fire the progress callback if set."""
    if callback is not None:
        try:
            callback(agent, status, message)
        except Exception:
            pass  # never let callback errors break the loop
