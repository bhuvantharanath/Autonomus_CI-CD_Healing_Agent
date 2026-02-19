"""Base agent interface that all agent modules implement."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class AgentResult:
    """Standard result returned by every agent."""
    agent_name: str
    status: str  # "success" | "failure" | "skipped"
    summary: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def to_dict(self) -> dict:
        return {
            "agent_name": self.agent_name,
            "status": self.status,
            "summary": self.summary,
            "details": self.details,
            "errors": self.errors,
            "timestamp": self.timestamp,
        }


class BaseAgent(ABC):
    """Abstract base class for all self-healing agents."""

    name: str = "base"

    @abstractmethod
    async def run(self, context: dict[str, Any]) -> AgentResult:
        """Execute the agent's task.

        Args:
            context: Dictionary containing repo_path, config, and any
                     data produced by preceding agents.
        Returns:
            AgentResult with findings.
        """
        ...

    def __repr__(self) -> str:
        return f"<Agent: {self.name}>"
