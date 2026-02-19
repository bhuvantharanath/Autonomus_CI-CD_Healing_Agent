"""Reasoning Loop — LangGraph-powered CI-driven agent orchestrator.

This module implements the core autonomous DevOps agent as a **LangGraph
StateGraph** with 8 tool-nodes and conditional edges.  Each tool node
wraps an AgentTool from the registry; transitions between nodes are
governed by deterministic reasoning logic that inspects tool outputs.

LangGraph StateGraph (CI-driven):
    RUN_TESTS → CLASSIFY → PLAN_FIX → APPLY_PATCH → COMMIT_PUSH
              → WAIT_FOR_CI → FETCH_CI_RESULTS → VERIFY → (loop or DONE)

After APPLY_PATCH the agent commits and pushes to GitHub, waits for
the CI workflow to complete, fetches the CI logs, and verifies using
CI results.  If CI fails and iterations remain, the loop goes back to
CLASSIFY using CI logs (not RUN_TESTS — the CI already ran the tests).

Each full cycle counts as one iteration.  The loop caps at
*max_iterations* (default 5) and exits early when CI passes.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, TypedDict

from langgraph.graph import StateGraph, END

from agents.run_memory import RunMemory

from agents.tools.registry import ToolRegistry, ToolResult
from agents.tools.test_runner_tool import TestRunnerTool
from agents.tools.failure_classifier_tool import FailureClassifierTool
from agents.tools.fix_planner_tool import FixPlannerTool
from agents.tools.patch_applier_tool import PatchApplierTool
from agents.tools.commit_push_tool import CommitPushTool
from agents.tools.wait_for_ci_tool import WaitForCITool
from agents.tools.fetch_ci_results_tool import FetchCIResultsTool
from agents.tools.verification_tool import VerificationTool

logger = logging.getLogger(__name__)

# ── Commit budget ────────────────────────────────────────────────────
MAX_TOTAL_COMMITS = 10  # hard cap — keep the PR diff reviewable


# ── Workflow phases ──────────────────────────────────────────────────

class Phase:
    """Workflow phase constants."""

    RUN_TESTS        = "RUN_TESTS"
    CLASSIFY         = "CLASSIFY"
    PLAN_FIX         = "PLAN_FIX"
    APPLY_PATCH      = "APPLY_PATCH"
    COMMIT_PUSH      = "COMMIT_PUSH"
    WAIT_FOR_CI      = "WAIT_FOR_CI"
    FETCH_CI_RESULTS = "FETCH_CI_RESULTS"
    VERIFY           = "VERIFY"
    DONE             = "DONE"


# Each phase → (tool_name, next_phase_on_continue, next_phase_on_stop)
TRANSITIONS: dict[str, tuple[str, str, str]] = {
    Phase.RUN_TESTS:        ("test_runner",         Phase.CLASSIFY,         Phase.DONE),
    Phase.CLASSIFY:         ("failure_classifier",  Phase.PLAN_FIX,         Phase.DONE),
    Phase.PLAN_FIX:         ("fix_planner",         Phase.APPLY_PATCH,      Phase.DONE),
    Phase.APPLY_PATCH:      ("patch_applier",       Phase.COMMIT_PUSH,      Phase.DONE),
    Phase.COMMIT_PUSH:      ("commit_push",         Phase.WAIT_FOR_CI,      Phase.DONE),
    Phase.WAIT_FOR_CI:      ("wait_for_ci",         Phase.FETCH_CI_RESULTS, Phase.DONE),
    Phase.FETCH_CI_RESULTS: ("fetch_ci_results",    Phase.VERIFY,           Phase.DONE),
    Phase.VERIFY:           ("verification",        Phase.CLASSIFY,         Phase.DONE),
}


# ── Iteration report ────────────────────────────────────────────────

@dataclass
class IterationReport:
    """Structured snapshot of one reasoning-loop iteration."""

    iteration: int
    tool_invocations: list[dict[str, Any]] = field(default_factory=list)
    bugs_found: int = 0
    patches_applied: int = 0
    commit_sha: str = ""
    ci_conclusion: str = ""
    all_passed: bool = False
    verdict: str = ""
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "iteration": self.iteration,
            "tool_invocations": self.tool_invocations,
            "bugs_found": self.bugs_found,
            "patches_applied": self.patches_applied,
            "commit_sha": self.commit_sha,
            "ci_conclusion": self.ci_conclusion,
            "all_passed": self.all_passed,
            "verdict": self.verdict,
            "timestamp": self.timestamp,
        }


@dataclass
class ReasoningLoopResult:
    """Aggregated result of the complete reasoning loop."""

    status: str                        # "healed" | "partial" | "failed"
    iterations_used: int
    max_iterations: int
    total_bugs_found: int
    total_fixes_applied: int
    iterations: list[IterationReport] = field(default_factory=list)
    final_test_output: str = ""
    tool_registry_summary: list[dict[str, Any]] = field(default_factory=list)
    memory: dict[str, Any] = field(default_factory=dict)
    _memory_ref: RunMemory | None = field(default=None, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "iterations_used": self.iterations_used,
            "max_iterations": self.max_iterations,
            "total_bugs_found": self.total_bugs_found,
            "total_fixes_applied": self.total_fixes_applied,
            "iterations": [it.to_dict() for it in self.iterations],
            "tool_registry_summary": self.tool_registry_summary,
            "memory": self.memory,
        }


# Progress callback type
ProgressCallback = Callable[[str, str, str], None] | None


# ── Build the default tool registry ─────────────────────────────────

def build_default_registry() -> ToolRegistry:
    """Create a registry with all 8 tools for the CI-driven workflow."""
    registry = ToolRegistry()
    registry.register(TestRunnerTool())
    registry.register(FailureClassifierTool())
    registry.register(FixPlannerTool())
    registry.register(PatchApplierTool())
    registry.register(CommitPushTool())
    registry.register(WaitForCITool())
    registry.register(FetchCIResultsTool())
    registry.register(VerificationTool())
    return registry


# ── LangGraph state schema ───────────────────────────────────────────

class WorkflowState(TypedDict, total=False):
    """LangGraph state that flows through every node in the graph.

    Keys are populated incrementally by the tool-nodes and consumed
    by the conditional-edge functions that implement transition logic.
    """
    # Core identifiers
    repo_path: str
    repo_url: str
    branch: str
    team_name: str
    leader_name: str
    # Tool outputs (accumulated)
    test_output: str
    classified_bugs: list
    fix_plan: list
    applied_count: int
    applied_patches: list
    commit_sha: str
    commit_message: str
    push_status: str
    ci_run_id: int
    ci_conclusion: str
    ci_run_url: str
    ci_status: str
    ci_logs: str
    ci_passed: int
    ci_failed: int
    all_passed: bool
    verdict: str
    should_continue: bool
    local_all_passed: bool


# ── LangGraph node factory ───────────────────────────────────────────

def _make_langgraph_node(
    tool_name: str,
    registry: ToolRegistry,
    shared: dict[str, Any],
    on_progress: ProgressCallback,
):
    """Create an async LangGraph node function for a given tool.

    Each node:
      1. Reads the shared mutable state dict (tool inputs live there).
      2. Dispatches the tool via the registry.
      3. Merges outputs back into shared state.
      4. Returns outputs as a partial state update for LangGraph.
    """

    async def _node(state: dict[str, Any]) -> dict[str, Any]:
        tool = registry.get(tool_name)
        iteration = shared.get("_current_iteration", 1)

        _emit(on_progress, tool_name, "started",
              f"[iter {iteration}] {tool.description[:60]}…")

        logger.info("[iter %d] LangGraph node dispatching tool: %s",
                    iteration, tool_name)

        # Validate inputs against shared state
        missing = registry.validate_io(tool_name, shared)
        if missing:
            logger.warning(
                "[iter %d] Tool '%s' missing inputs: %s — returning empty",
                iteration, tool_name, missing,
            )
            _emit(on_progress, tool_name, "skipped",
                  f"Missing inputs: {missing}")
            shared["_tool_skipped"] = True
            return {}

        shared["_tool_skipped"] = False
        result: ToolResult = await tool.execute(shared)

        logger.info(
            "[iter %d] Tool '%s' finished | status=%s | summary=%s",
            iteration, tool_name, result.status,
            result.summary[:200] if result.summary else "(none)",
        )
        if result.errors:
            logger.warning(
                "[iter %d] Tool '%s' errors: %s",
                iteration, tool_name, result.errors,
            )

        # Record invocation in the iteration report
        report: IterationReport | None = shared.get("_current_report")
        if report:
            report.tool_invocations.append(result.to_dict())

        _emit(on_progress, tool_name, result.status, result.summary)

        # Merge outputs into shared state
        shared.update(result.outputs)

        return result.outputs

    _node.__name__ = f"node_{tool_name}"        # LangGraph uses __name__
    _node.__qualname__ = f"node_{tool_name}"
    return _node


# ── LangGraph conditional-edge factories ─────────────────────────────

def _make_edge_after_phase(
    phase: str,
    shared: dict[str, Any],
    memory: RunMemory,
):
    """Build the conditional-edge function for *phase*.

    Returns either the next node name or END.
    """
    tool_name, next_continue, next_stop = TRANSITIONS[phase]
    next_continue_node = f"node_{TRANSITIONS[next_continue][0]}" if next_continue != Phase.DONE else END
    next_stop_node = END

    def _edge(state: dict[str, Any]) -> str:
        # If the tool was skipped, go to DONE
        if shared.get("_tool_skipped"):
            return next_stop_node

        report: IterationReport | None = shared.get("_current_report")
        iteration = shared.get("_current_iteration", 1)
        max_iterations = shared.get("_max_iterations", 5)

        # Reuse the existing transition logic
        # Build a synthetic ToolResult for the transition function
        result_status = shared.get("_last_tool_status", "success")

        # Execute the transition logic via the same _reason_transition
        next_phase = _reason_transition(
            phase=phase, tool_name=tool_name,
            result=ToolResult(
                tool_name=tool_name, status=result_status,
                outputs={},
            ),
            state=shared, report=report if report else IterationReport(iteration=iteration),
            next_continue=next_continue, next_stop=next_stop,
            current_iteration=iteration,
            max_iterations=max_iterations,
            memory=memory,
        )

        if next_phase == Phase.DONE:
            return END

        # Map phase → node name
        target_tool = TRANSITIONS[next_phase][0]
        return f"node_{target_tool}"

    _edge.__name__ = f"edge_after_{phase.lower()}"
    return _edge


def _build_langgraph(
    registry: ToolRegistry,
    shared: dict[str, Any],
    memory: RunMemory,
    on_progress: ProgressCallback,
):
    """Build and compile a LangGraph StateGraph for one iteration.

    The graph has 8 nodes (one per tool) connected by conditional
    edges that implement the transition logic.
    """

    graph = StateGraph(dict)  # Use plain dict state

    # ── Add nodes (one per tool) ─────────────────────────────────────
    for phase_name, (tool_name, _, _) in TRANSITIONS.items():
        node_fn = _make_langgraph_node(tool_name, registry, shared, on_progress)
        graph.add_node(f"node_{tool_name}", node_fn)

    # ── Set entry point ──────────────────────────────────────────────
    graph.set_entry_point("node_test_runner")

    # ── Add conditional edges ────────────────────────────────────────
    all_node_names = [f"node_{TRANSITIONS[p][0]}" for p in TRANSITIONS] + [END]

    for phase_name in TRANSITIONS:
        tool_name = TRANSITIONS[phase_name][0]
        source_node = f"node_{tool_name}"
        edge_fn = _make_edge_after_phase(phase_name, shared, memory)
        graph.add_conditional_edges(
            source_node,
            edge_fn,
            {name: name for name in all_node_names},
        )

    return graph.compile()


# ── Main reasoning loop (LangGraph-powered) ─────────────────────────

async def run_reasoning_loop(
    repo_path: str,
    max_iterations: int = 5,
    config: dict[str, Any] | None = None,
    on_progress: ProgressCallback = None,
    registry: ToolRegistry | None = None,
) -> ReasoningLoopResult:
    """Run the CI-driven autonomous reasoning loop using LangGraph.

    This function builds a LangGraph StateGraph with 8 tool-nodes and
    conditional edges, then invokes it for each iteration.  The graph
    structure mirrors the deterministic state machine:

        RUN_TESTS → CLASSIFY → PLAN_FIX → APPLY_PATCH → COMMIT_PUSH
                  → WAIT_FOR_CI → FETCH_CI_RESULTS → VERIFY

    Args:
        repo_path:       Path to the cloned repository.
        max_iterations:  Max heal iterations (default 5).
        config:          Extra config merged into workflow state.
                         Must include 'repo_url' and 'branch'.
        on_progress:     Optional callback(tool_name, status, message).
        registry:        Optional pre-built ToolRegistry (for testing).

    Returns:
        ReasoningLoopResult with full iteration history.
    """

    # ── Initialise state & registry ──────────────────────────────────
    if registry is None:
        registry = build_default_registry()

    shared: dict[str, Any] = {
        "repo_path": repo_path,
        **(config or {}),
    }

    memory = RunMemory()
    iterations: list[IterationReport] = []
    total_bugs = 0
    total_fixes = 0
    total_commits = 0
    current_iteration = 0

    shared["_max_iterations"] = max_iterations

    _emit(on_progress, "reasoning_loop", "started",
          f"LangGraph CI-driven reasoning loop started (max {max_iterations} iterations). "
          f"Tools: {[t['name'] for t in registry.list_tools()]}")

    # Build the LangGraph once — it's re-invoked per iteration
    compiled_graph = _build_langgraph(registry, shared, memory, on_progress)
    logger.info("LangGraph StateGraph compiled with %d nodes.", len(TRANSITIONS))

    # ── Outer iteration loop ─────────────────────────────────────────
    while current_iteration < max_iterations:
        current_iteration += 1
        report = IterationReport(iteration=current_iteration)

        logger.info("═══ LangGraph Reasoning Loop — iteration %d/%d ═══",
                     current_iteration, max_iterations)
        _emit(on_progress, "reasoning_loop", "running",
              f"Starting iteration {current_iteration}/{max_iterations}")

        shared["_first_iteration"] = (current_iteration == 1)
        shared["_current_iteration"] = current_iteration
        shared["_run_memory"] = memory
        shared["_current_report"] = report

        # Log the state keys available at the start of each iteration
        logger.info(
            "[iter %d] State keys at iteration start: %s",
            current_iteration,
            sorted(k for k in shared.keys() if not k.startswith("_")),
        )
        logger.info(
            "[iter %d] repo_path=%s | exists=%s",
            current_iteration,
            shared.get("repo_path", "(not set)"),
            Path(shared.get("repo_path", ".")).exists(),
        )

        # ── Invoke the LangGraph for this iteration ──────────────────
        try:
            await compiled_graph.ainvoke(
                shared.copy(),  # initial state snapshot
                {"recursion_limit": 25},
            )
        except Exception as graph_exc:
            logger.error(
                "[iter %d] LangGraph execution error: %s",
                current_iteration, graph_exc,
            )
            report.verdict = f"graph_error: {graph_exc}"

        # ── Record iteration ─────────────────────────────────────────
        iterations.append(report)
        total_bugs += report.bugs_found
        total_fixes += report.patches_applied
        if report.commit_sha:
            total_commits += 1

        if report.all_passed:
            logger.info("CI passed after iteration %d — healed!", current_iteration)
            break

        # ── Commit budget guard ──────────────────────────────────────
        if total_commits >= MAX_TOTAL_COMMITS:
            logger.info(
                "Commit budget exhausted (%d/%d) — stopping loop.",
                total_commits, MAX_TOTAL_COMMITS,
            )
            break

        if not shared.get("should_continue", False):
            logger.info("Verifier says stop — halting loop.")
            break

    # ── Final verdict ────────────────────────────────────────────────
    final_passed = iterations[-1].all_passed if iterations else False

    if final_passed and total_bugs == 0 and total_fixes == 0 and total_commits == 0:
        memory.append_ci_run(
            current_iteration,
            "success",
        )

    if final_passed:
        status = "healed"
    elif total_fixes > 0:
        status = "partial"
    else:
        status = "failed"

    loop_result = ReasoningLoopResult(
        status=status,
        iterations_used=len(iterations),
        max_iterations=max_iterations,
        total_bugs_found=total_bugs,
        total_fixes_applied=total_fixes,
        iterations=iterations,
        final_test_output=shared.get("verification_output", shared.get("test_output", "")),
        tool_registry_summary=registry.list_tools(),
        memory=memory.to_dict(),
        _memory_ref=memory,
    )

    _emit(on_progress, "reasoning_loop", "completed",
          f"LangGraph loop {status}: {len(iterations)} iteration(s), "
          f"{total_bugs} bug(s), {total_fixes} fix(es).")

    return loop_result


def _reason_transition(
    phase: str,
    tool_name: str,
    result: ToolResult,
    state: dict[str, Any],
    report: IterationReport,
    next_continue: str,
    next_stop: str,
    current_iteration: int,
    max_iterations: int,
    memory: RunMemory | None = None,
) -> str:
    """Deterministic transition logic — the 'reasoning' in the loop.

    Examines tool outputs and decides the next phase.
    """

    if phase == Phase.RUN_TESTS:
        # Local test results are informational — they guide classification
        # but never determine final pass/fail.  Only CI can do that.
        local_passed = state.get("local_all_passed", False)

        # If CI already confirmed success (from a prior iteration), we
        # can exit early — the authoritative source has spoken.
        if memory and memory.ci_runs:
            last_ci = memory.ci_runs[-1]
            if last_ci.status == "success":
                report.all_passed = True
                report.verdict = "pass"
                return Phase.DONE

        # Local tests pass but CI hasn't confirmed yet — feed CI logs
        # (if available) into test_output so the classifier analyses the
        # CI failures rather than the (clean) local output.
        if local_passed and state.get("ci_logs"):
            logger.info(
                "[iter %d] Local tests pass but CI not confirmed — "
                "using CI logs for classification.",
                current_iteration,
            )
            state["test_output"] = state["ci_logs"]

        return next_continue

    if phase == Phase.CLASSIFY:
        bugs = state.get("classified_bugs", [])
        report.bugs_found = len(bugs)
        if memory and bugs:
            memory.append_failures(current_iteration, bugs)
        if not bugs:
            # If local tests actually passed AND no bugs were classified
            # the repo is genuinely clean — skip CI monitoring and mark
            # as passed.  If local tests *failed* but the classifier
            # couldn't parse the errors, treat it as an unclassifiable
            # failure (do NOT mark as passed).
            local_passed = state.get("local_all_passed", False)
            if local_passed:
                report.all_passed = True
                report.verdict = "pass"
                return Phase.DONE
            report.verdict = "no_bugs_classified"
            return next_stop

        # ── Early stop: identical failures as previous iteration ─────
        # Compare by (file, bug_type) — ignoring exact line numbers.
        # This catches the ping-pong pattern where a bad fix shifts
        # the error to an adjacent line, making it look "new".
        current_keys = {
            (b.get("file", ""), b.get("bug_type", ""))
            for b in bugs
        }
        prev_keys = state.get("_prev_failure_keys", set())
        if prev_keys and current_keys == prev_keys:
            logger.info(
                "[iter %d] Same (file, bug_type) failures as previous "
                "iteration (%d identical) — stopping early to avoid "
                "redundant commits.",
                current_iteration, len(current_keys),
            )
            report.verdict = "no_new_failures"
            return next_stop
        state["_prev_failure_keys"] = current_keys

        return next_continue

    if phase == Phase.PLAN_FIX:
        plan = state.get("fix_plan", [])
        actionable = [
            e for e in plan
            if e.get("strategy") not in ("skip_test_file", "unresolvable")
        ]
        if not actionable:
            report.verdict = "no_actionable_fixes"
            return next_stop
        return next_continue

    if phase == Phase.APPLY_PATCH:
        applied = state.get("applied_count", 0)
        report.patches_applied = applied
        if applied == 0:
            report.verdict = "no_patches_applied"
            return next_stop
        return next_continue

    if phase == Phase.COMMIT_PUSH:
        sha = state.get("commit_sha", "")
        push_status = state.get("push_status", "")
        report.commit_sha = sha
        if memory and sha:
            memory.append_fixes(
                current_iteration,
                state.get("applied_patches", []),
                sha,
            )
        if not sha:
            report.verdict = "commit_failed"
            return next_stop

        # If push failed, skip CI monitoring — fixes are applied locally
        # but can't be verified via CI.
        if push_status == "push_failed":
            logger.warning(
                "[iter %d] Push failed — skipping CI monitoring. "
                "Fixes are committed locally.",
                current_iteration,
            )
            report.verdict = "push_failed_fixes_applied"
            report.all_passed = False
            return Phase.DONE

        return next_continue

    if phase == Phase.WAIT_FOR_CI:
        ci_status = state.get("ci_status", "")
        if ci_status != "completed":
            # CI monitoring failed (timeout, no workflow found, etc.)
            # Instead of stopping, fall back to local test verification.
            logger.warning(
                "[iter %d] CI monitoring failed (status=%s) — "
                "falling back to local test verification.",
                current_iteration, ci_status,
            )
            # Mark that CI is unavailable so VERIFY can handle gracefully
            state["_ci_unavailable"] = True
            # Skip FETCH_CI_RESULTS and go directly to VERIFY
            # with the local test results as verification basis
            state["ci_conclusion"] = ""
            state["ci_logs"] = state.get("test_output", "")
            return Phase.VERIFY
        state["_ci_unavailable"] = False
        return next_continue

    if phase == Phase.FETCH_CI_RESULTS:
        ci_logs = state.get("ci_logs", "")
        conclusion = state.get("ci_conclusion", "")
        if memory:
            memory.append_ci_run(
                current_iteration,
                conclusion or "unknown",
            )
        if not ci_logs:
            report.verdict = "no_ci_logs"
            return next_stop
        return next_continue

    if phase == Phase.VERIFY:
        report.all_passed = state.get("all_passed", False)
        report.ci_conclusion = state.get("ci_conclusion", "")
        report.verdict = state.get("verdict", "fail")

        ci_unavailable = state.get("_ci_unavailable", False)

        if ci_unavailable:
            # CI was unavailable — use local test results as ground truth.
            # Re-run local tests to verify the fixes we just applied.
            local_passed = state.get("local_all_passed", False)
            if local_passed:
                report.all_passed = True
                report.verdict = "pass_local"
                report.ci_conclusion = "local_pass"
                if memory:
                    memory.append_ci_run(current_iteration, "success")
                logger.info(
                    "[iter %d] CI unavailable but local tests PASS — "
                    "marking as healed.", current_iteration,
                )
                return Phase.DONE
            else:
                # Local tests still fail — try to fix remaining issues
                should_continue = True
                at_limit = current_iteration >= max_iterations
                if should_continue and not at_limit:
                    state["test_output"] = state.get("verification_output", state.get("test_output", ""))
                    state["should_continue"] = True
                    return next_continue  # → CLASSIFY
                report.verdict = "fail_local_no_ci"
                return next_stop

        if report.all_passed:
            return Phase.DONE

        should_continue = state.get("should_continue", False)
        at_limit = current_iteration >= max_iterations
        if should_continue and not at_limit:
            # Feed CI logs back as test_output for next CLASSIFY
            state["test_output"] = state.get("ci_logs", state.get("verification_output", ""))
            return next_continue  # → CLASSIFY (loops back using CI logs)
        return next_stop

    return next_stop


# ── Progress helper ──────────────────────────────────────────────────

def _emit(
    callback: ProgressCallback,
    agent: str,
    status: str,
    message: str,
) -> None:
    if callback is not None:
        try:
            callback(agent, status, message)
        except Exception:
            pass
