"""What each paid call of a book job cost, so every job keeps its own cost log.

Prices are OpenAI's list prices (USD) as of 2026-10; update them here if they change. Moderation is
free. Commons photos and R2 storage (inside the free tier) cost nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# USD per 1M tokens (input, output).
TOKEN_PRICES = {
    "gpt-5.4-mini": (0.75, 4.50),
    "gpt-5.4-nano": (0.20, 1.25),
    "gpt-5.4": (2.50, 15.00),
}
WEB_SEARCH_PER_CALL = 10.0 / 1000
# Audio, USD per minute: gpt-4o-mini-tts output and whisper-1 input.
TTS_PER_MIN = {"gpt-4o-mini-tts": 0.015}
STT_PER_MIN = {"whisper-1": 0.006}


def token_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    price_in, price_out = TOKEN_PRICES.get(model, TOKEN_PRICES["gpt-5.4"])
    return (input_tokens * price_in + output_tokens * price_out) / 1_000_000


@dataclass
class CostLog:
    """Running total and line items of one job (kept in ``generation_jobs.costs``)."""

    items: list[dict[str, Any]] = field(default_factory=list)

    @property
    def total(self) -> float:
        return round(sum(float(i.get("usd") or 0) for i in self.items), 6)

    def add(self, step: str, what: str, usd: float, **detail: Any) -> float:
        self.items.append({"step": step, "what": what, "usd": round(usd, 6), **detail})
        return usd

    def tokens(self, step: str, model: str, input_tokens: int, output_tokens: int, web_searches: int = 0) -> float:
        usd = token_cost(model, input_tokens, output_tokens) + web_searches * WEB_SEARCH_PER_CALL
        detail: dict[str, Any] = {"model": model, "in": input_tokens, "out": output_tokens}
        if web_searches:
            detail["web_searches"] = web_searches
        return self.add(step, "text", usd, **detail)

    def audio(self, step: str, model: str, seconds: float, *, speech: bool) -> float:
        per_min = (TTS_PER_MIN if speech else STT_PER_MIN).get(model, 0.015 if speech else 0.006)
        return self.add(step, "tts" if speech else "stt", per_min * seconds / 60, model=model, seconds=round(seconds, 2))

    def summary(self) -> dict[str, Any]:
        by_kind: dict[str, float] = {}
        for item in self.items:
            by_kind[item["what"]] = round(by_kind.get(item["what"], 0.0) + float(item["usd"]), 6)
        return {"usd": self.total, "by_kind": by_kind, "calls": len(self.items)}
