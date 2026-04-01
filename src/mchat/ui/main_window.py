# ------------------------------------------------------------------
# Component: MainWindow
# Responsibility: Top-level window — wires sidebar, chat, input, providers
# Collaborators: PySide6, all ui components, router, db, config, workers
# ------------------------------------------------------------------
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QKeySequence, QShortcut, QTextBlockFormat, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
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

from mchat.config import Config, MAX_FONT_SIZE, MIN_FONT_SIZE, PROVIDER_META
from mchat.db import Database
from mchat.models.conversation import Conversation
from mchat.models.message import Message, Provider, Role
from mchat.pricing import estimate_cost, format_cost
from mchat.providers.base import BaseProvider
from mchat.providers.claude import ClaudeProvider
from mchat.providers.gemini_provider import GeminiProvider
from mchat.providers.openai_provider import OpenAIProvider
from mchat.providers.perplexity_provider import PerplexityProvider
from mchat.router import Router
from mchat.ui.chat_widget import ChatWidget
from mchat.ui.input_widget import InputWidget
from mchat.ui.settings_dialog import SettingsDialog
from mchat.ui.sidebar import Sidebar
from mchat.workers.stream_worker import StreamWorker

# Display names for provider labels in "X's take:" prefixes
_PROVIDER_DISPLAY = {p: PROVIDER_META[p.value]["display"] for p in Provider}


class MainWindow(QMainWindow):
    def __init__(self, config: Config, db: Database) -> None:
        super().__init__()
        self._config = config
        self._db = db
        self._current_conv: Conversation | None = None
        self._stream_worker: StreamWorker | None = None
        self._multi_workers: dict[Provider, StreamWorker] = {}
        self._router: Router | None = None
        self._font_size = int(self._config.get("font_size") or 14)

        # Per-provider UI widgets (built dynamically)
        self._checkboxes: dict[Provider, QCheckBox] = {}
        self._combos: dict[Provider, QComboBox] = {}
        self._spend_labels: dict[Provider, QLabel] = {}

        self._init_providers()
        self._build_ui()
        self._populate_model_combos()
        self._apply_all_combo_styles()
        self._sync_checkboxes_from_selection()
        self._setup_shortcuts()
        self._load_conversations()
        self._update_input_placeholder()
        self._update_input_color()

    def _init_providers(self) -> None:
        providers: dict[Provider, BaseProvider] = {}

        anthropic_key = self._config.get("anthropic_api_key")
        if anthropic_key:
            providers[Provider.CLAUDE] = ClaudeProvider(
                api_key=anthropic_key,
                default_model=self._config.get("claude_model"),
            )

        openai_key = self._config.get("openai_api_key")
        if openai_key:
            providers[Provider.OPENAI] = OpenAIProvider(
                api_key=openai_key,
                default_model=self._config.get("openai_model"),
            )

        gemini_key = self._config.get("gemini_api_key")
        if gemini_key:
            providers[Provider.GEMINI] = GeminiProvider(
                api_key=gemini_key,
                default_model=self._config.get("gemini_model"),
            )

        perplexity_key = self._config.get("perplexity_api_key")
        if perplexity_key:
            providers[Provider.PERPLEXITY] = PerplexityProvider(
                api_key=perplexity_key,
                default_model=self._config.get("perplexity_model"),
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
        self._sidebar.rename_requested.connect(self._on_rename_conversation)
        self._sidebar.save_requested.connect(self._on_save_conversation)
        self._sidebar.delete_requested.connect(self._on_delete_conversation)
        main_layout.addWidget(self._sidebar)

        # Right panel (chat + bar + input)
        right = QFrame()
        right.setStyleSheet("background-color: #f5f5f5;")
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        # Chat area
        self._chat = ChatWidget(
            font_size=self._font_size,
            color_user=self._config.get("color_user"),
            color_claude=self._config.get("color_claude"),
            color_openai=self._config.get("color_openai"),
            color_gemini=self._config.get("color_gemini"),
            color_perplexity=self._config.get("color_perplexity"),
        )
        right_layout.addWidget(self._chat, stretch=1)

        # ---- Provider bar (between chat and input) ----
        bar = QFrame()
        bar.setStyleSheet("background-color: #f5f5f5; border-top: 1px solid #ddd;")
        self._bar_layout = QHBoxLayout(bar)
        self._bar_layout.setContentsMargins(16, 8, 16, 8)
        self._bar_layout.setSpacing(8)

        # Build checkbox + combo + spend label for each provider
        providers_list = list(Provider)
        for i, p in enumerate(providers_list):
            if i > 0:
                self._bar_layout.addSpacing(12)

            cb = QCheckBox()
            cb.setToolTip(f"Include {_PROVIDER_DISPLAY[p]} in selection")
            cb.stateChanged.connect(lambda _, pid=p: self._on_checkbox_changed(pid))
            self._bar_layout.addWidget(cb)
            self._checkboxes[p] = cb

            combo = QComboBox()
            combo.setMinimumWidth(160)
            combo.activated.connect(lambda _, c=combo: c.hidePopup())
            self._bar_layout.addWidget(combo)
            self._combos[p] = combo

            label = QLabel("$0.00000")
            self._apply_spend_label_style(label)
            self._bar_layout.addWidget(label)
            self._spend_labels[p] = label

        self._bar_layout.addStretch()

        # Settings button (right-aligned)
        self._settings_btn = QPushButton("⚙ Settings")
        self._apply_settings_btn_style()
        self._settings_btn.clicked.connect(self._open_settings)
        self._bar_layout.addWidget(self._settings_btn)

        right_layout.addWidget(bar)

        # Input area
        self._input = InputWidget(font_size=self._font_size)
        self._input.message_submitted.connect(self._on_message_submitted)
        right_layout.addWidget(self._input)

        main_layout.addWidget(right, stretch=1)

    def _populate_model_combos(self) -> None:
        """Fill model combo boxes from live providers."""
        providers = self._router._providers if self._router else {}
        for p in Provider:
            combo = self._combos[p]
            meta = PROVIDER_META[p.value]
            combo.blockSignals(True)
            combo.clear()
            provider = providers.get(p)
            models = provider.list_models() if provider else []
            current = self._config.get(meta["model_key"])
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

            # Enable/disable checkbox based on provider availability
            cb = self._checkboxes[p]
            cb.setEnabled(provider is not None)

    def _sync_checkboxes_from_selection(self) -> None:
        """Update checkboxes to reflect the router's current selection."""
        if not self._router:
            return
        sel = set(self._router.selection)
        for p, cb in self._checkboxes.items():
            cb.blockSignals(True)
            cb.setChecked(p in sel)
            cb.blockSignals(False)

    def _on_checkbox_changed(self, provider_id: Provider) -> None:
        """Handle a checkbox toggle — update the router selection."""
        if not self._router:
            return
        selected = [p for p, cb in self._checkboxes.items() if cb.isChecked()]
        if not selected:
            # Don't allow empty selection — revert
            self._sync_checkboxes_from_selection()
            self._chat.add_note("Error: at least one provider must be selected")
            return
        self._router.set_selection(selected)
        self._save_selection()
        self._update_input_placeholder()
        self._update_input_color()

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

    def _provider_color(self, p: Provider) -> str:
        return self._config.get(PROVIDER_META[p.value]["color_key"])

    def _apply_combo_provider_style(self, p: Provider) -> None:
        color = self._provider_color(p)
        combo = self._combos[p]
        if combo.isEnabled():
            combo.setStyleSheet(f"QComboBox {{ background-color: {color}; }}")
        else:
            combo.setStyleSheet(
                "QComboBox { background-color: #e0e0e0; color: #999; }"
            )

    def _apply_all_combo_styles(self) -> None:
        for p in Provider:
            self._apply_combo_provider_style(p)

    def _set_combo_waiting(self, p: Provider, waiting: bool) -> None:
        combo = self._combos[p]
        if waiting:
            combo.setStyleSheet(
                "QComboBox { border: 2px solid #e8a020; background-color: #fff8e0; "
                "font-weight: bold; }"
            )
        else:
            self._apply_combo_provider_style(p)

    def _update_input_color(self) -> None:
        if not self._router:
            return
        sel = self._router.selection
        if len(sel) == 1:
            color = self._provider_color(sel[0])
        else:
            color = self._config.get("color_user")
        self._input.set_background(color)

    def _update_spend_labels(self) -> None:
        if self._current_conv:
            spend = self._db.get_conversation_spend(self._current_conv.id)
        else:
            spend = {}
        for p in Provider:
            label = self._spend_labels[p]
            entry = spend.get(p.value)
            if entry:
                amount, estimated = entry
                text = format_cost(amount) if amount else "$0.00000"
                if estimated:
                    label.setText(f"<i>{text}</i>")
                else:
                    label.setText(text)
            else:
                label.setText("$0.00000")

    # ------------------------------------------------------------------
    # Shortcuts
    # ------------------------------------------------------------------

    def _setup_shortcuts(self) -> None:
        QShortcut(QKeySequence("Ctrl+="), self).activated.connect(self._zoom_in)
        QShortcut(QKeySequence("Ctrl++"), self).activated.connect(self._zoom_in)
        QShortcut(QKeySequence("Ctrl+-"), self).activated.connect(self._zoom_out)
        QShortcut(QKeySequence("Ctrl+0"), self).activated.connect(self._zoom_reset)
        QShortcut(QKeySequence("Ctrl+S"), self).activated.connect(self._export_chat)

    def _export_chat(self) -> None:
        if not self._current_conv or not self._current_conv.messages:
            return
        title = self._current_conv.title.replace(" ", "_")[:40]
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Chat", f"{title}.html", "HTML Files (*.html)"
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
        for label in self._spend_labels.values():
            self._apply_spend_label_style(label)

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

        # Restore selection from last_provider (comma-separated)
        if conv.last_provider and self._router:
            try:
                providers = [Provider(v.strip()) for v in conv.last_provider.split(",") if v.strip()]
                if providers:
                    self._router.set_selection(providers)
            except ValueError:
                pass
        self._sync_checkboxes_from_selection()
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

    def _on_rename_conversation(self, conv_id: int, new_title: str) -> None:
        self._db.update_conversation_title(conv_id, new_title)
        if self._current_conv and self._current_conv.id == conv_id:
            self._current_conv.title = new_title
        self._load_conversations()

    def _on_save_conversation(self, conv_id: int) -> None:
        messages = self._db.get_messages(conv_id)
        if not messages:
            return
        convs = self._db.list_conversations()
        conv = next((c for c in convs if c.id == conv_id), None)
        title = (conv.title if conv else "chat").replace(" ", "_")[:40]

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
        if not self._router:
            self._input.set_placeholder("Configure an API key in Settings to start chatting")
            return
        sel = self._router.selection
        if len(sel) == 1:
            name = _PROVIDER_DISPLAY[sel[0]]
            others = [_PROVIDER_DISPLAY[p] for p in Provider if p != sel[0]]
            alt = ", ".join(others[:2])
            self._input.set_placeholder(f"Message {name} — prefix another provider or use //select")
        else:
            names = ", ".join(_PROVIDER_DISPLAY[p] for p in sel)
            self._input.set_placeholder(f"Message {names} — use //select to change")

    def _selected_model(self, p: Provider) -> str:
        return self._combos[p].currentText()

    def _build_context(self, provider_id: Provider) -> list[Message]:
        context: list[Message] = []

        # Provider-specific system prompt + main system prompt
        parts: list[str] = []
        provider_prompt = self._config.get(
            PROVIDER_META[provider_id.value]["system_prompt_key"]
        )
        if provider_prompt:
            parts.append(provider_prompt)
        if self._current_conv.system_prompt:
            parts.append(self._current_conv.system_prompt)
        if parts:
            context.append(
                Message(role=Role.SYSTEM, content="\n\n".join(parts))
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
        "  //mark [tagname]      — mark this point in the chat\n"
        "  //marklast [tagname]  — mark just before the last request\n"
        "  //limit [tagname]     — only send chat from that mark onwards\n"
        "  //limit ALL           — remove the limit, send full chat history\n"
        "  //marks               — list all marks (click to scroll)\n"
        "  //select <providers>  — set target providers (e.g. //select gpt, claude)\n"
        "  //select all          — target all configured providers\n"
        "  //providers           — list available providers and config status\n"
        "  //incremental         — render markdown progressively while streaming\n"
        "  //batch               — render on completion (default)\n"
        "  //help                — show this help\n"
        "\n"
        "Provider prefixes:\n"
        "  claude, <message>     — send to Claude\n"
        "  gpt, <message>        — send to GPT\n"
        "  gemini, <message>     — send to Gemini\n"
        "  perplexity, <message> — send to Perplexity (also: pplx,)\n"
        "  (no prefix)           — send to current selection"
    )

    def _handle_command(self, text: str) -> bool:
        stripped = text.strip()
        if not stripped.startswith("//"):
            return False

        parts = stripped.split(None, 1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd == "//help":
            self._chat.add_note("Help")
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
        if cmd == "//marklast":
            return self._handle_marklast(arg)
        if cmd == "//limit":
            return self._handle_limit(arg)
        if cmd == "//marks":
            return self._handle_marks()
        if cmd == "//select":
            return self._handle_select(arg)
        if cmd == "//providers":
            return self._handle_providers()

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
        name = tag
        count = len(self._current_conv.messages)
        self._db.set_mark(self._current_conv.id, name, count)
        label = f"mark '{tag}'" if tag else "mark (unnamed)"
        self._chat.add_note(f"{label} set at message {count}")
        return True

    def _handle_marklast(self, tag: str) -> bool:
        if not self._current_conv:
            self._on_new_chat()
        if tag.upper() == "ALL":
            self._chat.add_note("Error: 'ALL' is not allowed as a mark name")
            return True

        # Find the position just before the last user message
        messages = self._current_conv.messages
        last_user_idx = None
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].role == Role.USER:
                last_user_idx = i
                break

        if last_user_idx is None:
            self._chat.add_note("Error: no user message found to mark before")
            return True

        name = tag
        self._db.set_mark(self._current_conv.id, name, last_user_idx)
        label = f"mark '{tag}'" if tag else "mark (unnamed)"
        self._chat.add_note(f"{label} set before last request (message {last_user_idx})")
        return True

    def _handle_marks(self) -> bool:
        if not self._current_conv:
            self._chat.add_note("No active conversation")
            return True
        marks = self._db.list_marks(self._current_conv.id)
        self._chat.add_mark_list(marks)
        return True

    def _handle_limit(self, tag: str) -> bool:
        if not self._current_conv:
            self._on_new_chat()
        if tag.upper() == "ALL":
            self._current_conv.limit_mark = None
            self._db.set_conversation_limit(self._current_conv.id, None)
            self._chat.add_note("limit removed — full chat history will be sent")
            return True
        name = tag
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

    def _handle_select(self, arg: str) -> bool:
        if not self._router:
            self._chat.add_note("Error: no providers configured")
            return True
        if not self._current_conv:
            self._on_new_chat()

        configured = set(self._router._providers.keys())

        if arg.strip().upper() == "ALL":
            selected = [p for p in Provider if p in configured]
            if not selected:
                self._chat.add_note("Error: no providers configured")
                return True
            self._router.set_selection(selected)
            names = ", ".join(_PROVIDER_DISPLAY[p] for p in selected)
            self._chat.add_note(f"selected: {names}")
        else:
            # Parse comma-separated provider names
            from mchat.router import PREFIX_TO_PROVIDER
            requested: list[Provider] = []
            unknown: list[str] = []
            for name in arg.split(","):
                name = name.strip().lower()
                if not name:
                    continue
                p = PREFIX_TO_PROVIDER.get(name)
                if p and p not in requested:
                    requested.append(p)
                else:
                    unknown.append(name)

            if unknown:
                self._chat.add_note(f"Error: unknown provider(s): {', '.join(unknown)}")

            # Filter to configured only
            skipped = [p for p in requested if p not in configured]
            valid = [p for p in requested if p in configured]

            if skipped:
                names = ", ".join(_PROVIDER_DISPLAY[p] for p in skipped)
                self._chat.add_note(f"{names} skipped (no API key)")

            if not valid:
                self._chat.add_note("Error: no valid providers in selection")
                return True

            self._router.set_selection(valid)
            names = ", ".join(_PROVIDER_DISPLAY[p] for p in valid)
            self._chat.add_note(f"selected: {names}")

        self._save_selection()
        self._sync_checkboxes_from_selection()
        self._update_input_placeholder()
        self._update_input_color()
        return True

    def _handle_providers(self) -> bool:
        lines: list[str] = []
        configured = set(self._router._providers.keys()) if self._router else set()
        for p in Provider:
            name = _PROVIDER_DISPLAY[p]
            if p not in configured:
                lines.append(f"  {name} (no API key)")
            else:
                lines.append(f"  {name}")
        # Render as note
        self._chat.add_note("Providers")
        cursor = self._chat.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        fmt = QTextBlockFormat()
        fmt.setBackground(QColor("#f5f5f5"))
        for line in lines:
            cursor.insertBlock(fmt)
            char_fmt = cursor.charFormat()
            char_fmt.setForeground(QColor("#666"))
            cursor.insertText(line, char_fmt)
        self._chat._scroll_to_bottom()
        return True

    def _save_selection(self) -> None:
        """Persist the current selection to the conversation."""
        if self._current_conv and self._router:
            sel_str = ",".join(p.value for p in self._router.selection)
            self._current_conv.last_provider = sel_str
            self._db.update_conversation_last_provider(
                self._current_conv.id, sel_str
            )

    # ------------------------------------------------------------------

    def _on_message_submitted(self, text: str) -> None:
        if text.strip().startswith("//"):
            self._handle_command(text)
            return

        if not self._router:
            QMessageBox.warning(
                self, "No API Keys",
                "Please configure at least one API key in Settings.",
            )
            return

        if not self._current_conv:
            self._on_new_chat()

        # Route message
        targets, cleaned_text = self._router.parse(text)

        # Validate all targets are configured
        configured = set(self._router._providers.keys())
        missing = [p for p in targets if p not in configured]
        targets = [p for p in targets if p in configured]
        if missing:
            names = ", ".join(_PROVIDER_DISPLAY[p] for p in missing)
            self._chat.add_note(f"{names} not configured — skipped")
        if not targets:
            QMessageBox.warning(
                self, "No Provider Available",
                "None of the target providers have API keys configured.",
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
        self._save_selection()
        self._sync_checkboxes_from_selection()

        if len(targets) == 1:
            self._send_single(targets[0])
        else:
            self._send_multi(targets)

    def _send_single(self, provider_id: Provider) -> None:
        """Send to a single provider."""
        model = self._selected_model(provider_id)
        provider = self._router.get_provider(provider_id)

        self._set_combo_waiting(provider_id, True)

        if self._chat._incremental:
            context_messages = self._build_context(provider_id)
            # Incremental mode: stream tokens to UI as they arrive
            assistant_msg = Message(
                role=Role.ASSISTANT,
                content="",
                provider=provider_id,
                model=model,
                conversation_id=self._current_conv.id,
            )
            self._chat.begin_streaming(assistant_msg)

            self._stream_worker = StreamWorker(provider, context_messages, model)
            self._stream_worker.token_received.connect(self._chat.append_token)
            self._stream_worker.stream_complete.connect(
                lambda full_text, inp, out, est: self._on_stream_complete(
                    full_text, provider_id, model, inp, out, est
                )
            )
            self._stream_worker.stream_error.connect(self._on_stream_error)
            self._stream_worker.start()
        else:
            # Batch mode: collect silently, render when complete
            self._send_multi([provider_id])

    def _send_multi(self, targets: list[Provider]) -> None:
        """Send to multiple providers simultaneously, render when each completes."""
        self._multi_workers.clear()

        for provider_id in targets:
            model = self._selected_model(provider_id)
            provider = self._router.get_provider(provider_id)
            self._set_combo_waiting(provider_id, True)
            context_messages = self._build_context(provider_id)

            worker = StreamWorker(provider, context_messages, model)
            worker.stream_complete.connect(
                lambda full_text, inp, out, est, pid=provider_id, mdl=model: (
                    self._on_multi_complete(pid, mdl, full_text, inp, out, est)
                )
            )
            worker.stream_error.connect(
                lambda error, pid=provider_id: self._on_multi_error(pid, error)
            )
            self._multi_workers[provider_id] = worker
            worker.start()

    # ------------------------------------------------------------------
    # Multi-provider completion
    # ------------------------------------------------------------------

    def _on_multi_complete(
        self,
        provider_id: Provider,
        model: str,
        full_text: str,
        input_tokens: int,
        output_tokens: int,
        estimated: bool = False,
    ) -> None:
        self._set_combo_waiting(provider_id, False)
        self._multi_workers.pop(provider_id, None)

        label = f"{_PROVIDER_DISPLAY[provider_id]}'s take"
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

        cost = estimate_cost(model, input_tokens, output_tokens)
        if cost is not None and self._current_conv:
            self._db.add_conversation_spend(
                self._current_conv.id, provider_id.value, cost, estimated
            )
        self._update_spend_labels()

        if not self._multi_workers:
            self._input.set_enabled(True)
            self._update_input_placeholder()
            self._update_input_color()

    def _on_multi_error(self, provider_id: Provider, error: str) -> None:
        self._set_combo_waiting(provider_id, False)
        self._multi_workers.pop(provider_id, None)

        error_msg = Message(
            role=Role.ASSISTANT,
            content=f"[Error from {provider_id.value}: {error}]",
            provider=provider_id,
            conversation_id=self._current_conv.id,
        )
        self._chat.add_message(error_msg)

        if not self._multi_workers:
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
        estimated: bool = False,
    ) -> None:
        self._set_combo_waiting(provider_id, False)
        msg = self._chat.end_streaming()
        if msg:
            msg.content = full_text
            self._db.add_message(msg)
            self._current_conv.messages.append(msg)

        cost = estimate_cost(model, input_tokens, output_tokens)
        if cost is not None and self._current_conv:
            self._db.add_conversation_spend(
                self._current_conv.id, provider_id.value, cost, estimated
            )
        self._update_spend_labels()

        self._input.set_enabled(True)
        self._update_input_placeholder()
        self._update_input_color()
        self._stream_worker = None

    def _on_stream_error(self, error: str) -> None:
        for p in Provider:
            self._set_combo_waiting(p, False)
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
            self._apply_all_combo_styles()
            self._update_input_placeholder()
            self._update_input_color()
            new_size = int(self._config.get("font_size") or 14)
            if new_size != self._font_size:
                self._font_size = new_size
                self._apply_font_size()
            self._chat.update_colors(
                **{meta["color_key"]: self._config.get(meta["color_key"])
                   for meta in PROVIDER_META.values()},
                color_user=self._config.get("color_user"),
            )
