# ------------------------------------------------------------------
# Component: ChatWidget
# Responsibility: Scrollable chat message area
# Collaborators: PySide6, ui.message_bubble, models.message
# ------------------------------------------------------------------
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QFrame,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from mchat.models.message import Message
from mchat.ui.message_bubble import MessageBubble


class ChatWidget(QScrollArea):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._bubbles: list[MessageBubble] = []
        self._streaming_bubble: MessageBubble | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet("QScrollArea { border: none; background-color: #f5f5f5; }")

        self._container = QWidget()
        self._layout = QVBoxLayout(self._container)
        self._layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._layout.setSpacing(8)
        self._layout.setContentsMargins(16, 16, 16, 16)
        self._layout.addStretch()

        self.setWidget(self._container)

    def add_message(self, message: Message) -> MessageBubble:
        bubble = MessageBubble(message)
        # Insert before the stretch
        self._layout.insertWidget(self._layout.count() - 1, bubble)
        self._bubbles.append(bubble)
        self._scroll_to_bottom()
        return bubble

    def begin_streaming(self, message: Message) -> MessageBubble:
        """Add a bubble that will be updated as tokens arrive."""
        bubble = self.add_message(message)
        self._streaming_bubble = bubble
        return bubble

    def append_token(self, token: str) -> None:
        if self._streaming_bubble:
            current = self._streaming_bubble._message.content
            self._streaming_bubble.update_content(current + token)
            self._scroll_to_bottom()

    def end_streaming(self) -> Message | None:
        if self._streaming_bubble:
            msg = self._streaming_bubble._message
            self._streaming_bubble = None
            return msg
        return None

    def clear_messages(self) -> None:
        for bubble in self._bubbles:
            self._layout.removeWidget(bubble)
            bubble.deleteLater()
        self._bubbles.clear()
        self._streaming_bubble = None

    def _scroll_to_bottom(self) -> None:
        QTimer.singleShot(50, lambda: self.verticalScrollBar().setValue(
            self.verticalScrollBar().maximum()
        ))
