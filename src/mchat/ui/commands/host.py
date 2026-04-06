# ------------------------------------------------------------------
# Component: CommandHost
# Responsibility: Typing-only Protocol that documents the surface a
#                 command handler is allowed to touch on its host.
#                 Not runtime-enforced — handlers still duck-type
#                 against the real MainWindow — but annotating
#                 handlers as (host: CommandHost) removes the
#                 concrete dependency and gives IDEs a narrow view
#                 of the allowed interface.
# Collaborators: models.conversation, models.message, db, router
# ------------------------------------------------------------------
from __future__ import annotations

from typing import Any, Protocol

from mchat.db import Database
from mchat.models.conversation import Conversation
from mchat.models.message import Message, Provider
from mchat.router import Router


class CommandHost(Protocol):
    """The subset of MainWindow that // command handlers are allowed
    to read and mutate. This is documentation-level: Python does not
    enforce it at runtime, but every handler in the ``commands``
    package takes ``host: CommandHost`` so the coupling is explicit."""

    # Core services
    _db: Database
    _router: Router | None
    _current_conv: Conversation | None
    _column_mode: bool

    # UI surfaces commands reach into
    _chat: Any  # ChatWidget — keeping Any avoids a heavy import loop
    _input: Any  # InputWidget
    _sidebar: Any  # Sidebar

    # Retry stash (forwarded from SendController via properties)
    _retry_failed: dict[Provider, tuple[str, bool]]
    _retry_error_msg_ids: dict[Provider, int | None]
    _retry_contexts: dict[Provider, list[Message]]

    # Operations commands can invoke
    def _on_new_chat(self) -> None: ...
    def _display_messages(self, messages: list[Message]) -> None: ...
    def _save_selection(self) -> None: ...
    def _sync_checkboxes_from_selection(self) -> None: ...
    def _update_input_placeholder(self) -> None: ...
    def _update_input_color(self) -> None: ...
    def _toggle_column_mode(self) -> None: ...
    def _on_personas_requested(self, conv_id: int) -> None: ...
    def _sync_toolbar_personas(self) -> None: ...
    def _send_multi(
        self,
        targets: list[Provider],
        context_override: dict[Provider, list[Message]] | None = None,
    ) -> None: ...
