# ------------------------------------------------------------------
# Component: ClaudeProvider
# Responsibility: Anthropic Claude API integration
# Collaborators: providers.base, anthropic SDK
# ------------------------------------------------------------------
from __future__ import annotations

from collections.abc import Iterator

import anthropic

from mchat.models.message import Message, Provider, Role
from mchat.providers.base import BaseProvider

FALLBACK_MODELS = [
    "claude-opus-4-20250514",
    "claude-sonnet-4-20250514",
    "claude-haiku-4-20250414",
]


class ClaudeProvider(BaseProvider):
    def __init__(self, api_key: str, default_model: str = "claude-sonnet-4-20250514") -> None:
        super().__init__()
        self._client = anthropic.Anthropic(api_key=api_key)
        self._default_model = default_model

    @property
    def provider_id(self) -> Provider:
        return Provider.CLAUDE

    @property
    def display_name(self) -> str:
        return "Claude"

    def stream(self, messages: list[Message], model: str | None = None) -> Iterator[str]:
        self.last_usage = None
        system_text, api_messages = self._format_messages(messages)
        kwargs: dict = dict(
            model=model or self._default_model,
            max_tokens=4096,
            messages=api_messages,
        )
        if system_text:
            kwargs["system"] = system_text
        with self._client.messages.stream(**kwargs) as stream:
            for text in stream.text_stream:
                yield text
            final = stream.get_final_message()
            self.last_usage = (final.usage.input_tokens, final.usage.output_tokens)

    def list_models(self) -> list[str]:
        try:
            resp = self._client.models.list(limit=100)
            models = sorted(
                [m.id for m in resp.data],
                reverse=True,
            )
            return models if models else FALLBACK_MODELS
        except Exception:
            return list(FALLBACK_MODELS)

    @staticmethod
    def _format_messages(messages: list[Message]) -> tuple[str, list[dict]]:
        """Convert normalized messages to Anthropic API format.

        Returns (system_text, api_messages).  System messages are
        extracted and joined for the ``system`` parameter.
        """
        system_parts: list[str] = []
        api_messages: list[dict] = []
        for msg in messages:
            if msg.role == Role.SYSTEM:
                system_parts.append(msg.content)
                continue
            role = "user" if msg.role == Role.USER else "assistant"
            # If it's an assistant message from a different provider, include as
            # user context so the API contract stays user/assistant alternation.
            if msg.role == Role.ASSISTANT and msg.provider != Provider.CLAUDE:
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
        return "\n\n".join(system_parts), api_messages
