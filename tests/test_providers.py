# ------------------------------------------------------------------
# Component: test_providers
# Responsibility: Tests for provider message formatting
# Collaborators: providers.base, models.message
# ------------------------------------------------------------------
from __future__ import annotations

from mchat.models.message import Message, Provider, Role
from mchat.providers.base import BaseProvider


class TestFormatMessagesOpenai:
    def test_basic_user_assistant(self):
        msgs = [
            Message(role=Role.USER, content="hello"),
            Message(role=Role.ASSISTANT, content="hi", provider=Provider.OPENAI),
        ]
        result = BaseProvider.format_messages_openai(msgs, Provider.OPENAI)
        assert result == [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]

    def test_system_message(self):
        msgs = [
            Message(role=Role.SYSTEM, content="Be helpful"),
            Message(role=Role.USER, content="hello"),
        ]
        result = BaseProvider.format_messages_openai(msgs, Provider.OPENAI)
        assert result[0] == {"role": "system", "content": "Be helpful"}

    def test_cross_provider_as_user_context(self):
        msgs = [
            Message(role=Role.ASSISTANT, content="I think X", provider=Provider.CLAUDE),
        ]
        result = BaseProvider.format_messages_openai(msgs, Provider.OPENAI)
        assert result[0]["role"] == "user"
        assert "[CLAUDE responded]" in result[0]["content"]

    def test_same_provider_stays_assistant(self):
        msgs = [
            Message(role=Role.ASSISTANT, content="I think X", provider=Provider.OPENAI),
        ]
        result = BaseProvider.format_messages_openai(msgs, Provider.OPENAI)
        assert result[0]["role"] == "assistant"

    def test_consecutive_same_role_merged(self):
        msgs = [
            Message(role=Role.USER, content="first"),
            Message(role=Role.USER, content="second"),
        ]
        result = BaseProvider.format_messages_openai(msgs, Provider.OPENAI)
        assert len(result) == 1
        assert "first" in result[0]["content"]
        assert "second" in result[0]["content"]

    def test_gemini_provider(self):
        msgs = [
            Message(role=Role.ASSISTANT, content="response", provider=Provider.OPENAI),
        ]
        result = BaseProvider.format_messages_openai(msgs, Provider.GEMINI)
        assert result[0]["role"] == "user"
        assert "[OPENAI responded]" in result[0]["content"]


class TestMistralProvider:
    """#80 — MistralProvider must exist and implement BaseProvider."""

    def test_provider_class_exists(self):
        from mchat.providers.mistral_provider import MistralProvider
        assert issubclass(MistralProvider, BaseProvider)

    def test_provider_id(self):
        from mchat.providers.mistral_provider import MistralProvider
        # Can't instantiate without a real key, but we can check the class
        # provides provider_id == Provider.MISTRAL
        from unittest.mock import patch, MagicMock
        with patch("mchat.providers.mistral_provider.Mistral") as mock_cls:
            mock_cls.return_value = MagicMock()
            p = MistralProvider(api_key="fake", default_model="mistral-large-latest")
            assert p.provider_id == Provider.MISTRAL
            assert p.display_name == "Mistral"

    def test_cross_provider_formatting_with_mistral(self):
        """Messages from Mistral should be reformatted as user context
        when sent to another provider."""
        msgs = [
            Message(role=Role.ASSISTANT, content="I think Y", provider=Provider.MISTRAL),
        ]
        result = BaseProvider.format_messages_openai(msgs, Provider.OPENAI)
        assert result[0]["role"] == "user"
        assert "[MISTRAL responded]" in result[0]["content"]
