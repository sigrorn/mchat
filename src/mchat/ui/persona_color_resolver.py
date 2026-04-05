# ------------------------------------------------------------------
# Component: PersonaColorResolver
# Responsibility: Per-conversation cache of persona colour overrides,
#                 queried by ChatWidget when rendering a message. The
#                 cache is keyed by (conversation_id, persona_id) and
#                 invalidates when the caller says so (persona add/
#                 edit/remove). See docs/plans/personas.md § Stage 3A.2.
#
#                 The resolver uses list_personas_including_deleted
#                 so tombstoned personas still paint their historical
#                 messages in the right colour.
# Collaborators: db, config, models.message, models.persona,
#                ui.persona_resolution
# ------------------------------------------------------------------
from __future__ import annotations

from mchat.config import Config
from mchat.db import Database
from mchat.models.message import Message, Role
from mchat.models.persona import Persona
from mchat.ui.persona_resolution import resolve_persona_color


class PersonaColorResolver:
    """Caches per-conversation persona colour overrides for the chat
    widget. Resolves ``message.persona_id`` to an effective colour
    via the D6b resolve_persona_color helper.
    """

    def __init__(self, db: Database, config: Config) -> None:
        self._db = db
        self._config = config
        self._conv_id: int | None = None
        self._cache: dict[str, Persona] = {}

    def set_conversation(self, conv_id: int | None) -> None:
        """Bind the resolver to a conversation and pre-load its
        persona rows into the cache. Called on conversation switch
        and after persona add/edit/remove signals."""
        self._conv_id = conv_id
        self._cache = {}
        if conv_id is not None:
            # Include deleted so historical messages still resolve
            for p in self._db.list_personas_including_deleted(conv_id):
                self._cache[p.id] = p

    def invalidate(self) -> None:
        """Drop the cache and reload for the current conversation.
        Cheaper than ``set_conversation(self._conv_id)`` because it
        skips the None check."""
        if self._conv_id is not None:
            self._cache = {
                p.id: p
                for p in self._db.list_personas_including_deleted(self._conv_id)
            }

    def color_for_message(self, message: Message) -> str | None:
        """Return the effective colour for a message if persona-aware
        resolution applies, otherwise ``None`` — the caller falls back
        to its legacy colour logic (provider default or user colour).
        """
        if message.role == Role.USER:
            return None
        if message.persona_id is None:
            return None
        persona = self._cache.get(message.persona_id)
        if persona is None:
            return None
        return resolve_persona_color(persona, self._config)
