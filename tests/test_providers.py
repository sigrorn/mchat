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


class TestOpenAICompatibleBase:
    """#87 — OpenAI-compatible providers share a base class."""

    def test_base_class_exists(self):
        from mchat.providers.openai_compat import OpenAICompatibleProvider
        assert issubclass(OpenAICompatibleProvider, BaseProvider)

    def test_openai_provider_uses_base(self):
        from mchat.providers.openai_provider import OpenAIProvider
        from mchat.providers.openai_compat import OpenAICompatibleProvider
        assert issubclass(OpenAIProvider, OpenAICompatibleProvider)

    def test_gemini_provider_uses_base(self):
        from mchat.providers.gemini_provider import GeminiProvider
        from mchat.providers.openai_compat import OpenAICompatibleProvider
        assert issubclass(GeminiProvider, OpenAICompatibleProvider)

    def test_perplexity_provider_uses_base(self):
        from mchat.providers.perplexity_provider import PerplexityProvider
        from mchat.providers.openai_compat import OpenAICompatibleProvider
        assert issubclass(PerplexityProvider, OpenAICompatibleProvider)

    def test_subclasses_do_not_define_stream(self):
        """stream() should live on the base class, not be overridden
        (except Gemini which adds usage estimation)."""
        from mchat.providers.openai_provider import OpenAIProvider
        from mchat.providers.perplexity_provider import PerplexityProvider
        from mchat.providers.openai_compat import OpenAICompatibleProvider
        # OpenAI and Perplexity should NOT override stream
        assert OpenAIProvider.stream is OpenAICompatibleProvider.stream
        assert PerplexityProvider.stream is OpenAICompatibleProvider.stream

    def test_subclasses_do_not_define_get_client(self):
        """_get_client() should live on the base class."""
        from mchat.providers.openai_provider import OpenAIProvider
        from mchat.providers.gemini_provider import GeminiProvider
        from mchat.providers.perplexity_provider import PerplexityProvider
        from mchat.providers.openai_compat import OpenAICompatibleProvider
        assert OpenAIProvider._get_client is OpenAICompatibleProvider._get_client
        assert GeminiProvider._get_client is OpenAICompatibleProvider._get_client
        assert PerplexityProvider._get_client is OpenAICompatibleProvider._get_client


class TestMistralProvider:
    """#80 — MistralProvider must exist and implement BaseProvider."""

    def test_provider_class_exists(self):
        from mchat.providers.mistral_provider import MistralProvider
        assert issubclass(MistralProvider, BaseProvider)

    def test_provider_id(self):
        """MistralProvider uses lazy init — construction doesn't need
        the SDK, so no patching required."""
        from mchat.providers.mistral_provider import MistralProvider
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
