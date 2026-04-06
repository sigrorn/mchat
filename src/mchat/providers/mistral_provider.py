# ------------------------------------------------------------------
# Component: MistralProvider
# Responsibility: Mistral AI API integration (dedicated SDK)
# Collaborators: providers.base, mistralai SDK
# ------------------------------------------------------------------
from __future__ import annotations

from collections.abc import Iterator

from mistralai.client import Mistral

from mchat.models.message import Message, Provider, Role
from mchat.providers.base import BaseProvider

FALLBACK_MODELS = [
    "mistral-large-latest",
    "mistral-small-latest",
    "codestral-latest",
    "pixtral-large-latest",
]


class MistralProvider(BaseProvider):
    def __init__(self, api_key: str, default_model: str = "mistral-large-latest") -> None:
        super().__init__()
        self._client = Mistral(api_key=api_key)
        self._default_model = default_model

    @property
    def provider_id(self) -> Provider:
        return Provider.MISTRAL

    @property
    def display_name(self) -> str:
        return "Mistral"

    def stream(self, messages: list[Message], model: str | None = None) -> Iterator[str]:
        self.last_usage = None
        api_messages = self._format_messages(messages)
        response = self._client.chat.stream(
            model=model or self._default_model,
            messages=api_messages,
        )
        input_tokens = 0
        output_tokens = 0
        for event in response:
            data = event.data
            if data.choices and data.choices[0].delta.content:
                yield data.choices[0].delta.content
            if data.usage is not None:
                input_tokens = data.usage.prompt_tokens or 0
                output_tokens = data.usage.completion_tokens or 0
        if input_tokens or output_tokens:
            self.last_usage = (input_tokens, output_tokens)

    def list_models(self) -> list[str]:
        try:
            resp = self._client.models.list()
            models = sorted(
                [m.id for m in resp.data],
                reverse=True,
            )
            return models if models else FALLBACK_MODELS
        except Exception:
            return list(FALLBACK_MODELS)

    def _format_messages(self, messages: list[Message]) -> list[dict]:
        return self.format_messages_openai(messages, Provider.MISTRAL)
