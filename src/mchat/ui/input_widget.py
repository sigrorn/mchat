# ------------------------------------------------------------------
# Component: InputWidget
# Responsibility: Message input area with send button
# Collaborators: PySide6
# ------------------------------------------------------------------
from __future__ import annotations

import re

from PySide6.QtCore import QMimeData, Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QTextEdit, QWidget

# Matches lines like //user, //claude, //gpt (sonnet-4), //gemini, //perplexity (sonar), etc.
_SPEAKER_PREFIX = re.compile(
    r"^//(user|claude|gpt|gemini|perplexity|pplx|assistant)(\s*\(.*\))?\s*$",
    re.MULTILINE,
)


class _PasteCleanTextEdit(QTextEdit):
    """QTextEdit that strips //speaker prefix lines on paste."""

    def insertFromMimeData(self, source: QMimeData) -> None:
        text = source.text()
        if text and _SPEAKER_PREFIX.search(text):
            # Normalize line endings and strip prefix lines
            lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
            cleaned = "\n".join(
                line for line in lines
                if not _SPEAKER_PREFIX.match(line)
            ).strip()
            if cleaned:
                clean_mime = QMimeData()
                clean_mime.setText(cleaned)
                super().insertFromMimeData(clean_mime)
            # else: all lines were prefixes — paste nothing
        else:
            super().insertFromMimeData(source)


class InputWidget(QWidget):
    message_submitted = Signal(str)

    def __init__(self, font_size: int = 14, parent=None) -> None:
        super().__init__(parent)
        self._font_size = font_size
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 8, 16, 8)

        self._text_edit = _PasteCleanTextEdit()
        self._text_edit.setPlaceholderText("")
        self._text_edit.setMaximumHeight(100)
        self._apply_text_edit_style()
        self._text_edit.installEventFilter(self)
        layout.addWidget(self._text_edit)

        self._send_btn = QPushButton("Send")
        self._send_btn.setFixedWidth(70)
        self._apply_send_btn_style()
        self._send_btn.clicked.connect(self._submit)
        layout.addWidget(self._send_btn)

    def _apply_text_edit_style(self) -> None:
        bg = getattr(self, "_bg_color", "white")
        self._text_edit.setStyleSheet(
            f"QTextEdit {{ border: 1px solid #ccc; border-radius: 8px; padding: 8px; "
            f"font-size: {self._font_size}px; background: {bg}; }}"
        )

    def set_background(self, color: str) -> None:
        self._bg_color = color
        self._apply_text_edit_style()

    def _apply_send_btn_style(self) -> None:
        self._send_btn.setStyleSheet(
            f"QPushButton {{ background-color: #6b5ce7; color: white; border: none; "
            f"border-radius: 8px; padding: 8px; font-size: {self._font_size}px; font-weight: bold; }}"
            f"QPushButton:hover {{ background-color: #5a4bd6; }}"
            f"QPushButton:disabled {{ background-color: #ccc; }}"
        )

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
        if enabled:
            self._text_edit.setFocus()

    def set_placeholder(self, text: str) -> None:
        self._text_edit.setPlaceholderText(text)

    def update_font_size(self, size: int) -> None:
        self._font_size = size
        self._apply_text_edit_style()
        self._apply_send_btn_style()
