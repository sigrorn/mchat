# ------------------------------------------------------------------
# Component: pricing
# Responsibility: Estimated per-token pricing for supported models
# Collaborators: none
# ------------------------------------------------------------------
from __future__ import annotations

# Prices per 1M tokens: (input_rate, output_rate) in USD.
# These are approximate and may lag behind actual pricing changes.
_PRICES: dict[str, tuple[float, float]] = {
    # Anthropic Claude
    "claude-opus-4-20250514":   (15.00, 75.00),
    "claude-sonnet-4-20250514": (3.00,  15.00),
    "claude-haiku-4-20250414":  (0.80,  4.00),
    # OpenAI GPT-4.1
    "gpt-4.1":      (2.00, 8.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1-nano": (0.10, 0.40),
    # OpenAI GPT-4o
    "gpt-4o":      (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    # OpenAI o-series
    "o3":      (2.00, 8.00),
    "o3-mini": (1.10, 4.40),
}


def estimate_cost(
    model: str, input_tokens: int, output_tokens: int
) -> float | None:
    """Return estimated cost in USD, or None if model pricing is unknown."""
    rates = _PRICES.get(model)
    if rates is None:
        return None
    inp_rate, out_rate = rates
    return (input_tokens * inp_rate + output_tokens * out_rate) / 1_000_000


def format_cost(usd: float) -> str:
    """Format a dollar amount for display."""
    if usd < 0.01:
        return f"${usd:.4f}"
    return f"${usd:.2f}"
