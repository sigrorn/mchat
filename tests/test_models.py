# ------------------------------------------------------------------
# Component: test_models
# Responsibility: Tests for data models
# Collaborators: models.message, models.conversation
# ------------------------------------------------------------------
from __future__ import annotations

from mchat.models.conversation import Conversation
from mchat.models.message import Message, Provider, Role


class TestMessage:
    def test_user_message(self):
        msg = Message(role=Role.USER, content="hello")
        assert msg.role == Role.USER
        assert msg.content == "hello"
        assert msg.provider is None

    def test_assistant_message(self):
        msg = Message(role=Role.ASSISTANT, content="hi", provider=Provider.CLAUDE)
        assert msg.provider == Provider.CLAUDE


class TestProvider:
    def test_all_providers_exist(self):
        assert Provider.CLAUDE.value == "claude"
        assert Provider.OPENAI.value == "openai"
        assert Provider.GEMINI.value == "gemini"
        assert Provider.PERPLEXITY.value == "perplexity"

    def test_provider_from_string(self):
        assert Provider("gemini") == Provider.GEMINI
        assert Provider("perplexity") == Provider.PERPLEXITY


class TestConversation:
    def test_defaults(self):
        conv = Conversation()
        assert conv.title == "New Chat"
        assert conv.messages == []
