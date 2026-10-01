"""Agent settings (env) and the per-model price table used for the per-request cost cap and estimate."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

# Project default (.env.example AGENT_MODEL). Exact first-party model id, no date suffix.
DEFAULT_MODEL = "claude-sonnet-5-5"

# USD per million tokens: (input, output, cache read). Cache writes bill at 1.25x input (5-minute TTL).
PRICES_PER_MTOK: dict[str, tuple[float, float, float]] = {
    "claude-sonnet-5-5": (2.00, 10.00, 0.20),
    "claude-sonnet-5": (2.00, 10.00, 0.20),
    "claude-opus-5-5": (4.00, 20.00, 0.20),
    "claude-opus-5": (5.00, 25.00, 0.50),
    "claude-haiku-4-5": (1.00, 5.00, 0.10),
}
FALLBACK_PRICE = (5.00, 25.00, 0.50)  # unknown model: assume Opus-tier so the cap stays conservative


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


@dataclass(frozen=True)
class AgentConfig:
    model: str = DEFAULT_MODEL
    api_key: str = ""
    max_tool_calls: int = 5
    max_tokens: int = 4096  # per Messages call (includes adaptive thinking)
    cost_cap_usd: float = 0.10  # per /agent/query request
    max_question_chars: int = 1000
    effort: str = "medium"
    server_fallback: bool = True  # `fallbacks: "default"` on refusals (Claude API only)
    timeout_s: float = 30.0
    client: Any = field(default=None, compare=False)  # injected Anthropic-compatible client (tests)

    @property
    def llm_enabled(self) -> bool:
        return bool(self.api_key) or self.client is not None

    def prices(self) -> tuple[float, float, float]:
        return PRICES_PER_MTOK.get(self.model, FALLBACK_PRICE)


def load_config() -> AgentConfig:
    return AgentConfig(
        model=os.environ.get("AGENT_MODEL", "").strip() or DEFAULT_MODEL,
        api_key=os.environ.get("ANTHROPIC_API_KEY", "").strip(),
        max_tool_calls=max(1, min(_env_int("AGENT_MAX_TOOL_CALLS", 5), 5)),
        max_tokens=max(256, _env_int("AGENT_MAX_TOKENS", 4096)),
        cost_cap_usd=max(0.0, _env_float("AGENT_COST_CAP_USD", 0.10)),
        max_question_chars=max(1, min(_env_int("AGENT_MAX_QUESTION_CHARS", 1000), 2000)),
        effort=os.environ.get("AGENT_EFFORT", "medium").strip() or "medium",
        server_fallback=os.environ.get("AGENT_SERVER_FALLBACK", "1").strip() not in ("0", "false", "no"),
        timeout_s=max(1.0, _env_float("AGENT_TIMEOUT_S", 30.0)),
    )


def estimate_cost_usd(
    cfg: AgentConfig,
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float:
    p_in, p_out, p_cache = cfg.prices()
    usd = (
        input_tokens * p_in
        + output_tokens * p_out
        + cache_read_tokens * p_cache
        + cache_write_tokens * p_in * 1.25
    ) / 1_000_000
    return round(usd, 6)
