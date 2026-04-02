# ------------------------------------------------------------------
# Component: test_router
# Responsibility: Tests for message routing and prefix parsing
# Collaborators: router, models.message
# ------------------------------------------------------------------
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from mchat.models.message import Provider
from mchat.router import Router


@pytest.fixture
def mock_providers():
    providers = {}
    for p in Provider:
        mock = MagicMock()
        mock.provider_id = p
        providers[p] = mock
    return providers


@pytest.fixture
def router(mock_providers):
    return Router(mock_providers, default=Provider.CLAUDE)


class TestRouter:
    def test_claude_prefix(self, router):
        targets, text = router.parse("claude, explain this code")
        assert targets == [Provider.CLAUDE]
        assert text == "explain this code"

    def test_gpt_prefix(self, router):
        targets, text = router.parse("gpt, what do you think?")
        assert targets == [Provider.OPENAI]
        assert text == "what do you think?"

    def test_gemini_prefix(self, router):
        targets, text = router.parse("gemini, summarise this")
        assert targets == [Provider.GEMINI]
        assert text == "summarise this"

    def test_perplexity_prefix(self, router):
        targets, text = router.parse("perplexity, search for this")
        assert targets == [Provider.PERPLEXITY]
        assert text == "search for this"

    def test_pplx_prefix(self, router):
        targets, text = router.parse("pplx, search for this")
        assert targets == [Provider.PERPLEXITY]
        assert text == "search for this"

    def test_claude_prefix_colon(self, router):
        targets, text = router.parse("claude: hello")
        assert targets == [Provider.CLAUDE]
        assert text == "hello"

    def test_no_prefix_uses_default(self, router):
        targets, text = router.parse("just a normal message")
        assert targets == [Provider.CLAUDE]
        assert text == "just a normal message"

    def test_no_prefix_uses_last_used(self, router):
        router.parse("gpt, first message")
        targets, text = router.parse("follow up question")
        assert targets == [Provider.OPENAI]
        assert text == "follow up question"

    def test_case_insensitive(self, router):
        targets, text = router.parse("Claude, hello")
        assert targets == [Provider.CLAUDE]
        assert text == "hello"

        targets, text = router.parse("GPT, hello")
        assert targets == [Provider.OPENAI]
        assert text == "hello"

    def test_get_provider(self, router, mock_providers):
        assert router.get_provider(Provider.CLAUDE) is mock_providers[Provider.CLAUDE]
        assert router.get_provider(Provider.OPENAI) is mock_providers[Provider.OPENAI]

    def test_select_single(self, router):
        router.set_selection([Provider.GEMINI])
        assert router.selection == [Provider.GEMINI]
        # Unprefixed message should go to selection
        target, text = router.parse("hello")
        assert target == [Provider.GEMINI]

    def test_select_multiple(self, router):
        router.set_selection([Provider.CLAUDE, Provider.OPENAI])
        assert router.selection == [Provider.CLAUDE, Provider.OPENAI]
        target, text = router.parse("hello")
        assert target == [Provider.CLAUDE, Provider.OPENAI]

    def test_prefix_overrides_selection_for_one_message(self, router):
        router.set_selection([Provider.CLAUDE, Provider.OPENAI])
        target, text = router.parse("gemini, just this one")
        assert target == [Provider.GEMINI]
        assert text == "just this one"
        # Selection should now be sticky to gemini
        assert router.selection == [Provider.GEMINI]

    def test_both_prefix_removed(self, router):
        """'both,' prefix should no longer be recognised."""
        target, text = router.parse("both, hello")
        # "both" is not a valid prefix, treated as plain text
        assert text == "both, hello"

    def test_all_prefix(self, router, mock_providers):
        """'all,' selects all configured providers."""
        targets, text = router.parse("all, compare these")
        assert text == "compare these"
        assert set(targets) == set(mock_providers.keys())

    def test_all_prefix_sticky(self, router, mock_providers):
        """'all,' selection is sticky."""
        router.parse("all, first question")
        targets, text = router.parse("follow up")
        assert set(targets) == set(mock_providers.keys())

    def test_flipped_prefix(self, router, mock_providers):
        """'flipped,' inverts the selection."""
        router.set_selection([Provider.CLAUDE])
        targets, text = router.parse("flipped, hello")
        assert text == "hello"
        assert Provider.CLAUDE not in targets
        assert len(targets) == len(mock_providers) - 1

    def test_flipped_sticky(self, router, mock_providers):
        """Flipped selection is sticky."""
        router.set_selection([Provider.CLAUDE])
        router.parse("flipped, first")
        targets, text = router.parse("follow up")
        assert Provider.CLAUDE not in targets

    def test_flipped_all_selected_noop(self, router, mock_providers):
        """Flipping when all are selected does nothing."""
        router.parse("all, setup")
        original = set(router.selection)
        targets, text = router.parse("flipped, hello")
        assert text == "hello"
        assert set(targets) == original
