"""Trusted, server-created identity for one Agent execution."""
from __future__ import annotations

import hashlib
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Optional

from app.models import Agent


@dataclass(frozen=True)
class ExecutionContext:
    """Security context that must never be populated from model tool arguments."""

    tenant_id: int
    agent_id: int
    execution_id: str

    @classmethod
    def for_agent(cls, agent: Agent, execution_id: str = "") -> "ExecutionContext":
        return cls(
            tenant_id=int(getattr(agent, "tenant_id", 0) or 0),
            agent_id=int(agent.id or 0),
            execution_id=(execution_id or "one-shot")[:160],
        )

    @property
    def scope_key(self) -> str:
        raw = f"{self.tenant_id}:{self.agent_id}:{self.execution_id}".encode("utf-8")
        suffix = hashlib.sha256(raw).hexdigest()[:24]
        return f"tenant-{self.tenant_id}-agent-{self.agent_id}-{suffix}"


_current_execution: ContextVar[Optional[ExecutionContext]] = ContextVar(
    "current_execution",
    default=None,
)


def set_execution_context(context: ExecutionContext) -> Token:
    return _current_execution.set(context)


def reset_execution_context(token: Token) -> None:
    _current_execution.reset(token)


def current_execution_context() -> Optional[ExecutionContext]:
    return _current_execution.get()
