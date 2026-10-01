"""Response models for /agent/* (extend the contract shapes in orbit_api.schemas) and run bookkeeping."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from ..schemas import AgentAnswer, AgentDecision

Mode = Literal["llm", "offline"]


class AgentUsage(BaseModel):
    """Per-request accounting. Token fields are 0 in offline mode."""

    model: str | None = None
    llm_calls: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cost_usd: float = 0.0
    cost_cap_usd: float = 0.0
    latency_ms: int = 0
    fallback_reason: str | None = Field(
        default=None, description="Set when LLM mode failed and the offline agent answered instead"
    )
    stopped_reason: str | None = Field(
        default=None, description="tool_call_cap | cost_cap | max_tokens when the run was cut short"
    )


class AgentQueryResponse(AgentAnswer):
    """`{answer, actions, audit_id}` per contracts/api.md, plus `mode` and `usage`."""

    mode: Mode
    usage: AgentUsage


class AgentExecution(BaseModel):
    status: Literal["EXECUTED", "FAILED"]
    plan_ids: list[int] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class AgentDecisionResult(AgentDecision):
    """`{id, status}` per contract; `execution` is present only when approving ran an action (REPLAN)."""

    execution: AgentExecution | None = None


@dataclass(slots=True)
class LlmUsage:
    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cost_usd: float = 0.0


@dataclass(slots=True)
class RunResult:
    answer: str
    usage: LlmUsage
    stopped_reason: str | None = None
