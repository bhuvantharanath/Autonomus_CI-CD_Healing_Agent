"""Tool infrastructure for the autonomous agent workflow.

Provides:
  • AgentTool  — protocol that all tool-agents implement
  • ToolResult — structured return value from every tool execution
  • ToolRegistry — registers, validates, and looks up tools by name
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


# ── Tool result ──────────────────────────────────────────────────────

@dataclass
class ToolResult:
    """Structured output returned by every tool execution."""

    tool_name: str
    status: str               # "success" | "failure" | "skipped"
    summary: str = ""
    outputs: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "status": self.status,
            "summary": self.summary,
            "outputs": self.outputs,
            "errors": self.errors,
            "timestamp": self.timestamp,
        }


# ── Tool protocol ────────────────────────────────────────────────────

class AgentTool(ABC):
    """Protocol that every tool-agent in the reasoning loop implements.

    Attributes:
        name:        Unique tool identifier (e.g. "test_runner").
        description: Human-readable summary of what the tool does.
        input_keys:  State keys the tool reads from WorkflowState.
        output_keys: State keys the tool writes into WorkflowState.
    """

    name: str = "base_tool"
    description: str = ""
    input_keys: list[str] = []
    output_keys: list[str] = []

    @abstractmethod
    async def execute(self, state: dict[str, Any]) -> ToolResult:
        """Run the tool and return a structured ToolResult.

        The tool should only read keys listed in ``input_keys`` and
        write outputs into the ``ToolResult.outputs`` dict using only
        keys listed in ``output_keys``.
        """
        ...

    def __repr__(self) -> str:
        return f"<Tool: {self.name}>"


# ── Tool registry ───────────────────────────────────────────────────

class ToolRegistry:
    """Central registry of available agent tools.

    Usage::

        registry = ToolRegistry()
        registry.register(TestRunnerTool())
        tool = registry.get("test_runner")
        result = await tool.execute(state)
    """

    def __init__(self) -> None:
        self._tools: dict[str, AgentTool] = {}

    def register(self, tool: AgentTool) -> None:
        """Register a tool instance by its name."""
        if tool.name in self._tools:
            raise ValueError(f"Tool '{tool.name}' is already registered.")
        self._tools[tool.name] = tool

    def get(self, name: str) -> AgentTool:
        """Retrieve a tool by name. Raises KeyError if not found."""
        if name not in self._tools:
            raise KeyError(
                f"Tool '{name}' not found. Available: {list(self._tools.keys())}"
            )
        return self._tools[name]

    def list_tools(self) -> list[dict[str, Any]]:
        """Return a summary of all registered tools."""
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_keys": t.input_keys,
                "output_keys": t.output_keys,
            }
            for t in self._tools.values()
        ]

    def validate_io(self, tool_name: str, state: dict[str, Any]) -> list[str]:
        """Check that all required input_keys exist in *state*.

        Returns a list of missing keys (empty means valid).
        """
        tool = self.get(tool_name)
        missing = [k for k in tool.input_keys if k not in state]
        return missing

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools
