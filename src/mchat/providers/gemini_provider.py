# ------------------------------------------------------------------
# Component: GeminiProvider
# Responsibility: Google Gemini API integration (OpenAI-compatible endpoint)
# Collaborators: providers.base, openai SDK
# ------------------------------------------------------------------
from __future__ import annotations

from collections.abc import Iterator

from mchat.models.message import Message, Provider, Role
from mchat.providers.base import BaseProvider

FALLBACK_MODELS = [
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.0-flash",
]

_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"


class GeminiProvider(BaseProvider):
    def __init__(self, api_key: str, default_model: str = "gemini-2.5-flash") -> None:
        super().__init__()
        self._api_key = api_key
        self._default_model = default_model
        self._client = None

    def _get_client(self):
        if self._client is None:
            import openai
            self._client = openai.OpenAI(
                api_key=self._api_key,
                base_url=_GEMINI_BASE_URL,
            )
        return self._client

    @property
    def provider_id(self) -> Provider:
        return Provider.GEMINI

    @property
    def display_name(self) -> str:
        return "Gemini"

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

        # Gemini's OpenAI-compat endpoint may not return usage data.
        # Fall back to a rough estimate (~4 chars per token).
        if self.last_usage is None:
            input_chars = sum(len(m["content"]) for m in api_messages)
            self.last_usage = (input_chars // 4, len(full_text) // 4)
            self.last_usage_estimated = True

    def list_models(self) -> list[str]:
        try:
            resp = self._get_client().models.list()
            models = sorted(
                [m.id for m in resp.data if "gemini" in m.id.lower()],
                reverse=True,
            )
            return models if models else FALLBACK_MODELS
        except Exception:
            return list(FALLBACK_MODELS)

    def _format_messages(self, messages: list[Message]) -> list[dict]:
        return self.format_messages_openai(messages, Provider.GEMINI)
        return api_messages
