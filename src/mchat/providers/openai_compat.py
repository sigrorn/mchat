# ------------------------------------------------------------------
# Component: OpenAICompatibleProvider
# Responsibility: Shared base for providers that use the OpenAI SDK
#                 with an optional custom base_url (OpenAI, Gemini,
#                 Perplexity). Owns lazy client construction, the
#                 streaming loop, usage extraction, and list_models
#                 with a filter hook. Subclasses set provider_id,
#                 display_name, fallback models, base_url, and
#                 optionally override _filter_model or _on_stream_done.
# Collaborators: providers.base  (external: openai)
# ------------------------------------------------------------------
from __future__ import annotations

from collections.abc import Iterator

from mchat.models.message import Message, Provider
from mchat.providers.base import BaseProvider


class OpenAICompatibleProvider(BaseProvider):
    """Base class for providers that speak the OpenAI chat completions API."""

    _base_url: str | None = None
    _fallback_models: list[str] = []

    def __init__(self, api_key: str, default_model: str) -> None:
        super().__init__()
        self._api_key = api_key
        self._default_model = default_model
        self._client = None

    def _get_client(self):
        if self._client is None:
            import openai
            kwargs: dict = {"api_key": self._api_key}
            if self._base_url:
                kwargs["base_url"] = self._base_url
            self._client = openai.OpenAI(**kwargs)
        return self._client

    def stream(self, messages: list[Message], model: str | None = None) -> Iterator[str]:
        self.last_usage = None
        self.last_usage_estimated = False
        api_messages = self._format_messages(messages)
        try:
            response = self._get_client().chat.completions.create(
                model=model or self._default_model,
                messages=api_messages,
                stream=True,
                stream_options={"include_usage": True},
            )
        except TypeError:
            response = self._get_client().chat.completions.create(
                model=model or self._default_model,
                messages=api_messages,
                stream=True,
            )
        full_text = ""
        for chunk in response:
            if chunk.choices and chunk.choices[0].delta.content:
                token = chunk.choices[0].delta.content
                full_text += token
                yield token
            try:
                usage = chunk.usage
                if usage is not None:
                    self.last_usage = (
                        usage.prompt_tokens or 0,
                        usage.completion_tokens or 0,
                    )
            except AttributeError:
                pass

        self._on_stream_done(api_messages, full_text)

    def _on_stream_done(self, api_messages: list[dict], full_text: str) -> None:
        """Hook for post-stream processing. Override in subclasses
        that need usage estimation fallbacks (e.g. Gemini)."""

    def list_models(self) -> list[str]:
        try:
            resp = self._get_client().models.list()
            models = sorted(
                [m.id for m in resp.data if self._filter_model(m.id)],
                reverse=True,
            )
            return models if models else list(self._fallback_models)
        except Exception:
            return list(self._fallback_models)

    def _filter_model(self, model_id: str) -> bool:
        """Return True if model_id should be included in list_models().
        Override in subclasses to filter by prefix/pattern."""
        return True

    def _format_messages(self, messages: list[Message]) -> list[dict]:
        return self.format_messages_openai(messages, self.provider_id)
