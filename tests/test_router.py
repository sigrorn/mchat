# ------------------------------------------------------------------
# Component: test_router
# Responsibility: Tests for Router.parse and Router._strip_prefix
#                 under the #140 @-prefix grammar. Router.parse is
#                 the provider-shorthand-only parser (no persona
#                 awareness) kept for _strip_prefix consumers and
#                 a few legacy callers; PersonaResolver wraps it
#                 with conversation-scoped logic.
# Collaborators: router, models.message
# ------------------------------------------------------------------
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from mchat.models.message import Provider
from mchat.router import Router
from mchat.ui.persona_target import synthetic_default
from mchat.ui.state import SelectionState


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
        targets, text = router.parse("@claude explain this code")
        assert targets == [Provider.CLAUDE]
        assert text == "explain this code"

    def test_gpt_prefix(self, router):
        targets, text = router.parse("@gpt what do you think?")
        assert targets == [Provider.OPENAI]
        assert text == "what do you think?"

    def test_gemini_prefix(self, router):
        targets, text = router.parse("@gemini summarise this")
        assert targets == [Provider.GEMINI]
        assert text == "summarise this"

    def test_perplexity_prefix(self, router):
        targets, text = router.parse("@perplexity search for this")
        assert targets == [Provider.PERPLEXITY]
        assert text == "search for this"

    def test_pplx_prefix(self, router):
        targets, text = router.parse("@pplx search for this")
        assert targets == [Provider.PERPLEXITY]
        assert text == "search for this"

    def test_no_prefix_uses_default(self, router):
        targets, text = router.parse("just a normal message")
        assert targets == [Provider.CLAUDE]
        assert text == "just a normal message"

    def test_no_prefix_uses_last_used(self, router):
        router.parse("@gpt first message")
        targets, text = router.parse("follow up question")
        assert targets == [Provider.OPENAI]
        assert text == "follow up question"

    def test_case_insensitive(self, router):
        targets, text = router.parse("@Claude hello")
        assert targets == [Provider.CLAUDE]
        assert text == "hello"

        targets, text = router.parse("@GPT hello")
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
        target, text = router.parse("@gemini just this one")
        assert target == [Provider.GEMINI]
        assert text == "just this one"
        # Selection should now be sticky to gemini
        assert router.selection == [Provider.GEMINI]

    def test_both_prefix_removed(self, router):
        """'@both' should not be recognised — 'both' was an old alias
        that no longer exists."""
        target, text = router.parse("@both hello")
        # Unknown @ token — Router.parse treats unknown prefixes as
        # plain text (the PersonaResolver layer raises ResolveError
        # for command-level UX; Router.parse is lower-level).
        # So '@both hello' becomes plain text routed to current selection.
        assert text == "@both hello" or "both" in text

    def test_all_prefix(self, router, mock_providers):
        """'@all' selects all configured providers."""
        targets, text = router.parse("@all compare these")
        assert text == "compare these"
        assert set(targets) == set(mock_providers.keys())

    def test_all_prefix_sticky(self, router, mock_providers):
        """'@all' selection is sticky."""
        router.parse("@all first question")
        targets, text = router.parse("follow up")
        assert set(targets) == set(mock_providers.keys())

    def test_others_prefix(self, router, mock_providers):
        """'@others' (was 'flipped,') inverts the selection."""
        router.set_selection([Provider.CLAUDE])
        targets, text = router.parse("@others hello")
        assert text == "hello"
        assert Provider.CLAUDE not in targets
        assert len(targets) == len(mock_providers) - 1

    def test_others_sticky(self, router, mock_providers):
        """@others selection is sticky."""
        router.set_selection([Provider.CLAUDE])
        router.parse("@others first")
        targets, text = router.parse("follow up")
        assert Provider.CLAUDE not in targets

    def test_others_all_selected_noop(self, router, mock_providers):
        """@others when all are selected does nothing."""
        router.parse("@all setup")
        original = set(router.selection)
        targets, text = router.parse("@others hello")
        assert text == "hello"
        assert set(targets) == original

    # --- Multi-provider prefix tests ---

    def test_multi_provider_prefix(self, router):
        targets, text = router.parse("@claude @gemini what's your take?")
        assert targets == [Provider.CLAUDE, Provider.GEMINI]
        assert text == "what's your take?"

    def test_multi_provider_sticky(self, router):
        router.parse("@claude @gemini first question")
        targets, text = router.parse("follow up")
        assert targets == [Provider.CLAUDE, Provider.GEMINI]
        assert text == "follow up"

    def test_multi_provider_three(self, router):
        targets, text = router.parse("@gpt @claude @pplx compare these")
        assert set(targets) == {Provider.OPENAI, Provider.CLAUDE, Provider.PERPLEXITY}
        assert text == "compare these"

    def test_multi_provider_no_message(self, router):
        """All words are providers, no message — selection changes but message is empty."""
        targets, text = router.parse("@claude @gemini")
        assert targets == [Provider.CLAUDE, Provider.GEMINI]
        assert text == ""

    def test_all_not_combinable(self, router, mock_providers):
        """'@all' at the start is not combinable with others — if it's
        the first @ token, everything after it is message text."""
        targets, text = router.parse("@all @claude hello")
        assert set(targets) == set(mock_providers.keys())
        assert text == "@claude hello"

    def test_provider_then_all_in_message(self, router):
        """'@all' after real providers is treated as message text."""
        targets, text = router.parse("@claude @all hello")
        assert targets == [Provider.CLAUDE]
        assert text == "@all hello"

    def test_mid_sentence_provider_not_parsed(self, router):
        """Provider names mid-sentence are not parsed as prefixes —
        first non-@ token terminates @ parsing."""
        targets, text = router.parse("@gpt what about claude, do you agree?")
        assert targets == [Provider.OPENAI]
        assert text == "what about claude, do you agree?"

    def test_duplicate_provider_deduplicated(self, router):
        targets, text = router.parse("@claude @claude hello")
        assert targets == [Provider.CLAUDE]
        assert text == "hello"

    def test_natural_english_with_comma_no_longer_parsed(self, router):
        """#140 regression guard — 'ok, but what...' used to fail
        under the old <word>, grammar. Now it's just text."""
        targets, text = router.parse("ok, but what about X?")
        assert targets == [Provider.CLAUDE]  # current selection
        assert text == "ok, but what about X?"


class TestRouterEmptySelection:
    """Stage 3A.4 — empty selection is a valid state. Router must not
    auto-seed a default when the SelectionState is empty, and
    _store_selection([]) must write through instead of silently
    no-opping."""

    def test_empty_selection_state_not_seeded(self, mock_providers):
        """When Router receives an empty SelectionState, it must NOT
        force-seed a synthetic default. The selection stays empty."""
        state = SelectionState()
        assert state.selection == []
        Router(mock_providers, default=Provider.CLAUDE, selection_state=state)
        assert state.selection == []

    def test_store_selection_empty_writes_through(self, mock_providers):
        """set_selection([]) must actually clear the selection, not
        silently no-op."""
        state = SelectionState([synthetic_default(Provider.CLAUDE)])
        router = Router(mock_providers, default=Provider.CLAUDE, selection_state=state)
        router.set_selection([])
        assert state.selection == []
        assert router.selection == []

    def test_parse_with_empty_selection_returns_empty(self, mock_providers):
        """Unprefixed input with an empty selection returns [] — there
        is no implicit fallback to any provider."""
        state = SelectionState()
        router = Router(mock_providers, default=Provider.CLAUDE, selection_state=state)
        targets, text = router.parse("hello world")
        assert targets == []
        assert text == "hello world"

    def test_all_prefix_from_empty_selection(self, mock_providers):
        """'@all' from an empty selection selects every configured
        provider (same as today)."""
        state = SelectionState()
        router = Router(mock_providers, default=Provider.CLAUDE, selection_state=state)
        targets, text = router.parse("@all compare these")
        assert set(targets) == set(mock_providers.keys())
        assert text == "compare these"


class TestMistralPrefix:
    """#80 — Mistral as a provider must be routable via a prefix."""

    def test_mistral_prefix(self, mock_providers):
        router = Router(mock_providers, default=Provider.CLAUDE)
        targets, text = router.parse("@mistral explain this")
        assert targets == [Provider.MISTRAL]
        assert text == "explain this"


class TestStripPrefix:
    """Router._strip_prefix: used by context_builder to clean user
    messages before sending them as context. Must recognise the new
    @-grammar."""

    def test_strip_single_at_prefix(self):
        found, cleaned = Router._strip_prefix("@claude hello world")
        assert "claude" in found
        assert cleaned == "hello world"

    def test_strip_multi_at_prefix(self):
        found, cleaned = Router._strip_prefix("@claude @gpt compare")
        assert "claude" in found
        assert "gpt" in found
        assert cleaned == "compare"

    def test_strip_all_prefix(self):
        found, cleaned = Router._strip_prefix("@all hi everyone")
        assert "all" in found
        assert cleaned == "hi everyone"

    def test_strip_others_prefix(self):
        found, cleaned = Router._strip_prefix("@others continue")
        assert "others" in found
        assert cleaned == "continue"

    def test_strip_no_prefix_returns_text_unchanged(self):
        found, cleaned = Router._strip_prefix("plain text")
        assert found == []
        assert cleaned == "plain text"

    def test_strip_natural_english_comma_unchanged(self):
        """#140 — 'ok, hello' used to be treated as a prefix by the
        old grammar. Under @-grammar it's just text."""
        found, cleaned = Router._strip_prefix("ok, hello world")
        assert found == []
        assert cleaned == "ok, hello world"

    def test_strip_at_mid_text_unchanged(self):
        """'@' that isn't the first token is data, not a prefix."""
        found, cleaned = Router._strip_prefix("hello @claude")
        assert found == []
        assert cleaned == "hello @claude"
