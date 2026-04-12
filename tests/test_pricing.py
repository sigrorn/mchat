# ------------------------------------------------------------------
# Component: test_pricing
# Responsibility: Tests for cost estimation and model lookup
# Collaborators: pricing
# ------------------------------------------------------------------
from __future__ import annotations

from mchat.pricing import estimate_cost, format_cost


class TestEstimateCost:
    def test_known_model(self):
        cost = estimate_cost("claude-sonnet-4", 1000, 500)
        assert cost is not None
        assert cost > 0

    def test_prefix_match(self):
        cost = estimate_cost("claude-sonnet-4-20250514", 1000, 500)
        assert cost is not None

    def test_gemini_models_prefix_stripped(self):
        cost = estimate_cost("models/gemini-2.5-pro", 1000, 500)
        assert cost is not None

    def test_unknown_model_returns_none(self):
        assert estimate_cost("unknown-model-xyz", 1000, 500) is None

    def test_zero_tokens(self):
        cost = estimate_cost("gpt-4.1", 0, 0)
        assert cost == 0.0


class TestMistralPricing:
    """#80 — Mistral model families must have pricing entries."""

    def test_mistral_large_pricing(self):
        cost = estimate_cost("mistral-large-latest", 1000, 500)
        assert cost is not None
        assert cost > 0

    def test_mistral_small_pricing(self):
        cost = estimate_cost("mistral-small-latest", 1000, 500)
        assert cost is not None
        assert cost > 0


class TestApertusPricing:
    """#156 — Apertus model must have a pricing entry."""

    def test_apertus_pricing(self):
        cost = estimate_cost("swiss-ai/Apertus-70B-Instruct-2509", 1000, 500)
        assert cost is not None
        assert cost > 0


class TestFormatCost:
    def test_format(self):
        assert format_cost(0.00123) == "$0.00123"
        assert format_cost(1.5) == "$1.50000"
