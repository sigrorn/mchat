# ------------------------------------------------------------------
# Component: OpenAIProvider
# Responsibility: OpenAI ChatGPT API integration
# Collaborators: providers.base, openai SDK
# ------------------------------------------------------------------
from __future__ import annotations

import re
from collections.abc import Iterator

import openai

from mchat.models.message import Message, Provider, Role
from mchat.providers.base import BaseProvider

FALLBACK_MODELS = [
    "o3",
    "o3-mini",
    "gpt-4.1",
    "gpt-4.1-mini",
    "gpt-4.1-nano",
    "gpt-4o",
    "gpt-4o-mini",
]

# Prefixes that indicate a chat-capable model
_CHAT_PREFIXES = re.compile(r"^(gpt-|o\d|chatgpt-)")


class OpenAIProvider(BaseProvider):
    def __init__(self, api_key: str, default_model: str = "gpt-4.1") -> None:
        super().__init__()
        self._client = openai.OpenAI(api_key=api_key)
        self._default_model = default_model

    @property
    def provider_id(self) -> Provider:
        return Provider.OPENAI

    @property
    def display_name(self) -> str:
        return "ChatGPT"

    def stream(self, messages: list[Message], model: str | None = None) -> Iterator[str]:
        self.last_usage = None
        api_messages = self._format_messages(messages)
        try:
            response = self._client.chat.completions.create(
                model=model or self._default_model,
                messages=api_messages,
                stream=True,
                stream_options={"include_usage": True},
            )
        except TypeError:
            # Older SDK versions may not accept stream_options
            response = self._client.chat.completions.create(
                model=model or self._default_model,
                messages=api_messages,
                stream=True,
            )
        for chunk in response:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content
            try:
                usage = chunk.usage
                if usage is not None:
                    self.last_usage = (
                        usage.prompt_tokens or 0,
                        usage.completion_tokens or 0,
                    )
            except AttributeError:
                pass

    def list_models(self) -> list[str]:
        try:
            resp = self._client.models.list()
            models = sorted(
                [m.id for m in resp.data if _CHAT_PREFIXES.match(m.id)],
                reverse=True,
            )
            return models if models else FALLBACK_MODELS
        except Exception:
            return list(FALLBACK_MODELS)

    def _format_messages(self, messages: list[Message]) -> list[dict]:
        return self.format_messages_openai(messages, Provider.OPENAI)
