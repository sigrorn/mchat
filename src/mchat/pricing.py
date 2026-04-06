# ------------------------------------------------------------------
# Component: pricing
# Responsibility: Estimated per-token pricing for supported models
# Collaborators: none
# ------------------------------------------------------------------
from __future__ import annotations

# Prices per 1M tokens: (input_rate, output_rate) in USD.
# Keyed by base model family so that both dated IDs
# (claude-sonnet-4-20250514) and aliases (claude-sonnet-4-6) match.
_PRICES: dict[str, tuple[float, float]] = {
    # Anthropic Claude  (key = family prefix)
    "claude-opus-4":   (15.00, 75.00),
    "claude-sonnet-4": (3.00,  15.00),
    "claude-haiku-4":  (0.80,  4.00),
    # OpenAI GPT-4.1   (longer keys first in lookup)
    "gpt-4.1-nano": (0.10, 0.40),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1":      (2.00, 8.00),
    # OpenAI GPT-5.4
    "gpt-5.4-nano": (0.20, 1.25),
    "gpt-5.4":      (2.50, 15.00),
    # OpenAI GPT-4o
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o":      (2.50, 10.00),
    # OpenAI o-series
    "o3-mini": (1.10, 4.40),
    "o3":      (2.00, 8.00),
    # Google Gemini
    "gemini-2.5-pro":   (1.25, 10.00),
    "gemini-2.5-flash": (0.15, 0.60),
    "gemini-2.0-flash": (0.10, 0.40),
    # Mistral
    "mistral-large":  (2.00, 6.00),
    "mistral-small":  (0.10, 0.30),
    "mistral-medium": (0.40, 1.20),
    "codestral":      (0.30, 0.90),
    "pixtral-large":  (2.00, 6.00),
    # Perplexity Sonar
    "sonar-deep-research": (2.00, 8.00),
    "sonar-reasoning-pro": (2.00, 8.00),
    "sonar-pro":           (3.00, 15.00),
    "sonar":               (1.00, 1.00),
}


def _lookup_rates(model: str) -> tuple[float, float] | None:
    """Find pricing for a model by prefix match (longest key wins)."""
    # Gemini API returns model IDs like "models/gemini-2.5-pro"
    model = model.removeprefix("models/")
    for key, rates in sorted(_PRICES.items(), key=lambda kv: -len(kv[0])):
        if model.startswith(key):
            return rates
    return None


def estimate_cost(
    model: str, input_tokens: int, output_tokens: int
) -> float | None:
    """Return estimated cost in USD, or None if model pricing is unknown."""
    rates = _lookup_rates(model)
    if rates is None:
        return None
    inp_rate, out_rate = rates
    return (input_tokens * inp_rate + output_tokens * out_rate) / 1_000_000


def format_cost(usd: float) -> str:
    """Format a dollar amount for display."""
    return f"${usd:.5f}"
