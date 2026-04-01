# ------------------------------------------------------------------
# Component: MainWindow
# Responsibility: Top-level window — wires sidebar, chat, input, providers
# Collaborators: PySide6, all ui components, router, db, config, workers
# ------------------------------------------------------------------
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QKeySequence, QShortcut, QTextBlockFormat, QTextCursor
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from mchat.config import Config, MAX_FONT_SIZE, MIN_FONT_SIZE
from mchat.db import Database
from mchat.models.conversation import Conversation
from mchat.models.message import Message, Provider, Role
from mchat.pricing import estimate_cost, format_cost
from mchat.providers.base import BaseProvider
from mchat.providers.claude import ClaudeProvider
from mchat.providers.openai_provider import OpenAIProvider
from mchat.router import Router
from mchat.ui.chat_widget import ChatWidget
from mchat.ui.input_widget import InputWidget
from mchat.ui.settings_dialog import SettingsDialog
from mchat.ui.sidebar import Sidebar
from mchat.router import BOTH
from mchat.workers.stream_worker import StreamWorker


class MainWindow(QMainWindow):
    def __init__(self, config: Config, db: Database) -> None:
        super().__init__()
        self._config = config
        self._db = db
        self._current_conv: Conversation | None = None
        self._stream_worker: StreamWorker | None = None
        self._both_workers: dict[Provider, StreamWorker] = {}
        self._both_results: dict[Provider, tuple[str, int, int]] = {}
        self._router: Router | None = None
        self._font_size = int(self._config.get("font_size") or 14)

        self._init_providers()
        self._build_ui()
        self._populate_model_combos()
        self._setup_shortcuts()
        self._load_conversations()
        self._update_input_placeholder()
        self._update_input_color()

    def _init_providers(self) -> None:
        providers: dict[Provider, BaseProvider] = {}

        anthropic_key = self._config.anthropic_api_key
        if anthropic_key:
            providers[Provider.CLAUDE] = ClaudeProvider(
                api_key=anthropic_key,
                default_model=self._config.get("claude_model"),
            )

        openai_key = self._config.openai_api_key
        if openai_key:
            providers[Provider.OPENAI] = OpenAIProvider(
                api_key=openai_key,
                default_model=self._config.get("openai_model"),
            )

        default = Provider(self._config.get("default_provider"))
        self._router = Router(providers, default) if providers else None

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self.setWindowTitle("mchat")
        self.setMinimumSize(900, 600)
        self.resize(1100, 750)

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Sidebar
        self._sidebar = Sidebar(font_size=self._font_size)
        self._sidebar.conversation_selected.connect(self._on_conversation_selected)
        self._sidebar.new_chat_requested.connect(self._on_new_chat)
        self._sidebar.save_requested.connect(self._on_save_conversation)
        self._sidebar.delete_requested.connect(self._on_delete_conversation)
        main_layout.addWidget(self._sidebar)

        # Right panel (chat + input)
        right = QFrame()
        right.setStyleSheet("background-color: #f5f5f5;")
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        # ---- Top bar ----
        top_bar = QFrame()
        top_bar.setStyleSheet("background-color: #f5f5f5; border-bottom: 1px solid #ddd;")
        top_bar_layout = QHBoxLayout(top_bar)
        top_bar_layout.setContentsMargins(16, 8, 16, 8)
        top_bar_layout.setSpacing(8)

        # Claude model combo + spend
        self._claude_combo = QComboBox()
        self._claude_combo.setMinimumWidth(180)
        self._claude_combo.activated.connect(lambda: self._claude_combo.hidePopup())
        top_bar_layout.addWidget(self._claude_combo)

        self._claude_spend_label = QLabel("$0.00000")
        self._apply_spend_label_style(self._claude_spend_label)
        top_bar_layout.addWidget(self._claude_spend_label)

        # Gap
        top_bar_layout.addSpacing(16)

        # OpenAI model combo + spend
        self._openai_combo = QComboBox()
        self._openai_combo.setMinimumWidth(180)
        self._openai_combo.activated.connect(lambda: self._openai_combo.hidePopup())
        top_bar_layout.addWidget(self._openai_combo)

        self._openai_spend_label = QLabel("$0.00000")
        self._apply_spend_label_style(self._openai_spend_label)
        top_bar_layout.addWidget(self._openai_spend_label)

        top_bar_layout.addStretch()

        # Settings button (right-aligned)
        self._settings_btn = QPushButton("⚙ Settings")
        self._apply_settings_btn_style()
        self._settings_btn.clicked.connect(self._open_settings)
        top_bar_layout.addWidget(self._settings_btn)

        right_layout.addWidget(top_bar)

        # Chat area
        self._chat = ChatWidget(
            font_size=self._font_size,
            color_user=self._config.get("color_user"),
            color_claude=self._config.get("color_claude"),
            color_openai=self._config.get("color_openai"),
        )
        right_layout.addWidget(self._chat, stretch=1)

        # Input area
        self._input = InputWidget(font_size=self._font_size)
        self._input.message_submitted.connect(self._on_message_submitted)
        right_layout.addWidget(self._input)

        main_layout.addWidget(right, stretch=1)

    def _populate_model_combos(self) -> None:
        """Fill model combo boxes from live providers."""
        providers = self._router._providers if self._router else {}
        for combo, provider_enum, config_key in [
            (self._claude_combo, Provider.CLAUDE, "claude_model"),
            (self._openai_combo, Provider.OPENAI, "openai_model"),
        ]:
            combo.blockSignals(True)
            combo.clear()
            provider = providers.get(provider_enum)
            models = provider.list_models() if provider else []
            current = self._config.get(config_key)
            if models:
                combo.addItems(models)
            if current and combo.findText(current) < 0:
                combo.insertItem(0, current)
            if not combo.count() and current:
                combo.addItem(current)
            idx = combo.findText(current)
            if idx >= 0:
                combo.setCurrentIndex(idx)
            combo.setEnabled(provider is not None)
            combo.blockSignals(False)

    def _apply_spend_label_style(self, label: QLabel) -> None:
        label.setStyleSheet(
            f"color: #666; font-size: {self._font_size - 1}px; padding: 0 4px;"
        )

    def _apply_settings_btn_style(self) -> None:
        self._settings_btn.setStyleSheet(
            f"QPushButton {{ background: none; border: 1px solid #ccc; border-radius: 6px; "
            f"padding: 4px 12px; color: #666; font-size: {self._font_size - 1}px; }}"
            f"QPushButton:hover {{ background-color: #eee; }}"
        )

    def _set_combo_waiting(self, provider_id: Provider, waiting: bool) -> None:
        """Highlight or unhighlight a provider combo while waiting for a response."""
        combo = self._claude_combo if provider_id == Provider.CLAUDE else self._openai_combo
        if waiting:
            combo.setStyleSheet(
                "QComboBox { border: 2px solid #e8a020; background-color: #fff8e0; "
                "font-weight: bold; }"
            )
        else:
            combo.setStyleSheet("")

    def _update_input_color(self) -> None:
        """Set the input box background to match the target provider colour."""
        if not self._router:
            return
        current = self._router.last_used
        if current == BOTH:
            color = self._config.get("color_user")
        elif current == Provider.CLAUDE:
            color = self._config.get("color_claude")
        else:
            color = self._config.get("color_openai")
        self._input.set_background(color)

    def _update_spend_labels(self) -> None:
        conv = self._current_conv
        claude_spend = conv.spend_claude if conv else 0.0
        openai_spend = conv.spend_openai if conv else 0.0
        self._claude_spend_label.setText(format_cost(claude_spend) if claude_spend else "$0.00000")
        self._openai_spend_label.setText(format_cost(openai_spend) if openai_spend else "$0.00000")

    # ------------------------------------------------------------------
    # Shortcuts
    # ------------------------------------------------------------------

    def _setup_shortcuts(self) -> None:
        zoom_in = QShortcut(QKeySequence("Ctrl+="), self)
        zoom_in.activated.connect(self._zoom_in)

        zoom_in2 = QShortcut(QKeySequence("Ctrl++"), self)
        zoom_in2.activated.connect(self._zoom_in)

        zoom_out = QShortcut(QKeySequence("Ctrl+-"), self)
        zoom_out.activated.connect(self._zoom_out)

        zoom_reset = QShortcut(QKeySequence("Ctrl+0"), self)
        zoom_reset.activated.connect(self._zoom_reset)

        save = QShortcut(QKeySequence("Ctrl+S"), self)
        save.activated.connect(self._export_chat)

    def _export_chat(self) -> None:
        if not self._current_conv or not self._current_conv.messages:
            return
        title = self._current_conv.title.replace(" ", "_")[:40]
        default_name = f"{title}.html"
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Chat", default_name, "HTML Files (*.html)"
        )
        if path:
            html = self._chat.export_html()
            with open(path, "w", encoding="utf-8") as f:
                f.write(html)

    def _zoom_in(self) -> None:
        self._set_font_size(self._font_size + 1)

    def _zoom_out(self) -> None:
        self._set_font_size(self._font_size - 1)

    def _zoom_reset(self) -> None:
        self._set_font_size(14)

    def _set_font_size(self, size: int) -> None:
        size = max(MIN_FONT_SIZE, min(MAX_FONT_SIZE, size))
        if size == self._font_size:
            return
        self._font_size = size
        self._config.set("font_size", size)
        self._config.save()
        self._apply_font_size()

    def _apply_font_size(self) -> None:
        self._chat.update_font_size(self._font_size)
        self._input.update_font_size(self._font_size)
        self._sidebar.update_font_size(self._font_size)
        self._apply_settings_btn_style()
        self._apply_spend_label_style(self._claude_spend_label)
        self._apply_spend_label_style(self._openai_spend_label)

    # ------------------------------------------------------------------
    # Conversations
    # ------------------------------------------------------------------

    def _load_conversations(self) -> None:
        conversations = self._db.list_conversations()
        self._sidebar.set_conversations(conversations)
        if conversations:
            self._sidebar.select_conversation(conversations[0].id)

    def _on_conversation_selected(self, conv_id: int) -> None:
        messages = self._db.get_messages(conv_id)
        convs = self._db.list_conversations()
        conv = next((c for c in convs if c.id == conv_id), None)
        if not conv:
            return
        self._current_conv = conv
        self._current_conv.messages = messages

        # Restore the provider that was last used in this conversation
        if conv.last_provider and self._router:
            if conv.last_provider == BOTH:
                self._router._last_used = BOTH
            else:
                try:
                    self._router._last_used = Provider(conv.last_provider)
                except ValueError:
                    pass
        self._update_input_placeholder()
        self._update_input_color()
        self._update_spend_labels()

        self._chat.clear_messages()
        for msg in messages:
            self._chat.add_message(msg)

    def _on_new_chat(self) -> None:
        system_prompt = self._config.get("system_prompt")
        conv = self._db.create_conversation(system_prompt=system_prompt)
        self._current_conv = conv
        self._chat.clear_messages()
        self._update_spend_labels()
        self._load_conversations()
        self._sidebar.select_conversation(conv.id)

    def _on_save_conversation(self, conv_id: int) -> None:
        """Export a conversation to HTML (may differ from the currently viewed one)."""
        messages = self._db.get_messages(conv_id)
        if not messages:
            return
        convs = self._db.list_conversations()
        conv = next((c for c in convs if c.id == conv_id), None)
        title = (conv.title if conv else "chat").replace(" ", "_")[:40]

        # Build a temporary ChatWidget to render the messages as HTML
        from mchat.ui.chat_widget import ChatWidget
        tmp = ChatWidget(font_size=self._font_size)
        for msg in messages:
            tmp._messages.append(msg)
            tmp._insert_rendered(msg)
        html = tmp.export_html()
        tmp.deleteLater()

        path, _ = QFileDialog.getSaveFileName(
            self, "Export Chat", f"{title}.html", "HTML Files (*.html)"
        )
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(html)

    def _on_delete_conversation(self, conv_id: int) -> None:
        """Delete a conversation after confirmation."""
        reply = QMessageBox.question(
            self, "Delete Chat",
            "Delete this conversation? This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        was_current = self._current_conv and self._current_conv.id == conv_id
        self._db.delete_conversation(conv_id)
        if was_current:
            self._current_conv = None
            self._chat.clear_messages()
        self._load_conversations()
        if was_current:
            self._on_new_chat()

    # ------------------------------------------------------------------
    # Messaging
    # ------------------------------------------------------------------

    def _update_input_placeholder(self) -> None:
        """Set the input placeholder to reflect the current default provider."""
        if not self._router:
            self._input.set_placeholder("Configure an API key in Settings to start chatting")
            return
        current = self._router.last_used
        if current == BOTH:
            self._input.set_placeholder("Message both — prefix claude, or gpt, for one")
        elif current == Provider.CLAUDE:
            self._input.set_placeholder("Message Claude — prefix gpt, or both, to switch")
        else:
            self._input.set_placeholder("Message GPT — prefix claude, or both, to switch")

    def _selected_model(self, provider_id: Provider) -> str:
        """Return the model currently selected in the top-bar combo."""
        if provider_id == Provider.CLAUDE:
            return self._claude_combo.currentText()
        return self._openai_combo.currentText()

    def _build_context(self) -> list[Message]:
        """Build the context message list including system prompt.

        If a //limit is active, only messages from the mark position
        onwards are included (system prompt always goes).
        """
        context: list[Message] = []
        if self._current_conv.system_prompt:
            context.append(
                Message(role=Role.SYSTEM, content=self._current_conv.system_prompt)
            )
        messages = self._current_conv.messages
        limit_mark = self._current_conv.limit_mark
        if limit_mark is not None:
            idx = self._db.get_mark(self._current_conv.id, limit_mark)
            if idx is not None and idx < len(messages):
                messages = messages[idx:]
        context.extend(messages)
        return context

    # ------------------------------------------------------------------
    # // commands
    # ------------------------------------------------------------------

    _HELP_TEXT = (
        "Available commands:\n"
        "  //mark [tagname]      — mark this point in the chat (overwrites previous mark of same name)\n"
        "  //limit [tagname]     — only send chat from that mark onwards to providers\n"
        "  //limit ALL           — remove the limit, send full chat history again\n"
        "  //incremental         — render markdown progressively while streaming\n"
        "  //batch               — render markdown only when response is complete (default)\n"
        "  //help                — show this help\n"
        "\n"
        "Provider prefixes:\n"
        "  claude, <message>     — send to Claude\n"
        "  gpt, <message>        — send to GPT\n"
        "  both, <message>       — send to both simultaneously\n"
        "  (no prefix)           — send to last-used provider"
    )

    def _handle_command(self, text: str) -> bool:
        """Handle // commands. Returns True if the input was a command."""
        stripped = text.strip()
        if not stripped.startswith("//"):
            return False

        parts = stripped.split(None, 1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd == "//help":
            self._chat.add_note("Help")
            # Display help as a note-styled message (not sent to providers)
            cursor = self._chat.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            fmt = QTextBlockFormat()
            fmt.setBackground(QColor("#f5f5f5"))
            for line in self._HELP_TEXT.split("\n"):
                cursor.insertBlock(fmt)
                char_fmt = cursor.charFormat()
                char_fmt.setForeground(QColor("#666"))
                cursor.insertText(line, char_fmt)
            self._chat._scroll_to_bottom()
            return True

        if cmd == "//mark":
            return self._handle_mark(arg)

        if cmd == "//limit":
            return self._handle_limit(arg)

        if cmd == "//incremental":
            self._chat._incremental = True
            self._chat.add_note("incremental rendering enabled")
            return True

        if cmd == "//batch":
            self._chat._incremental = False
            self._chat.add_note("batch rendering enabled (default)")
            return True

        return False

    def _handle_mark(self, tag: str) -> bool:
        if not self._current_conv:
            self._on_new_chat()

        if tag.upper() == "ALL":
            self._chat.add_note("Error: 'ALL' is not allowed as a mark name")
            return True

        name = tag  # empty string = unnamed/general mark
        count = len(self._current_conv.messages)
        self._db.set_mark(self._current_conv.id, name, count)

        label = f"mark '{tag}'" if tag else "mark (unnamed)"
        self._chat.add_note(f"{label} set at message {count}")
        return True

    def _handle_limit(self, tag: str) -> bool:
        if not self._current_conv:
            self._on_new_chat()

        if tag.upper() == "ALL":
            self._current_conv.limit_mark = None
            self._db.set_conversation_limit(self._current_conv.id, None)
            self._chat.add_note("limit removed — full chat history will be sent")
            return True

        name = tag  # empty string = unnamed/general mark
        idx = self._db.get_mark(self._current_conv.id, name)
        if idx is None:
            label = f"mark '{tag}'" if tag else "unnamed mark"
            self._chat.add_note(f"Error: {label} not found")
            return True

        self._current_conv.limit_mark = name
        self._db.set_conversation_limit(self._current_conv.id, name)

        label = f"mark '{tag}'" if tag else "unnamed mark"
        self._chat.add_note(f"limit set to {label} (message {idx}) — earlier context will not be sent")
        return True

    # ------------------------------------------------------------------

    def _on_message_submitted(self, text: str) -> None:
        # Handle // commands before provider routing
        if text.strip().startswith("//"):
            self._handle_command(text)
            return

        if not self._router:
            QMessageBox.warning(
                self, "No API Keys",
                "Please configure at least one API key in Settings.",
            )
            return

        # Create conversation if none active
        if not self._current_conv:
            self._on_new_chat()

        # Route message
        target, cleaned_text = self._router.parse(text)

        if target == BOTH:
            # Need both providers configured
            missing = [
                p for p in (Provider.CLAUDE, Provider.OPENAI)
                if p not in self._router._providers
            ]
            if missing:
                names = ", ".join(p.value for p in missing)
                QMessageBox.warning(
                    self, "Provider Not Configured",
                    f"No API key configured for {names}. Check Settings.",
                )
                return
        else:
            if target not in self._router._providers:
                QMessageBox.warning(
                    self, "Provider Not Configured",
                    f"No API key configured for {target.value}. Check Settings.",
                )
                return

        # Save and display user message
        user_msg = Message(
            role=Role.USER,
            content=text,
            conversation_id=self._current_conv.id,
        )
        self._db.add_message(user_msg)
        self._current_conv.messages.append(user_msg)
        self._chat.add_message(user_msg)

        # Auto-title on first message
        if len(self._current_conv.messages) == 1:
            title = text[:50] + ("..." if len(text) > 50 else "")
            self._db.update_conversation_title(self._current_conv.id, title)
            self._load_conversations()
            self._sidebar.select_conversation(self._current_conv.id)

        self._input.set_enabled(False)

        if target == BOTH:
            self._send_both()
        else:
            self._send_single(target)

    def _send_single(self, provider_id: Provider) -> None:
        """Send to a single provider with live streaming."""
        model = self._selected_model(provider_id)
        provider = self._router.get_provider(provider_id)

        self._set_combo_waiting(provider_id, True)

        assistant_msg = Message(
            role=Role.ASSISTANT,
            content="",
            provider=provider_id,
            model=model,
            conversation_id=self._current_conv.id,
        )
        self._chat.begin_streaming(assistant_msg)

        context_messages = self._build_context()
        self._stream_worker = StreamWorker(provider, context_messages, model)
        self._stream_worker.token_received.connect(self._chat.append_token)
        self._stream_worker.stream_complete.connect(
            lambda full_text, inp, out: self._on_stream_complete(
                full_text, provider_id, model, inp, out
            )
        )
        self._stream_worker.stream_error.connect(self._on_stream_error)
        self._stream_worker.start()

    def _send_both(self) -> None:
        """Send to both providers simultaneously, render when each completes."""
        self._both_results.clear()
        self._both_workers.clear()
        self._set_combo_waiting(Provider.CLAUDE, True)
        self._set_combo_waiting(Provider.OPENAI, True)
        context_messages = self._build_context()

        for provider_id in (Provider.CLAUDE, Provider.OPENAI):
            model = self._selected_model(provider_id)
            provider = self._router.get_provider(provider_id)

            worker = StreamWorker(provider, context_messages, model)
            # No token_received connection — collect silently
            worker.stream_complete.connect(
                lambda full_text, inp, out, pid=provider_id, mdl=model: (
                    self._on_both_single_complete(pid, mdl, full_text, inp, out)
                )
            )
            worker.stream_error.connect(
                lambda error, pid=provider_id: self._on_both_single_error(pid, error)
            )
            self._both_workers[provider_id] = worker
            worker.start()

    # ------------------------------------------------------------------
    # "both" mode completion
    # ------------------------------------------------------------------

    def _on_both_single_complete(
        self,
        provider_id: Provider,
        model: str,
        full_text: str,
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        self._set_combo_waiting(provider_id, False)
        self._both_results[provider_id] = (full_text, input_tokens, output_tokens)
        self._both_workers.pop(provider_id, None)

        # Render this response immediately as a complete message
        label = "Claude's take" if provider_id == Provider.CLAUDE else "GPT's take"
        prefixed = f"**{label}:**\n\n{full_text}"
        msg = Message(
            role=Role.ASSISTANT,
            content=prefixed,
            provider=provider_id,
            model=model,
            conversation_id=self._current_conv.id,
        )
        self._db.add_message(msg)
        self._current_conv.messages.append(msg)
        self._chat.add_message(msg)

        # Update per-conversation spend
        cost = estimate_cost(model, input_tokens, output_tokens)
        if cost is not None and self._current_conv:
            if provider_id == Provider.CLAUDE:
                self._current_conv.spend_claude += cost
            else:
                self._current_conv.spend_openai += cost
            self._db.add_conversation_spend(
                self._current_conv.id, provider_id.value, cost
            )
        self._update_spend_labels()

        # If both are done, re-enable input and save "both" as last provider
        if not self._both_workers:
            if self._current_conv:
                self._current_conv.last_provider = BOTH
                self._db.update_conversation_last_provider(
                    self._current_conv.id, BOTH
                )
            self._input.set_enabled(True)
            self._update_input_placeholder()
            self._update_input_color()

    def _on_both_single_error(self, provider_id: Provider, error: str) -> None:
        self._set_combo_waiting(provider_id, False)
        self._both_workers.pop(provider_id, None)

        # Show error as a message in chat
        error_msg = Message(
            role=Role.ASSISTANT,
            content=f"[Error from {provider_id.value}: {error}]",
            provider=provider_id,
            conversation_id=self._current_conv.id,
        )
        self._chat.add_message(error_msg)

        if not self._both_workers:
            self._input.set_enabled(True)
            self._update_input_placeholder()
            self._update_input_color()

    # ------------------------------------------------------------------
    # Single-provider completion
    # ------------------------------------------------------------------

    def _on_stream_complete(
        self,
        full_text: str,
        provider_id: Provider,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        self._set_combo_waiting(provider_id, False)
        msg = self._chat.end_streaming()
        if msg:
            msg.content = full_text
            self._db.add_message(msg)
            self._current_conv.messages.append(msg)

        # Update per-conversation spend
        cost = estimate_cost(model, input_tokens, output_tokens)
        if cost is not None and self._current_conv:
            if provider_id == Provider.CLAUDE:
                self._current_conv.spend_claude += cost
            else:
                self._current_conv.spend_openai += cost
            self._db.add_conversation_spend(
                self._current_conv.id, provider_id.value, cost
            )
        self._update_spend_labels()

        # Remember which provider was last used in this conversation
        if self._current_conv:
            self._current_conv.last_provider = provider_id.value
            self._db.update_conversation_last_provider(
                self._current_conv.id, provider_id.value
            )

        self._input.set_enabled(True)
        self._update_input_placeholder()
        self._update_input_color()
        self._stream_worker = None

    def _on_stream_error(self, error: str) -> None:
        self._set_combo_waiting(Provider.CLAUDE, False)
        self._set_combo_waiting(Provider.OPENAI, False)
        self._chat.end_streaming()
        self._input.set_enabled(True)
        self._stream_worker = None
        QMessageBox.critical(self, "Error", f"Streaming failed:\n{error}")

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    def _open_settings(self) -> None:
        providers = self._router._providers if self._router else {}
        dialog = SettingsDialog(self._config, providers=providers, parent=self)
        if dialog.exec():
            self._init_providers()
            self._populate_model_combos()
            self._update_input_placeholder()
            self._update_input_color()
            new_size = int(self._config.get("font_size") or 14)
            if new_size != self._font_size:
                self._font_size = new_size
                self._apply_font_size()
            self._chat.update_colors(
                self._config.get("color_user"),
                self._config.get("color_claude"),
                self._config.get("color_openai"),
            )
