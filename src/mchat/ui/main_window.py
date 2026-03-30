# ------------------------------------------------------------------
# Component: MainWindow
# Responsibility: Top-level window — wires sidebar, chat, input, providers
# Collaborators: PySide6, all ui components, router, db, config, workers
# ------------------------------------------------------------------
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
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

        self._init_providers()
        self._build_ui()
        self._setup_shortcuts()
        self._load_conversations()

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

        # Top bar with settings button
        top_bar = QFrame()
        top_bar.setStyleSheet("background-color: #f5f5f5; border-bottom: 1px solid #ddd;")
        top_bar_layout = QHBoxLayout(top_bar)
        top_bar_layout.setContentsMargins(16, 8, 16, 8)
        top_bar_layout.addStretch()

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

    def _apply_settings_btn_style(self) -> None:
        self._settings_btn.setStyleSheet(
            f"QPushButton {{ background: none; border: 1px solid #ccc; border-radius: 6px; "
            f"padding: 4px 12px; color: #666; font-size: {self._font_size - 1}px; }}"
            f"QPushButton:hover {{ background-color: #eee; }}"
        )

    def _setup_shortcuts(self) -> None:
        zoom_in = QShortcut(QKeySequence("Ctrl+="), self)
        zoom_in.activated.connect(self._zoom_in)

        zoom_in2 = QShortcut(QKeySequence("Ctrl++"), self)
        zoom_in2.activated.connect(self._zoom_in)

        zoom_out = QShortcut(QKeySequence("Ctrl+-"), self)
        zoom_out.activated.connect(self._zoom_out)

        zoom_reset = QShortcut(QKeySequence("Ctrl+0"), self)
        zoom_reset.activated.connect(self._zoom_reset)

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
        conv = self._db.create_conversation()
        self._current_conv = conv
        self._chat.clear_messages()
        self._load_conversations()
        self._sidebar.select_conversation(conv.id)

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
            content=text,  # store original with prefix
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

        # Start streaming response
        provider = self._router.get_provider(provider_id)
        model = self._config.get(f"{provider_id.value}_model") if provider_id == Provider.OPENAI else self._config.get("claude_model")

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

        # Build message list for context (use cleaned text for the current message)
        context_messages = list(self._current_conv.messages)

        self._stream_worker = StreamWorker(provider, context_messages, model)
        self._stream_worker.token_received.connect(self._chat.append_token)
        self._stream_worker.stream_complete.connect(
            lambda full_text: self._on_stream_complete(full_text, provider_id, model)
        )
        self._stream_worker.stream_error.connect(self._on_stream_error)
        self._stream_worker.start()

    def _on_stream_complete(self, full_text: str, provider_id: Provider, model: str) -> None:
        msg = self._chat.end_streaming()
        if msg:
            msg.content = full_text
            self._db.add_message(msg)
            self._current_conv.messages.append(msg)
        self._input.set_enabled(True)
        self._stream_worker = None

    def _on_stream_error(self, error: str) -> None:
        self._chat.end_streaming()
        self._input.set_enabled(True)
        self._stream_worker = None
        QMessageBox.critical(self, "Error", f"Streaming failed:\n{error}")

    def _open_settings(self) -> None:
        dialog = SettingsDialog(self._config, self)
        if dialog.exec():
            self._init_providers()
            new_size = int(self._config.get("font_size") or 14)
            if new_size != self._font_size:
                self._font_size = new_size
                self._apply_font_size()
