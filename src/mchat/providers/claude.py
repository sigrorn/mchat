# ------------------------------------------------------------------
# Component: ClaudeProvider
# Responsibility: Anthropic Claude API integration
# Collaborators: providers.base  (external: anthropic)
# ------------------------------------------------------------------
from __future__ import annotations

from collections.abc import Iterator

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
        self._api_key = api_key
        self._default_model = default_model
        self._client = None

    def _get_client(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic(api_key=self._api_key)
        return self._client

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
        with self._get_client().messages.stream(**kwargs) as stream:
            for text in stream.text_stream:
                yield text
            final = stream.get_final_message()
            self.last_usage = (final.usage.input_tokens, final.usage.output_tokens)

    def list_models(self) -> list[str]:
        try:
            resp = self._get_client().models.list(limit=100)
            models = sorted(
                [m.id for m in resp.data],
                reverse=True,
            )
            return models if models else FALLBACK_MODELS
        except Exception:
            return list(FALLBACK_MODELS)

    def _format_messages(self, messages: list[Message]) -> tuple[str, list[dict]]:
        """Convert normalized messages to Anthropic API format.

        Returns (system_text, api_messages).  System messages are
        extracted for the ``system`` parameter; the rest use the
        shared OpenAI-compatible formatting.
        """
        system_parts: list[str] = []
        non_system: list[Message] = []
        for msg in messages:
            if msg.role == Role.SYSTEM:
                system_parts.append(msg.content)
            else:
                non_system.append(msg)
        api_messages = self.format_messages_openai(non_system, Provider.CLAUDE)
        return "\n\n".join(system_parts), api_messages
