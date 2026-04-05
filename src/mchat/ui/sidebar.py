# ------------------------------------------------------------------
# Component: Sidebar
# Responsibility: Conversation list with new-chat and selection
# Collaborators: PySide6, models.conversation
# ------------------------------------------------------------------
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QVBoxLayout,
)

from mchat.models.conversation import Conversation


class Sidebar(QFrame):
    conversation_selected = Signal(int)  # conversation id
    new_chat_requested = Signal()
    rename_requested = Signal(int, str)  # conversation id, new title
    save_requested = Signal(int)    # conversation id
    delete_requested = Signal(int)  # conversation id

    def __init__(self, font_size: int = 14, parent=None) -> None:
        super().__init__(parent)
        self._font_size = font_size
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

        self._title = QLabel("mchat")
        self._title.setStyleSheet(f"color: white; font-size: {self._font_size + 4}px; font-weight: bold;")
        header_layout.addWidget(self._title)
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
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._show_context_menu)
        self._apply_list_style()
        self._list.currentItemChanged.connect(self._on_item_changed)
        layout.addWidget(self._list)

    def _apply_list_style(self) -> None:
        self._list.setStyleSheet(
            f"QListWidget {{ background-color: #2b2b2b; border: none; color: white; "
            f"font-size: {self._font_size}px; padding: 4px; }}"
            f"QListWidget::item {{ padding: 10px 12px; border-radius: 6px; margin: 2px 4px; }}"
            f"QListWidget::item:selected {{ background-color: #444; }}"
            f"QListWidget::item:hover {{ background-color: #3a3a3a; }}"
        )

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

    def update_conversation_title(self, conv_id: int, title: str) -> None:
        """Update a single item's displayed title in place.

        Avoids a full reload+select cycle when only the title changes.
        """
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == conv_id:
                item.setText(title)
                if conv_id in self._conversations:
                    self._conversations[conv_id].title = title
                return

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

    def _show_context_menu(self, pos) -> None:
        item = self._list.itemAt(pos)
        if not item:
            return
        conv_id = item.data(Qt.ItemDataRole.UserRole)

        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu { background-color: #333; color: white; padding: 4px; }"
            "QMenu::item { padding: 6px 24px; }"
            "QMenu::item:selected { background-color: #555; }"
        )

        rename_action = menu.addAction("Rename")
        save_action = menu.addAction("Save as HTML...")
        delete_action = menu.addAction("Delete")

        action = menu.exec(self._list.mapToGlobal(pos))
        if action == rename_action:
            current_title = item.text()
            new_title, ok = QInputDialog.getText(
                self, "Rename Chat", "New name:", text=current_title
            )
            if ok and new_title.strip():
                self.rename_requested.emit(conv_id, new_title.strip())
        elif action == save_action:
            self.save_requested.emit(conv_id)
        elif action == delete_action:
            self.delete_requested.emit(conv_id)

    def update_font_size(self, size: int) -> None:
        self._font_size = size
        self._title.setStyleSheet(f"color: white; font-size: {size + 4}px; font-weight: bold;")
        self._apply_list_style()
