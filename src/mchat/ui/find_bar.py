# ------------------------------------------------------------------
# Component: FindBar
# Responsibility: Compact find-in-chat bar hosted above ChatWidget —
#                 forwards find operations to the chat's find_text().
# Collaborators: ui.chat_widget  (external: PySide6)
# ------------------------------------------------------------------
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QPushButton, QWidget

from mchat.ui.chat_widget import ChatWidget


class FindBar(QWidget):
    """Compact find bar that sits above the chat widget."""

    def __init__(self, chat_widget: ChatWidget, parent=None) -> None:
        super().__init__(parent)
        self._chat = chat_widget
        self._build_ui()
        self.hide()

    def _build_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(4)

        self._edit = QLineEdit()
        self._edit.setPlaceholderText("Find...")
        self._edit.returnPressed.connect(self._find_next)
        self._edit.textChanged.connect(self._on_text_changed)
        layout.addWidget(self._edit, stretch=1)

        self._prev_btn = QPushButton("▲")
        self._prev_btn.setFixedWidth(30)
        self._prev_btn.setToolTip("Find previous")
        self._prev_btn.clicked.connect(self._find_prev)
        layout.addWidget(self._prev_btn)

        self._next_btn = QPushButton("▼")
        self._next_btn.setFixedWidth(30)
        self._next_btn.setToolTip("Find next")
        self._next_btn.clicked.connect(self._find_next)
        layout.addWidget(self._next_btn)

        close_btn = QPushButton("✕")
        close_btn.setFixedWidth(30)
        close_btn.setToolTip("Close (Esc)")
        close_btn.clicked.connect(self.close_bar)
        layout.addWidget(close_btn)

        self.setStyleSheet(
            "FindBar { background-color: #e8e8e8; border-bottom: 1px solid #ccc; }"
        )

    def open_bar(self) -> None:
        self.show()
        self._edit.setFocus()
        self._edit.selectAll()

    def close_bar(self) -> None:
        self.hide()
        self._chat.setFocus()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.close_bar()
        else:
            super().keyPressEvent(event)

    def _on_text_changed(self, text: str) -> None:
        if text:
            # Move cursor to start so we search from the top
            cursor = self._chat.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.Start)
            self._chat.setTextCursor(cursor)
            self._chat.find_text(text)

    def _find_next(self) -> None:
        text = self._edit.text()
        if text:
            if not self._chat.find_text(text):
                # Wrap around to start
                cursor = self._chat.textCursor()
                cursor.movePosition(QTextCursor.MoveOperation.Start)
                self._chat.setTextCursor(cursor)
                self._chat.find_text(text)

    def _find_prev(self) -> None:
        text = self._edit.text()
        if text:
            if not self._chat.find_text(text, backward=True):
                # Wrap around to end
                cursor = self._chat.textCursor()
                cursor.movePosition(QTextCursor.MoveOperation.End)
                self._chat.setTextCursor(cursor)
                self._chat.find_text(text, backward=True)
