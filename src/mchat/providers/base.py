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
        """Stream a response, yielding tokens as they arrive."""
        ...

    @abstractmethod
    def list_models(self) -> list[str]:
        """Return available model identifiers."""
        ...
