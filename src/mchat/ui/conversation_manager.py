# ------------------------------------------------------------------
# Component: ConversationManager
# Responsibility: Own the conversation lifecycle outside MainWindow —
#                 listing, selecting, creating, renaming, exporting
#                 and deleting conversations. Data-layer access (db,
#                 session state, router selection) goes through the
#                 ServicesContext. Presentational side-effects go
#                 through a narrow ConversationHost Protocol — the
#                 concrete MainWindow type is never imported at
#                 runtime.
# Collaborators: services.ServicesContext, ConversationHost (Protocol),
#                html_exporter, PySide6
# ------------------------------------------------------------------
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from PySide6.QtWidgets import QFileDialog, QMessageBox

from mchat.models.message import Message, Provider
from mchat.ui.html_exporter import exporter_from_config
from mchat.ui.services import ServicesContext

if TYPE_CHECKING:
    from mchat.ui.main_window import MainWindow  # noqa: F401


class ConversationHost(Protocol):
    """Presentational surface ConversationManager is allowed to touch.

    Documentation-level — Python doesn't enforce this at runtime, but
    every method the manager calls on its host is listed here so
    drift is easy to spot.
    """

    _chat: Any
    _sidebar: Any

    def _sync_checkboxes_from_selection(self) -> None: ...
    def _update_input_placeholder(self) -> None: ...
    def _update_input_color(self) -> None: ...
    def _update_spend_labels(self) -> None: ...
    def _sync_matrix_panel(self) -> None: ...
    def _display_messages(self, messages: list[Message]) -> None: ...


class ConversationManager:
    """All conversation-level operations the main window exposes."""

    def __init__(self, host: ConversationHost, services: ServicesContext) -> None:
        self._host = host
        self._services = services

    # ------------------------------------------------------------------
    # Listing & selection
    # ------------------------------------------------------------------

    def load_conversations(self) -> None:
        host = self._host
        conversations = self._services.db.list_conversations()
        host._sidebar.set_conversations(conversations)
        if conversations:
            host._sidebar.select_conversation(conversations[0].id)

    def on_conversation_selected(self, conv_id: int) -> None:
        host = self._host
        db = self._services.db
        conv = db.get_conversation(conv_id)
        if not conv:
            return
        messages = db.get_messages(conv_id)
        # Push the loaded conversation + messages through the session
        # in a single call so conversation_changed and messages_changed
        # fire in the right order.
        self._services.session.set_current(conv, messages=messages)

        # Restore selection from last_provider (comma-separated)
        if conv.last_provider and self._services.router:
            try:
                providers = [
                    Provider(v.strip())
                    for v in conv.last_provider.split(",") if v.strip()
                ]
                if providers:
                    self._services.router.set_selection(providers)
            except ValueError:
                pass
        host._sync_checkboxes_from_selection()
        host._update_input_placeholder()
        host._update_input_color()
        host._update_spend_labels()
        host._sync_matrix_panel()
        host._display_messages(messages)

    # ------------------------------------------------------------------
    # Creation / rename / delete / export
    # ------------------------------------------------------------------

    def new_chat(self) -> None:
        host = self._host
        system_prompt = self._services.config.get("system_prompt")
        conv = self._services.db.create_conversation(system_prompt=system_prompt)
        self._services.session.set_current(conv)
        host._chat.clear_messages()
        host._update_spend_labels()
        host._sync_matrix_panel()
        self.load_conversations()
        host._sidebar.select_conversation(conv.id)

    def on_rename(self, conv_id: int, new_title: str) -> None:
        host = self._host
        self._services.db.update_conversation_title(conv_id, new_title)
        current = self._services.session.current
        if current and current.id == conv_id:
            self._services.session.set_title(new_title)
        # Update the sidebar item in place — no reload, no re-render.
        host._sidebar.update_conversation_title(conv_id, new_title)

    def on_save(self, conv_id: int) -> None:
        host = self._host
        messages = self._services.db.get_messages(conv_id)
        if not messages:
            return
        convs = self._services.db.list_conversations()
        conv = next((c for c in convs if c.id == conv_id), None)
        title = (conv.title if conv else "chat").replace(" ", "_")[:40]

        # Pure non-Qt rendering — no temp widget, no private reach-through.
        html = exporter_from_config(self._services.config).export(messages)

        path, _ = QFileDialog.getSaveFileName(
            host, "Export Chat", f"{title}.html", "HTML Files (*.html)"
        )
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(html)

    def on_delete(self, conv_id: int) -> None:
        host = self._host
        reply = QMessageBox.question(
            host, "Delete Chat",
            "Delete this conversation? This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        current = self._services.session.current
        was_current = current is not None and current.id == conv_id
        self._services.db.delete_conversation(conv_id)
        if was_current:
            self._services.session.clear()
            host._chat.clear_messages()
        self.load_conversations()
        if was_current:
            self.new_chat()

    # ------------------------------------------------------------------
    # Per-conversation state persistence
    # ------------------------------------------------------------------

    def save_selection(self) -> None:
        """Persist the current router selection onto the conversation."""
        current = self._services.session.current
        router = self._services.router
        if current and router:
            sel_str = ",".join(p.value for p in router.selection)
            self._services.session.set_last_provider(sel_str)
            self._services.db.update_conversation_last_provider(
                current.id, sel_str
            )
