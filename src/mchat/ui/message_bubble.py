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
    def __init__(self, message: Message, parent=None) -> None:
        super().__init__(parent)
        self._message = message
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
        sender_label = QLabel(f"<b>{sender}</b>")
        sender_label.setStyleSheet("font-size: 12px; color: #444;")
        layout.addWidget(sender_label)

        # Message content
        content_label = QLabel(self._message.content)
        content_label.setWordWrap(True)
        content_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        content_label.setStyleSheet("font-size: 14px; color: #1a1a1a;")
        layout.addWidget(content_label)

        # Model tag (for assistant messages)
        if self._message.model and self._message.role == Role.ASSISTANT:
            model_label = QLabel(self._message.model)
            model_label.setStyleSheet("font-size: 10px; color: #888;")
            layout.addWidget(model_label)

    def update_content(self, text: str) -> None:
        """Update the message content (used during streaming)."""
        self._message.content = text
        content_label = self.layout().itemAt(1).widget()
        if isinstance(content_label, QLabel):
            content_label.setText(text)
