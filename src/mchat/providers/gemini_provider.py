# ------------------------------------------------------------------
# Component: GeminiProvider
# Responsibility: Google Gemini API integration (OpenAI-compatible endpoint)
# Collaborators: providers.base, openai SDK
# ------------------------------------------------------------------
from __future__ import annotations

from collections.abc import Iterator

import openai

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
        self._client = openai.OpenAI(
            api_key=api_key,
            base_url=_GEMINI_BASE_URL,
        )
        self._default_model = default_model

    @property
    def provider_id(self) -> Provider:
        return Provider.GEMINI

    @property
    def display_name(self) -> str:
        return "Gemini"

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
            response = self._client.chat.completions.create(
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

    def list_models(self) -> list[str]:
        try:
            resp = self._client.models.list()
            models = sorted(
                [m.id for m in resp.data if "gemini" in m.id.lower()],
                reverse=True,
            )
            return models if models else FALLBACK_MODELS
        except Exception:
            return list(FALLBACK_MODELS)

    @staticmethod
    def _format_messages(messages: list[Message]) -> list[dict]:
        """Convert normalized messages to OpenAI-compatible format for Gemini."""
        api_messages = []
        for msg in messages:
            if msg.role == Role.SYSTEM:
                api_messages.append({"role": "system", "content": msg.content})
                continue

            role = "user" if msg.role == Role.USER else "assistant"
            if msg.role == Role.ASSISTANT and msg.provider != Provider.GEMINI:
                provider_name = msg.provider.value.upper() if msg.provider else "ASSISTANT"
                content = f"[{provider_name} responded]: {msg.content}"
                role = "user"
            else:
                content = msg.content

            if api_messages and api_messages[-1]["role"] == role:
                api_messages[-1]["content"] += "\n\n" + content
            else:
                api_messages.append({"role": role, "content": content})
        return api_messages
