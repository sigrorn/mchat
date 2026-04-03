# ------------------------------------------------------------------
# Component: BaseProvider
# Responsibility: Abstract interface for LLM providers
# Collaborators: models.message
# ------------------------------------------------------------------
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator

from mchat.models.message import Message, Provider


class BaseProvider(ABC):
    """Abstract base class for LLM providers."""

    def __init__(self) -> None:
        self.last_usage: tuple[int, int] | None = None  # (input_tokens, output_tokens)
        self.last_usage_estimated: bool = False

    @property
    @abstractmethod
    def provider_id(self) -> Provider:
        ...

    @property
    @abstractmethod
    def display_name(self) -> str:
        ...

    @abstractmethod
    def stream(self, messages: list[Message], model: str | None = None) -> Iterator[str]:
        """Stream a response, yielding tokens as they arrive.

        After the generator is exhausted, ``last_usage`` should hold
        the token counts for the request.
        """
        ...

    @abstractmethod
    def list_models(self) -> list[str]:
        """Return available model identifiers."""
        ...

    @staticmethod
    def format_messages_openai(
        messages: list[Message], own_provider: Provider,
    ) -> list[dict]:
        """Convert normalized messages to OpenAI-compatible API format.

        Shared by all OpenAI-compatible providers (OpenAI, Gemini, Perplexity).
        Claude uses a different format (separate system parameter).
        """
        api_messages: list[dict] = []
        for msg in messages:
            if msg.role == Role.SYSTEM:
                api_messages.append({"role": "system", "content": msg.content})
                continue

            role = "user" if msg.role == Role.USER else "assistant"
            if msg.role == Role.ASSISTANT and msg.provider != own_provider:
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
