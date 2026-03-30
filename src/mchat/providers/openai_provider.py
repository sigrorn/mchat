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
        self._client = openai.OpenAI(api_key=api_key)
        self._default_model = default_model

    @property
    def provider_id(self) -> Provider:
        return Provider.OPENAI

    @property
    def display_name(self) -> str:
        return "ChatGPT"

    def stream(self, messages: list[Message], model: str | None = None) -> Iterator[str]:
        api_messages = self._format_messages(messages)
        response = self._client.chat.completions.create(
            model=model or self._default_model,
            messages=api_messages,
            stream=True,
        )
        for chunk in response:
            delta = chunk.choices[0].delta
            if delta.content:
                yield delta.content

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

    @staticmethod
    def _format_messages(messages: list[Message]) -> list[dict]:
        """Convert normalized messages to OpenAI API format."""
        api_messages = []
        for msg in messages:
            if msg.role == Role.SYSTEM:
                api_messages.append({"role": "system", "content": msg.content})
                continue

            role = "user" if msg.role == Role.USER else "assistant"
            # If it's an assistant message from a different provider, include as
            # user context so the API contract stays user/assistant alternation.
            if msg.role == Role.ASSISTANT and msg.provider != Provider.OPENAI:
                provider_name = msg.provider.value.upper() if msg.provider else "ASSISTANT"
                content = f"[{provider_name} responded]: {msg.content}"
                role = "user"
            else:
                content = msg.content

            # Merge consecutive same-role messages
            if api_messages and api_messages[-1]["role"] == role:
                api_messages[-1]["content"] += "\n\n" + content
            else:
                api_messages.append({"role": role, "content": content})
        return api_messages
