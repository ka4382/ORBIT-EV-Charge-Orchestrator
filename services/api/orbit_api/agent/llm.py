"""LLM strategy: a manual Claude tool-use loop (Anthropic Python SDK, `client.beta.messages.create`).

A manual loop (rather than the SDK tool runner) because each turn must pass through server-side guardrails:
the per-query tool-call cap, a per-request USD cost cap that also bounds `max_tokens`, an audit row per tool
call, and the untrusted-data wrapping of every tool result. History is append-only (the assistant content
is echoed back unchanged), which keeps thinking blocks valid across turns.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import anthropic

from .config import AgentConfig, estimate_cost_usd
from .models import LlmUsage, RunResult
from .prompts import SYSTEM_PROMPT, user_turn
from .tools import BUDGET_ERROR, ToolExecutor

SERVER_FALLBACK_BETA = "server-side-fallback-2026-07-01"
MIN_CALL_TOKENS = 512
EST_FIRST_INPUT_TOKENS = 3000


class LlmUnavailableError(Exception):
    """The LLM path cannot answer (API error, refusal, misconfiguration). The service falls back offline."""

    def __init__(self, reason: str, usage: LlmUsage | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.usage = usage or LlmUsage()


def make_client(cfg: AgentConfig) -> Any:
    if cfg.client is not None:
        return cfg.client
    return anthropic.AsyncAnthropic(api_key=cfg.api_key, timeout=cfg.timeout_s, max_retries=2)


def _attr(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _text(content: list[Any]) -> str:
    return "\n".join(_attr(b, "text", "") for b in content if _attr(b, "type") == "text").strip()


def build_tool_results(calls: list[tuple[str, Any]]) -> list[dict[str, Any]]:
    """`tool_result` blocks for one user turn. `calls` = [(tool_use_id, ToolCall)]."""
    out: list[dict[str, Any]] = []
    for tool_use_id, call in calls:
        block: dict[str, Any] = {"type": "tool_result", "tool_use_id": tool_use_id, "content": call.content}
        if not call.ok:
            block["is_error"] = True
        out.append(block)
    return out


class LlmAgent:
    mode = "llm"

    def __init__(self, cfg: AgentConfig, client: Any | None = None) -> None:
        self.cfg = cfg
        self.client = client if client is not None else make_client(cfg)
        self.last_messages: list[dict[str, Any]] = []

    def _request(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], max_tokens: int, allow_tools: bool
    ) -> dict[str, Any]:
        req: dict[str, Any] = {
            "model": self.cfg.model,
            "max_tokens": max_tokens,
            "system": SYSTEM_PROMPT,
            "tools": tools,
            "messages": list(messages),
            "output_config": {"effort": self.cfg.effort},
            "cache_control": {"type": "ephemeral"},
        }
        if not allow_tools:
            req["tool_choice"] = {"type": "none"}
        if self.cfg.server_fallback:
            req["betas"] = [SERVER_FALLBACK_BETA]
            req["fallbacks"] = "default"
        return req

    def _affordable_tokens(self, usage: LlmUsage, est_input: int) -> int:
        p_in, p_out, _ = self.cfg.prices()
        remaining = self.cfg.cost_cap_usd - usage.cost_usd - est_input * p_in / 1_000_000
        if remaining <= 0:
            return 0
        return min(self.cfg.max_tokens, int(remaining * 1_000_000 / p_out))

    def _account(self, usage: LlmUsage, resp: Any) -> int:
        u = _attr(resp, "usage")
        i = int(_attr(u, "input_tokens", 0) or 0)
        o = int(_attr(u, "output_tokens", 0) or 0)
        cr = int(_attr(u, "cache_read_input_tokens", 0) or 0)
        cw = int(_attr(u, "cache_creation_input_tokens", 0) or 0)
        usage.llm_calls += 1
        usage.input_tokens += i
        usage.output_tokens += o
        usage.cache_read_input_tokens += cr
        usage.cache_creation_input_tokens += cw
        usage.cost_usd = round(
            usage.cost_usd
            + estimate_cost_usd(
                self.cfg, input_tokens=i, output_tokens=o, cache_read_tokens=cr, cache_write_tokens=cw
            ),
            6,
        )
        return i + cr + cw

    async def _create(self, req: dict[str, Any], usage: LlmUsage) -> Any:
        try:
            return await self.client.beta.messages.create(**req)
        except anthropic.AuthenticationError as exc:
            raise LlmUnavailableError("authentication_error", usage) from exc
        except anthropic.RateLimitError as exc:
            raise LlmUnavailableError("rate_limited", usage) from exc
        except anthropic.BadRequestError as exc:
            raise LlmUnavailableError("bad_request", usage) from exc
        except anthropic.APIStatusError as exc:
            raise LlmUnavailableError(f"api_error_{exc.status_code}", usage) from exc
        except anthropic.APIConnectionError as exc:  # includes timeouts
            raise LlmUnavailableError("connection_error", usage) from exc
        except Exception as exc:  # unexpected client failure: still answer, offline
            raise LlmUnavailableError(f"client_error:{type(exc).__name__}", usage) from exc

    async def run(self, question: str, executor: ToolExecutor, now: datetime | None = None) -> RunResult:
        usage = LlmUsage()
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": user_turn(question, now or datetime.now(UTC))}
        ]
        self.last_messages = messages
        tools = executor.specs()
        est_input = EST_FIRST_INPUT_TOKENS
        stopped: str | None = None
        answer = ""
        max_calls = executor.max_calls + 2  # one call per tool round + the final answer, bounded
        while True:
            allow_tools = executor.budget_left > 0
            max_tokens = self._affordable_tokens(usage, est_input)
            if max_tokens < MIN_CALL_TOKENS or usage.llm_calls >= max_calls:
                stopped = "cost_cap" if max_tokens < MIN_CALL_TOKENS else "llm_call_cap"
                break
            resp = await self._create(self._request(messages, tools, max_tokens, allow_tools), usage)
            est_input = self._account(usage, resp)
            stop_reason = _attr(resp, "stop_reason")
            content = list(_attr(resp, "content", []) or [])
            if stop_reason == "refusal":
                raise LlmUnavailableError("refusal", usage)
            tool_uses = [b for b in content if _attr(b, "type") == "tool_use"]
            if stop_reason != "tool_use" or not tool_uses:
                answer = _text(content)
                if stop_reason == "max_tokens":
                    stopped = "max_tokens"
                break
            messages.append({"role": "assistant", "content": content})
            results = []
            for tu in tool_uses:
                call = await executor.call(str(_attr(tu, "name")), _attr(tu, "input") or {})
                results.append((str(_attr(tu, "id")), call))
                est_input += len(call.content) // 3
            if any(c.error == BUDGET_ERROR for _, c in results):
                stopped = "tool_call_cap"
            messages.append({"role": "user", "content": build_tool_results(results)})
        if not answer:
            answer = (
                "I stopped before finishing this answer because the request reached its "
                + ("cost limit." if stopped == "cost_cap" else "step limit.")
                + " Please ask a narrower question."
            )
        return RunResult(answer=answer, usage=usage, stopped_reason=stopped)
