# ------------------------------------------------------------------
# Component: MessageBubble
# Responsibility: Render a single chat message with provider-specific styling
# Collaborators: PySide6, models.message
# ------------------------------------------------------------------
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout

from mchat.models.message import Message, Provider, Role

# Background colors per participant
COLOR_USER = "#d4d4d4"       # neutral gray — user messages
COLOR_CLAUDE = "#b0b0b0"     # slightly darker — Claude responses
COLOR_OPENAI = "#e8e8e8"     # slightly lighter — GPT responses

PROVIDER_LABELS = {
    Provider.CLAUDE: "Claude",
    Provider.OPENAI: "ChatGPT",
}


class MessageBubble(QFrame):
    def __init__(self, message: Message, font_size: int = 14, parent=None) -> None:
        super().__init__(parent)
        self._message = message
        self._font_size = font_size
        self._build_ui()

    def _build_ui(self) -> None:
        if self._message.role == Role.USER:
            bg = COLOR_USER
            sender = "You"
        elif self._message.provider == Provider.CLAUDE:
            bg = COLOR_CLAUDE
            sender = "Claude"
        elif self._message.provider == Provider.OPENAI:
            bg = COLOR_OPENAI
            sender = "ChatGPT"
        else:
            bg = COLOR_USER
            sender = "Assistant"

        self.setStyleSheet(
            f"MessageBubble {{ background-color: {bg}; border-radius: 8px; padding: 8px; }}"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(4)

        # Sender label
        self._sender_label = QLabel(f"<b>{sender}</b>")
        self._sender_label.setStyleSheet(f"font-size: {self._font_size - 2}px; color: #444;")
        layout.addWidget(self._sender_label)

        # Message content
        self._content_label = QLabel(self._message.content)
        self._content_label.setWordWrap(True)
        self._content_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._content_label.setStyleSheet(f"font-size: {self._font_size}px; color: #1a1a1a;")
        layout.addWidget(self._content_label)

        # Model tag (for assistant messages)
        self._model_label = None
        if self._message.model and self._message.role == Role.ASSISTANT:
            self._model_label = QLabel(self._message.model)
            self._model_label.setStyleSheet(f"font-size: {self._font_size - 4}px; color: #888;")
            layout.addWidget(self._model_label)

    def update_content(self, text: str) -> None:
        """Update the message content (used during streaming)."""
        self._message.content = text
        self._content_label.setText(text)

    def update_font_size(self, size: int) -> None:
        self._font_size = size
        self._sender_label.setStyleSheet(f"font-size: {size - 2}px; color: #444;")
        self._content_label.setStyleSheet(f"font-size: {size}px; color: #1a1a1a;")
        if self._model_label:
            self._model_label.setStyleSheet(f"font-size: {size - 4}px; color: #888;")
