# ------------------------------------------------------------------
# Component: Conversation
# Responsibility: Data model for a chat conversation
# Collaborators: models.message
# ------------------------------------------------------------------
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from mchat.models.message import Message


@dataclass
class Conversation:
    title: str = "New Chat"
    id: int | None = None
    system_prompt: str = ""
    last_provider: str = ""
    spend_claude: float = 0.0
    spend_openai: float = 0.0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    messages: list[Message] = field(default_factory=list)
