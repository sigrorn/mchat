# ------------------------------------------------------------------
# Component: Message
# Responsibility: Data model for a single chat message
# Collaborators: models.conversation
# ------------------------------------------------------------------
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class Role(Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class Provider(Enum):
    CLAUDE = "claude"
    OPENAI = "openai"
    GEMINI = "gemini"
    PERPLEXITY = "perplexity"
    MISTRAL = "mistral"


@dataclass
class Message:
    role: Role
    content: str
    provider: Provider | None = None
    model: str | None = None
    conversation_id: int | None = None
    id: int | None = None
    display_mode: str | None = None  # "cols", "lines", or None (single/legacy)
    pinned: bool = False
    pin_target: str | None = None  # "all" or comma-separated provider values
    addressed_to: str | None = None  # "all" or comma-separated provider values (user msgs)
    persona_id: str | None = None  # opaque persona id, or None for legacy/synthetic-default
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
