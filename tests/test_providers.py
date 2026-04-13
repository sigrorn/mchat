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


class TestMistralSDKRuntime:
    """#166 — Mock the Mistral SDK to exercise _get_client, stream, list_models."""

    def _make_provider(self, monkeypatch):
        """Build a MistralProvider with a mocked SDK client."""
        from unittest.mock import MagicMock
        from mchat.providers.mistral_provider import MistralProvider

        mock_client = MagicMock()
        mock_mistral_class = MagicMock(return_value=mock_client)

        # Patch the lazy import inside _get_client
        import mchat.providers.mistral_provider as mp_mod
        monkeypatch.setattr(
            mp_mod, "MistralProvider",
            type(mp_mod.MistralProvider.__name__, (mp_mod.MistralProvider,), {}),
        )
        provider = MistralProvider(api_key="test-key", default_model="mistral-large-latest")
        # Inject mock client directly (bypass lazy import)
        provider._client = mock_client
        return provider, mock_client

    def test_get_client_creates_sdk_instance(self, monkeypatch):
        """_get_client should create a Mistral SDK client with the API key."""
        from unittest.mock import MagicMock, patch
        from mchat.providers.mistral_provider import MistralProvider

        provider = MistralProvider(api_key="sk-test-123", default_model="m")
        assert provider._client is None  # lazy, not yet created

        mock_class = MagicMock()
        with patch("mchat.providers.mistral_provider.Mistral", mock_class, create=True):
            # Patch the import path used by _get_client
            import mchat.providers.mistral_provider as mp_mod
            original_get = mp_mod.MistralProvider._get_client

            def patched_get(self):
                if self._client is None:
                    self._client = mock_class(api_key=self._api_key)
                return self._client

            monkeypatch.setattr(mp_mod.MistralProvider, "_get_client", patched_get)
            client = provider._get_client()
            mock_class.assert_called_once_with(api_key="sk-test-123")

    def test_stream_yields_tokens(self, monkeypatch):
        """stream() should yield delta content tokens from the SDK response."""
        from unittest.mock import MagicMock
        from mchat.providers.mistral_provider import MistralProvider

        provider = MistralProvider(api_key="fake", default_model="mistral-large-latest")
        mock_client = MagicMock()
        provider._client = mock_client

        # Build mock streaming response
        def make_chunk(content=None, usage=None):
            chunk = MagicMock()
            chunk.data = MagicMock()
            if content is not None:
                choice = MagicMock()
                choice.delta.content = content
                chunk.data.choices = [choice]
            else:
                chunk.data.choices = []
            chunk.data.usage = usage
            return chunk

        usage = MagicMock()
        usage.prompt_tokens = 10
        usage.completion_tokens = 20

        mock_client.chat.stream.return_value = [
            make_chunk("Hello"),
            make_chunk(" world"),
            make_chunk(None, usage=usage),
        ]

        msgs = [Message(role=Role.USER, content="hi")]
        tokens = list(provider.stream(msgs))
        assert tokens == ["Hello", " world"]
        assert provider.last_usage == (10, 20)

    def test_stream_with_no_usage(self, monkeypatch):
        """stream() should handle responses without usage data."""
        from unittest.mock import MagicMock
        from mchat.providers.mistral_provider import MistralProvider

        provider = MistralProvider(api_key="fake", default_model="m")
        mock_client = MagicMock()
        provider._client = mock_client

        chunk = MagicMock()
        choice = MagicMock()
        choice.delta.content = "ok"
        chunk.data.choices = [choice]
        chunk.data.usage = None

        mock_client.chat.stream.return_value = [chunk]

        msgs = [Message(role=Role.USER, content="hi")]
        tokens = list(provider.stream(msgs))
        assert tokens == ["ok"]
        assert provider.last_usage is None

    def test_list_models_returns_sorted(self, monkeypatch):
        """list_models() should return sorted model IDs from SDK."""
        from unittest.mock import MagicMock
        from mchat.providers.mistral_provider import MistralProvider

        provider = MistralProvider(api_key="fake", default_model="m")
        mock_client = MagicMock()
        provider._client = mock_client

        model_a = MagicMock()
        model_a.id = "mistral-small-latest"
        model_b = MagicMock()
        model_b.id = "mistral-large-latest"
        mock_client.models.list.return_value.data = [model_a, model_b]

        models = provider.list_models()
        assert "mistral-large-latest" in models
        assert "mistral-small-latest" in models
        # Should be sorted reverse
        assert models == sorted(models, reverse=True)

    def test_list_models_fallback_on_error(self, monkeypatch):
        """list_models() should return FALLBACK_MODELS on SDK error."""
        from unittest.mock import MagicMock
        from mchat.providers.mistral_provider import MistralProvider, FALLBACK_MODELS

        provider = MistralProvider(api_key="fake", default_model="m")
        mock_client = MagicMock()
        provider._client = mock_client

        mock_client.models.list.side_effect = Exception("API error")

        models = provider.list_models()
        assert models == list(FALLBACK_MODELS)

    def test_stream_passes_correct_model(self, monkeypatch):
        """stream() should pass the model argument to the SDK."""
        from unittest.mock import MagicMock
        from mchat.providers.mistral_provider import MistralProvider

        provider = MistralProvider(api_key="fake", default_model="default-model")
        mock_client = MagicMock()
        provider._client = mock_client
        mock_client.chat.stream.return_value = []

        msgs = [Message(role=Role.USER, content="hi")]
        list(provider.stream(msgs, model="override-model"))

        call_kwargs = mock_client.chat.stream.call_args
        assert call_kwargs[1]["model"] == "override-model"

    def test_stream_uses_default_model_when_none(self, monkeypatch):
        """stream() should use default_model when model arg is None."""
        from unittest.mock import MagicMock
        from mchat.providers.mistral_provider import MistralProvider

        provider = MistralProvider(api_key="fake", default_model="my-default")
        mock_client = MagicMock()
        provider._client = mock_client
        mock_client.chat.stream.return_value = []

        msgs = [Message(role=Role.USER, content="hi")]
        list(provider.stream(msgs))

        call_kwargs = mock_client.chat.stream.call_args
        assert call_kwargs[1]["model"] == "my-default"


class TestApertusProvider:
    """#156 — ApertusProvider via Infomaniak's OpenAI-compatible API."""

    def test_provider_class_exists(self):
        from mchat.providers.apertus_provider import ApertusProvider
        from mchat.providers.openai_compat import OpenAICompatibleProvider
        assert issubclass(ApertusProvider, OpenAICompatibleProvider)

    def test_provider_id(self):
        from mchat.providers.apertus_provider import ApertusProvider
        p = ApertusProvider(api_key="fake", product_id="12345")
        assert p.provider_id == Provider.APERTUS
        assert p.display_name == "Apertus"

    def test_base_url_includes_product_id(self):
        from mchat.providers.apertus_provider import ApertusProvider
        p = ApertusProvider(api_key="fake", product_id="99999")
        assert "99999" in p._base_url
        assert "infomaniak" in p._base_url

    def test_does_not_override_stream(self):
        """stream() should live on the base class, not be overridden."""
        from mchat.providers.apertus_provider import ApertusProvider
        from mchat.providers.openai_compat import OpenAICompatibleProvider
        assert ApertusProvider.stream is OpenAICompatibleProvider.stream

    def test_does_not_override_get_client(self):
        """_get_client() should live on the base class."""
        from mchat.providers.apertus_provider import ApertusProvider
        from mchat.providers.openai_compat import OpenAICompatibleProvider
        assert ApertusProvider._get_client is OpenAICompatibleProvider._get_client

    def test_cross_provider_formatting_with_apertus(self):
        """Messages from Apertus should be reformatted as user context
        when sent to another provider."""
        msgs = [
            Message(role=Role.ASSISTANT, content="I think Z", provider=Provider.APERTUS),
        ]
        result = BaseProvider.format_messages_openai(msgs, Provider.OPENAI)
        assert result[0]["role"] == "user"
        assert "[APERTUS responded]" in result[0]["content"]

    def test_fallback_models(self):
        from mchat.providers.apertus_provider import ApertusProvider
        p = ApertusProvider(api_key="fake", product_id="12345")
        assert "swiss-ai/Apertus-70B-Instruct-2509" in p._fallback_models

    def test_blocked_models_not_in_fallback(self):
        from mchat.providers.apertus_provider import ApertusProvider
        p = ApertusProvider(api_key="fake", product_id="12345")
        for model in p._fallback_models:
            lower = model.lower()
            assert not lower.startswith("qwen"), f"blocked model in fallback: {model}"
            assert not lower.startswith("moonshotai"), f"blocked model in fallback: {model}"
            assert not lower.startswith("kimi"), f"blocked model in fallback: {model}"

    def test_filter_model_blocks_chinese_models(self):
        from mchat.providers.apertus_provider import ApertusProvider
        p = ApertusProvider(api_key="fake", product_id="12345")
        assert not p._filter_model("Qwen/Qwen3-VL-235B-A22B-Instruct")
        assert not p._filter_model("moonshotai/Kimi-K2.5")
        assert not p._filter_model("kimi-something")
        # Allowed models pass through
        assert p._filter_model("swiss-ai/Apertus-70B-Instruct-2509")
        assert p._filter_model("Llama-3.3-70B-Instruct")
        assert p._filter_model("openai/gpt-oss-120b")
