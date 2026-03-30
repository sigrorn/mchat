# ------------------------------------------------------------------
# Component: MainWindow
# Responsibility: Top-level window — wires sidebar, chat, input, providers
# Collaborators: PySide6, all ui components, router, db, config, workers
# ------------------------------------------------------------------
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
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
from mchat.workers.stream_worker import StreamWorker


class MainWindow(QMainWindow):
    def __init__(self, config: Config, db: Database) -> None:
        super().__init__()
        self._config = config
        self._db = db
        self._current_conv: Conversation | None = None
        self._stream_worker: StreamWorker | None = None
        self._router: Router | None = None
        self._font_size = int(self._config.get("font_size") or 14)

        # Session spend tracking (reset when the app restarts)
        self._session_spend: dict[Provider, float] = {
            Provider.CLAUDE: 0.0,
            Provider.OPENAI: 0.0,
        }

        self._init_providers()
        self._build_ui()
        self._populate_model_combos()
        self._setup_shortcuts()
        self._load_conversations()
        self._update_input_placeholder()

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
        self._chat = ChatWidget(font_size=self._font_size)
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

    def _update_spend_labels(self) -> None:
        for provider, label in [
            (Provider.CLAUDE, self._claude_spend_label),
            (Provider.OPENAI, self._openai_spend_label),
        ]:
            amount = self._session_spend[provider]
            label.setText(format_cost(amount) if amount else "$0.00000")

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

        self._chat.clear_messages()
        for msg in messages:
            self._chat.add_message(msg)

    def _on_new_chat(self) -> None:
        system_prompt = self._config.get("system_prompt")
        conv = self._db.create_conversation(system_prompt=system_prompt)
        self._current_conv = conv
        self._chat.clear_messages()
        self._load_conversations()
        self._sidebar.select_conversation(conv.id)

    # ------------------------------------------------------------------
    # Messaging
    # ------------------------------------------------------------------

    def _update_input_placeholder(self) -> None:
        """Set the input placeholder to reflect the current default provider."""
        if not self._router:
            self._input.set_placeholder("Configure an API key in Settings to start chatting")
            return
        current = self._router.last_used
        if current == Provider.CLAUDE:
            self._input.set_placeholder("Message Claude — start with gpt, to switch")
        else:
            self._input.set_placeholder("Message GPT — start with claude, to switch")

    def _selected_model(self, provider_id: Provider) -> str:
        """Return the model currently selected in the top-bar combo."""
        if provider_id == Provider.CLAUDE:
            return self._claude_combo.currentText()
        return self._openai_combo.currentText()

    def _on_message_submitted(self, text: str) -> None:
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
        provider_id, cleaned_text = self._router.parse(text)

        if provider_id not in self._router._providers:
            QMessageBox.warning(
                self, "Provider Not Configured",
                f"No API key configured for {provider_id.value}. Check Settings.",
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

        # Use the model selected in the top-bar combo (not just config)
        model = self._selected_model(provider_id)
        provider = self._router.get_provider(provider_id)

        # Create placeholder assistant message
        assistant_msg = Message(
            role=Role.ASSISTANT,
            content="",
            provider=provider_id,
            model=model,
            conversation_id=self._current_conv.id,
        )
        self._chat.begin_streaming(assistant_msg)
        self._input.set_enabled(False)

        context_messages: list[Message] = []
        if self._current_conv.system_prompt:
            context_messages.append(
                Message(role=Role.SYSTEM, content=self._current_conv.system_prompt)
            )
        context_messages.extend(self._current_conv.messages)

        self._stream_worker = StreamWorker(provider, context_messages, model)
        self._stream_worker.token_received.connect(self._chat.append_token)
        self._stream_worker.stream_complete.connect(
            lambda full_text, inp, out: self._on_stream_complete(
                full_text, provider_id, model, inp, out
            )
        )
        self._stream_worker.stream_error.connect(self._on_stream_error)
        self._stream_worker.start()

    def _on_stream_complete(
        self,
        full_text: str,
        provider_id: Provider,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        msg = self._chat.end_streaming()
        if msg:
            msg.content = full_text
            self._db.add_message(msg)
            self._current_conv.messages.append(msg)

        # Update session spend
        cost = estimate_cost(model, input_tokens, output_tokens)
        if cost is not None:
            self._session_spend[provider_id] += cost
        self._update_spend_labels()

        self._input.set_enabled(True)
        self._update_input_placeholder()
        self._stream_worker = None

    def _on_stream_error(self, error: str) -> None:
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
            new_size = int(self._config.get("font_size") or 14)
            if new_size != self._font_size:
                self._font_size = new_size
                self._apply_font_size()
