# ------------------------------------------------------------------
# Component: Sidebar
# Responsibility: Conversation list with new-chat and selection
# Collaborators: PySide6, models.conversation
# ------------------------------------------------------------------
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from mchat.models.conversation import Conversation


class Sidebar(QFrame):
    conversation_selected = Signal(int)  # conversation id
    new_chat_requested = Signal()
    delete_requested = Signal(int)  # conversation id

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedWidth(250)
        self._conversations: dict[int, Conversation] = {}
        self._build_ui()

    def _build_ui(self) -> None:
        self.setStyleSheet(
            "Sidebar { background-color: #2b2b2b; border-right: 1px solid #3a3a3a; }"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header
        header = QFrame()
        header.setStyleSheet("background-color: #2b2b2b; padding: 8px;")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(12, 12, 12, 12)

        title = QLabel("mchat")
        title.setStyleSheet("color: white; font-size: 18px; font-weight: bold;")
        header_layout.addWidget(title)
        header_layout.addStretch()

        new_btn = QPushButton("+")
        new_btn.setFixedSize(32, 32)
        new_btn.setStyleSheet(
            "QPushButton { background-color: #444; color: white; border: none; "
            "border-radius: 16px; font-size: 18px; font-weight: bold; }"
            "QPushButton:hover { background-color: #555; }"
        )
        new_btn.clicked.connect(self.new_chat_requested.emit)
        header_layout.addWidget(new_btn)

        layout.addWidget(header)

        # Conversation list
        self._list = QListWidget()
        self._list.setStyleSheet(
            "QListWidget { background-color: #2b2b2b; border: none; color: white; "
            "font-size: 14px; padding: 4px; }"
            "QListWidget::item { padding: 10px 12px; border-radius: 6px; margin: 2px 4px; }"
            "QListWidget::item:selected { background-color: #444; }"
            "QListWidget::item:hover { background-color: #3a3a3a; }"
        )
        self._list.currentItemChanged.connect(self._on_item_changed)
        layout.addWidget(self._list)

    def set_conversations(self, conversations: list[Conversation]) -> None:
        self._list.blockSignals(True)
        self._list.clear()
        self._conversations.clear()
        for conv in conversations:
            item = QListWidgetItem(conv.title)
            item.setData(Qt.ItemDataRole.UserRole, conv.id)
            self._list.addItem(item)
            self._conversations[conv.id] = conv
        self._list.blockSignals(False)

    def select_conversation(self, conv_id: int) -> None:
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == conv_id:
                self._list.setCurrentItem(item)
                return

    def _on_item_changed(self, current: QListWidgetItem | None, _previous) -> None:
        if current:
            conv_id = current.data(Qt.ItemDataRole.UserRole)
            self.conversation_selected.emit(conv_id)
