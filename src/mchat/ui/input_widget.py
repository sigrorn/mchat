# ------------------------------------------------------------------
# Component: InputWidget
# Responsibility: Message input area with send button
# Collaborators: PySide6
# ------------------------------------------------------------------
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QTextEdit, QWidget


class InputWidget(QWidget):
    message_submitted = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 8, 16, 8)

        self._text_edit = QTextEdit()
        self._text_edit.setPlaceholderText("claude, Type a message...")
        self._text_edit.setMaximumHeight(100)
        self._text_edit.setStyleSheet(
            "QTextEdit { border: 1px solid #ccc; border-radius: 8px; padding: 8px; "
            "font-size: 14px; background: white; }"
        )
        self._text_edit.installEventFilter(self)
        layout.addWidget(self._text_edit)

        self._send_btn = QPushButton("Send")
        self._send_btn.setFixedWidth(70)
        self._send_btn.setStyleSheet(
            "QPushButton { background-color: #6b5ce7; color: white; border: none; "
            "border-radius: 8px; padding: 8px; font-size: 14px; font-weight: bold; }"
            "QPushButton:hover { background-color: #5a4bd6; }"
            "QPushButton:disabled { background-color: #ccc; }"
        )
        self._send_btn.clicked.connect(self._submit)
        layout.addWidget(self._send_btn)

    def eventFilter(self, obj, event) -> bool:
        if obj == self._text_edit and event.type() == event.Type.KeyPress:
            if event.key() == Qt.Key.Key_Return and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self._submit()
                return True
        return super().eventFilter(obj, event)

    def _submit(self) -> None:
        text = self._text_edit.toPlainText().strip()
        if text:
            self.message_submitted.emit(text)
            self._text_edit.clear()

    def set_enabled(self, enabled: bool) -> None:
        self._text_edit.setEnabled(enabled)
        self._send_btn.setEnabled(enabled)

    def set_placeholder(self, text: str) -> None:
        self._text_edit.setPlaceholderText(text)
