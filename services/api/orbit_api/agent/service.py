"""AgentService: one /agent/query run. Picks the strategy (LLM when configured, else offline), falls back to
offline on LLM failure, and writes the audit trail: AGENT_QUERY -> AGENT_TOOL* -> AGENT_ANSWER, all with
actor_type 'agent' and actor_id = run id."""

from __future__ import annotations

import time
import uuid
from typing import Any

import structlog

from ..auth import Principal
from ..problem import ProblemException
from ..repositories.base import Repos
from ..schemas import AgentAction
from ..state import StateStore
from .config import AgentConfig
from .guard import clean_question, looks_like_injection
from .llm import LlmAgent, LlmUnavailableError
from .models import AgentQueryResponse, AgentUsage, LlmUsage, Mode, RunResult
from .offline import OfflineAgent
from .tools import ToolContext, ToolExecutor

log = structlog.get_logger("orbit_api.agent")
MAX_ANSWER_AUDIT_CHARS = 4000


class AgentService:
    def __init__(self, cfg: AgentConfig, *, llm: LlmAgent | None = None, offline: OfflineAgent | None = None):
        self.cfg = cfg
        self._llm = llm
        self._offline = offline or OfflineAgent()

    def _llm_agent(self) -> LlmAgent:
        if self._llm is None:
            self._llm = LlmAgent(self.cfg)
        return self._llm

    async def query(
        self, question: str, *, repos: Repos, store: StateStore | None, principal: Principal
    ) -> AgentQueryResponse:
        q = clean_question(question)
        if not q:
            raise ProblemException(422, "question is empty", type_="urn:orbit:problem:validation")
        if len(q) > self.cfg.max_question_chars:
            raise ProblemException(
                422,
                f"question is longer than {self.cfg.max_question_chars} characters",
                type_="urn:orbit:problem:validation",
            )
        run_id = f"agent-run-{uuid.uuid4().hex[:16]}"
        mode: Mode = "llm" if self.cfg.llm_enabled else "offline"
        t0 = time.perf_counter()
        audit_id = await repos.audit.insert(
            actor_type="agent",
            actor_id=run_id,
            action="AGENT_QUERY",
            resource=f"agent_run:{run_id}",
            details={
                "question": q,
                "model": self.cfg.model if mode == "llm" else None,
                "mode": mode,
                "user": principal.sub,
                "role": principal.role,
                "question_flagged": looks_like_injection(q),
            },
        )
        executor = ToolExecutor(
            ToolContext(repos=repos, store=store, principal=principal, run_id=run_id),
            max_calls=self.cfg.max_tool_calls,
        )
        fallback_reason: str | None = None
        llm_usage = LlmUsage()
        result: RunResult
        if mode == "llm":
            try:
                result = await self._llm_agent().run(q, executor)
                llm_usage = result.usage
            except LlmUnavailableError as exc:
                log.warning("agent llm unavailable, answering offline", reason=exc.reason, run_id=run_id)
                fallback_reason, llm_usage, mode = exc.reason, exc.usage, "offline"
                result = await self._offline.run(q, executor)
        else:
            result = await self._offline.run(q, executor)

        latency_ms = int((time.perf_counter() - t0) * 1000)
        usage = AgentUsage(
            model=self.cfg.model if (mode == "llm" or fallback_reason) else None,
            llm_calls=llm_usage.llm_calls,
            tool_calls=executor.executed,
            input_tokens=llm_usage.input_tokens,
            output_tokens=llm_usage.output_tokens,
            cache_read_input_tokens=llm_usage.cache_read_input_tokens,
            cache_creation_input_tokens=llm_usage.cache_creation_input_tokens,
            cost_usd=llm_usage.cost_usd,
            cost_cap_usd=self.cfg.cost_cap_usd,
            latency_ms=latency_ms,
            fallback_reason=fallback_reason,
            stopped_reason=result.stopped_reason,
        )
        actions = [AgentAction(**a) for a in executor.actions]
        details: dict[str, Any] = {
            "mode": mode,
            "usage": usage.model_dump(),
            "tool_calls": [c.name for c in executor.calls],
            "action_ids": [a.id for a in actions],
            "answer": result.answer[:MAX_ANSWER_AUDIT_CHARS],
        }
        await repos.audit.insert(
            actor_type="agent",
            actor_id=run_id,
            action="AGENT_ANSWER",
            resource=f"agent_run:{run_id}",
            details=details,
        )
        log.info(
            "agent answered",
            run_id=run_id,
            mode=mode,
            tools=len(executor.calls),
            cost_usd=usage.cost_usd,
            latency_ms=latency_ms,
        )
        return AgentQueryResponse(
            answer=result.answer, actions=actions, audit_id=audit_id, mode=mode, usage=usage
        )
