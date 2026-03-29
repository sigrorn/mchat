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
    claude = MagicMock()
    claude.provider_id = Provider.CLAUDE
    openai = MagicMock()
    openai.provider_id = Provider.OPENAI
    return {Provider.CLAUDE: claude, Provider.OPENAI: openai}


@pytest.fixture
def router(mock_providers):
    return Router(mock_providers, default=Provider.CLAUDE)


class TestRouter:
    def test_claude_prefix(self, router):
        provider, text = router.parse("claude, explain this code")
        assert provider == Provider.CLAUDE
        assert text == "explain this code"

    def test_gpt_prefix(self, router):
        provider, text = router.parse("gpt, what do you think?")
        assert provider == Provider.OPENAI
        assert text == "what do you think?"

    def test_claude_prefix_colon(self, router):
        provider, text = router.parse("claude: hello")
        assert provider == Provider.CLAUDE
        assert text == "hello"

    def test_no_prefix_uses_default(self, router):
        provider, text = router.parse("just a normal message")
        assert provider == Provider.CLAUDE
        assert text == "just a normal message"

    def test_no_prefix_uses_last_used(self, router):
        router.parse("gpt, first message")
        provider, text = router.parse("follow up question")
        assert provider == Provider.OPENAI
        assert text == "follow up question"

    def test_case_insensitive(self, router):
        provider, text = router.parse("Claude, hello")
        assert provider == Provider.CLAUDE
        assert text == "hello"

        provider, text = router.parse("GPT, hello")
        assert provider == Provider.OPENAI
        assert text == "hello"

    def test_get_provider(self, router, mock_providers):
        assert router.get_provider(Provider.CLAUDE) is mock_providers[Provider.CLAUDE]
        assert router.get_provider(Provider.OPENAI) is mock_providers[Provider.OPENAI]
