"""Agents package – CI-driven autonomous DevOps agent modules."""

from agents.base import BaseAgent
from agents.run_memory import RunMemory, FailureRecord, FixRecord, CIRunRecord

# Tool-driven agents
from agents.tools import (
    AgentTool,
    ToolResult,
    ToolRegistry,
    TestRunnerTool,
    FailureClassifierTool,
    FixPlannerTool,
    PatchApplierTool,
    CommitPushTool,
    WaitForCITool,
    FetchCIResultsTool,
    VerificationTool,
)

# Reasoning loop orchestrator
from agents.reasoning_loop import (
    run_reasoning_loop,
    ReasoningLoopResult,
    IterationReport,
    build_default_registry,
)

__all__ = [
    "BaseAgent",
    # Tool infrastructure
    "AgentTool",
    "ToolResult",
    "ToolRegistry",
    # 8 tool agents
    "TestRunnerTool",
    "FailureClassifierTool",
    "FixPlannerTool",
    "PatchApplierTool",
    "CommitPushTool",
    "WaitForCITool",
    "FetchCIResultsTool",
    "VerificationTool",
    # Orchestration
    "run_reasoning_loop",
    "ReasoningLoopResult",
    "IterationReport",
    "build_default_registry",
]
