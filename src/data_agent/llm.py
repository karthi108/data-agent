"""Thin LLM client layer: one call in, text + token usage + cost out."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

# USD per 1M tokens (input, output). Source: OpenAI public pricing for gpt-4.1-mini.
# Update here if prices change; every cost figure in eval/ is computed from this table.
PRICES_PER_MTOK: dict[str, tuple[float, float]] = {
    "gpt-4.1-mini": (0.40, 1.60),
}


@dataclass(frozen=True)
class Completion:
    text: str
    input_tokens: int
    output_tokens: int
    latency_s: float
    model: str
    served_model: str = ""  # exact snapshot the API reports, e.g. gpt-4.1-mini-2025-04-14

    @property
    def cost_usd(self) -> float:
        return cost_usd(self.model, self.input_tokens, self.output_tokens)


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    if model not in PRICES_PER_MTOK:
        raise KeyError(f"no price for model {model!r}; add it to PRICES_PER_MTOK")
    price_in, price_out = PRICES_PER_MTOK[model]
    return (input_tokens * price_in + output_tokens * price_out) / 1_000_000


class LLMClient(Protocol):
    model: str

    def complete(self, prompt: str) -> Completion: ...


class OpenAIClient:
    """OpenAI chat completion, temperature 0, single user message."""

    def __init__(self, model: str = "gpt-4.1-mini", max_retries: int = 5) -> None:
        from openai import OpenAI  # imported lazily so tests never need the package configured

        self.model = model
        self._client = OpenAI(max_retries=max_retries, timeout=120.0)

    def complete(self, prompt: str) -> Completion:
        start = time.perf_counter()
        resp = self._client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )
        latency = time.perf_counter() - start
        usage = resp.usage
        return Completion(
            text=resp.choices[0].message.content or "",
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
            latency_s=latency,
            model=self.model,
            served_model=resp.model,
        )
