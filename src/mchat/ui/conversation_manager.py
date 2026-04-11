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
        # #128: in-memory per-conversation input drafts. Saved on
        # switch-out, restored on switch-in. Not persisted — drafts
        # live only for the current app session.
        self._input_drafts: dict[int, str] = {}

    # ------------------------------------------------------------------
    # Input draft helpers (#128)
    # ------------------------------------------------------------------

    def _save_current_draft(self) -> None:
        """Save the current input text as the draft for the currently
        active conversation, if any."""
        current = self._services.session.current
        if current is None:
            return
        host = self._host
        # Read raw (not stripped) so whitespace-only drafts also persist
        text = host._input._text_edit.toPlainText()
        if text:
            self._input_drafts[current.id] = text
        else:
            # Empty — drop any stale entry so a cleared input doesn't
            # resurrect on re-switch
            self._input_drafts.pop(current.id, None)

    def _restore_draft(self, conv_id: int) -> None:
        """Restore the draft for a conversation, clearing the input
        if none was saved."""
        host = self._host
        text = self._input_drafts.get(conv_id, "")
        host._input._text_edit.setPlainText(text)

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
        # #128: save the outgoing conversation's input draft BEFORE
        # the session swap, so we capture the right conv_id.
        current = self._services.session.current
        if current is not None and current.id != conv_id:
            self._save_current_draft()
        messages = db.get_messages(conv_id)
        # Push the loaded conversation + messages through the session
        # in a single call so conversation_changed and messages_changed
        # fire in the right order.
        self._services.session.set_current(conv, messages=messages)

        # Restore selection from last_provider (comma-separated persona_ids).
        # Builds PersonaTargets from the DB for explicit personas, falls back
        # to synthetic defaults for provider-value strings (legacy compat).
        # #121b: Always set the selection — an empty list is valid and
        # prevents stale targets from the previous conversation leaking.
        selection_changed = False
        if self._services.selection:
            if conv.last_provider:
                from mchat.ui.persona_target import PersonaTarget, synthetic_default
                tokens = [v.strip() for v in conv.last_provider.split(",") if v.strip()]
                personas = db.list_personas(conv_id)
                persona_map = {p.id: p for p in personas}
                targets: list[PersonaTarget] = []
                for token in tokens:
                    if token in persona_map:
                        p = persona_map[token]
                        targets.append(PersonaTarget(persona_id=p.id, provider=p.provider))
                    else:
                        # Legacy: token is a provider.value string
                        try:
                            prov = Provider(token)
                            targets.append(synthetic_default(prov))
                        except ValueError:
                            pass
                self._services.selection.set(targets)
                selection_changed = bool(targets)
            else:
                # No saved selection — clear to prevent stale leak
                self._services.selection.set([])
        if not selection_changed:
            # No selection change fired the fan-out; do it manually so
            # the new conversation still gets its placeholder/colour
            # updated (the colour may depend on the conversation's
            # title, context, etc.).
            host._sync_checkboxes_from_selection()
            host._update_input_placeholder()
            host._update_input_color()
        # #141: _sync_toolbar_personas must run BEFORE _update_spend_labels.
        # The spend labels are keyed off the persona rows that
        # _sync_toolbar_personas (re)builds; calling _update_spend_labels
        # first writes "$0.00000" into whatever rows the previous
        # conversation left behind, and the correct values never land.
        host._sync_toolbar_personas()
        host._update_spend_labels()
        host._sync_matrix_panel()
        # #124: restore per-conversation send mode (parallel/sequential)
        host._send._sequential_mode = (conv.send_mode == "sequential")
        # #128: restore the incoming conversation's input draft (or clear)
        self._restore_draft(conv_id)
        host._display_messages(messages)

    # ------------------------------------------------------------------
    # Creation / rename / delete / export
    # ------------------------------------------------------------------

    def new_chat(self) -> None:
        host = self._host
        # #128: save any draft from the outgoing conversation before
        # swapping — and clear the input so the new chat starts empty.
        self._save_current_draft()
        host._input._text_edit.setPlainText("")
        system_prompt = self._services.config.get("system_prompt")
        conv = self._services.db.create_conversation(system_prompt=system_prompt)
        self._services.session.set_current(conv)
        host._chat.clear_messages()
        # #141: personas first, spend labels second (see on_conversation_selected).
        host._sync_toolbar_personas()
        host._update_spend_labels()
        host._sync_matrix_panel()
        # #124: new chats always start in parallel mode (the DB default).
        # The DB column is already 'parallel' from the migration, but we
        # also reset the in-memory flag so it doesn't carry over.
        host._send._sequential_mode = False
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
        # Pass tombstoned personas too so historical messages keep their labels.
        personas = self._services.db.list_personas_including_deleted(conv_id)
        html = exporter_from_config(self._services.config).export(
            messages, personas=personas,
        )

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
        # #128: drop any cached input draft for the deleted conversation
        self._input_drafts.pop(conv_id, None)
        # Remove from sidebar immediately
        host._sidebar.remove_conversation(conv_id)
        if was_current:
            self._services.session.clear()
            host._chat.clear_messages()
            host._sync_toolbar_personas()
            host._sync_matrix_panel()
            # Select another conversation if any remain, else start fresh
            remaining = self._services.db.list_conversations()
            if remaining:
                host._sidebar.select_conversation(remaining[0].id)
            else:
                self.new_chat()

    # ------------------------------------------------------------------
    # Per-conversation state persistence
    # ------------------------------------------------------------------

    def save_selection(self) -> None:
        """Persist the current selection (persona_ids) onto the conversation.

        #121: Filter out synthetic defaults (persona_id == provider.value)
        when an explicit persona for the same provider exists in the
        selection, so they don't get restored as phantom targets.
        """
        current = self._services.session.current
        selection = self._services.selection
        if current and selection:
            targets = list(selection.selection)
            # Collect providers that have an explicit (non-synthetic) persona
            explicit_providers = {
                t.provider.value for t in targets
                if t.persona_id != t.provider.value
            }
            # Drop synthetic defaults for providers that have explicits
            filtered = [
                t for t in targets
                if not (t.persona_id == t.provider.value
                        and t.persona_id in explicit_providers)
            ]
            sel_str = ",".join(t.persona_id for t in filtered)
            self._services.session.set_last_provider(sel_str)
            self._services.db.update_conversation_last_provider(
                current.id, sel_str
            )
