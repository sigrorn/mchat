# ------------------------------------------------------------------
# Component: ConversationManager
# Responsibility: Own the conversation lifecycle outside MainWindow —
#                 listing, selecting, creating, renaming, exporting
#                 and deleting conversations. Coordinates the Sidebar,
#                 the current conversation on the host, the chat
#                 rendering pipeline, and the DB.
# Collaborators: MainWindow (host), db, sidebar, chat_widget,
#                message_renderer, models.conversation
# ------------------------------------------------------------------
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QFileDialog, QMessageBox

from mchat.models.conversation import Conversation
from mchat.models.message import Provider
from mchat.ui.chat_widget import ChatWidget

if TYPE_CHECKING:
    from mchat.ui.main_window import MainWindow


class ConversationManager:
    """All conversation-level operations the main window exposes.

    Holds a reference to the host so it can reach ``_db``, ``_sidebar``,
    ``_chat``, ``_router``, ``_current_conv``, and the various
    refresh/sync methods that need to fire after conversation changes.
    """

    def __init__(self, host: "MainWindow") -> None:
        self._host = host

    # ------------------------------------------------------------------
    # Listing & selection
    # ------------------------------------------------------------------

    def load_conversations(self) -> None:
        host = self._host
        conversations = host._db.list_conversations()
        host._sidebar.set_conversations(conversations)
        if conversations:
            host._sidebar.select_conversation(conversations[0].id)

    def on_conversation_selected(self, conv_id: int) -> None:
        host = self._host
        conv = host._db.get_conversation(conv_id)
        if not conv:
            return
        messages = host._db.get_messages(conv_id)
        host._current_conv = conv
        host._current_conv.messages = messages

        # Restore selection from last_provider (comma-separated)
        if conv.last_provider and host._router:
            try:
                providers = [
                    Provider(v.strip())
                    for v in conv.last_provider.split(",") if v.strip()
                ]
                if providers:
                    host._router.set_selection(providers)
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
        system_prompt = host._config.get("system_prompt")
        conv = host._db.create_conversation(system_prompt=system_prompt)
        host._current_conv = conv
        host._chat.clear_messages()
        host._update_spend_labels()
        host._sync_matrix_panel()
        self.load_conversations()
        host._sidebar.select_conversation(conv.id)

    def on_rename(self, conv_id: int, new_title: str) -> None:
        host = self._host
        host._db.update_conversation_title(conv_id, new_title)
        if host._current_conv and host._current_conv.id == conv_id:
            host._current_conv.title = new_title
        # Update the sidebar item in place — no reload, no re-render.
        host._sidebar.update_conversation_title(conv_id, new_title)

    def on_save(self, conv_id: int) -> None:
        host = self._host
        messages = host._db.get_messages(conv_id)
        if not messages:
            return
        convs = host._db.list_conversations()
        conv = next((c for c in convs if c.id == conv_id), None)
        title = (conv.title if conv else "chat").replace(" ", "_")[:40]

        tmp = ChatWidget(font_size=host._font_size)
        for msg in messages:
            tmp._messages.append(msg)
            tmp._insert_rendered(msg)
        html = tmp.export_html()
        tmp.deleteLater()

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
        was_current = host._current_conv and host._current_conv.id == conv_id
        host._db.delete_conversation(conv_id)
        if was_current:
            host._current_conv = None
            host._chat.clear_messages()
        self.load_conversations()
        if was_current:
            self.new_chat()

    # ------------------------------------------------------------------
    # Per-conversation state persistence
    # ------------------------------------------------------------------

    def save_selection(self) -> None:
        """Persist the current router selection onto the conversation."""
        host = self._host
        if host._current_conv and host._router:
            sel_str = ",".join(p.value for p in host._router.selection)
            host._current_conv.last_provider = sel_str
            host._db.update_conversation_last_provider(
                host._current_conv.id, sel_str
            )
