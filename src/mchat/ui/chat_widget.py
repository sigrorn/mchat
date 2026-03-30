# ------------------------------------------------------------------
# Component: ChatWidget
# Responsibility: Scrollable chat area rendered as a single QTextEdit
#                 with per-line background colour by speaker
# Collaborators: PySide6, models.message
# ------------------------------------------------------------------
from __future__ import annotations

from PySide6.QtCore import QMimeData, Qt, QTimer
from PySide6.QtGui import QColor, QTextBlockFormat, QTextCursor
from PySide6.QtWidgets import QTextEdit

from mchat.models.message import Message, Provider, Role

# Background colours per participant
COLOR_USER = "#d4d4d4"
COLOR_CLAUDE = "#b0b0b0"
COLOR_OPENAI = "#e8e8e8"


class ChatWidget(QTextEdit):
    def __init__(self, font_size: int = 14, parent=None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self._font_size = font_size
        self._messages: list[Message] = []
        self._block_roles: dict[int, tuple[Role, Provider | None]] = {}
        self._streaming_msg: Message | None = None
        self._streaming_block_fmt: QTextBlockFormat | None = None
        self._is_empty = True
        self._build_ui()

    def _build_ui(self) -> None:
        self.setStyleSheet(
            "QTextEdit { border: none; background-color: #f5f5f5; }"
        )
        self.document().setDocumentMargin(16)
        self._apply_default_font()

    def _apply_default_font(self) -> None:
        font = self.document().defaultFont()
        font.setPixelSize(self._font_size)
        self.document().setDefaultFont(font)

    # ------------------------------------------------------------------
    # Colour helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _color_for(message: Message) -> str:
        if message.role == Role.USER:
            return COLOR_USER
        if message.provider == Provider.CLAUDE:
            return COLOR_CLAUDE
        if message.provider == Provider.OPENAI:
            return COLOR_OPENAI
        return COLOR_USER

    def _make_block_fmt(self, message: Message) -> QTextBlockFormat:
        fmt = QTextBlockFormat()
        fmt.setBackground(QColor(self._color_for(message)))
        return fmt

    # ------------------------------------------------------------------
    # Public API (matches the interface MainWindow expects)
    # ------------------------------------------------------------------

    def add_message(self, message: Message) -> None:
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)

        block_fmt = self._make_block_fmt(message)
        role_info = (message.role, message.provider)

        lines = message.content.split("\n") if message.content else [""]
        for i, line in enumerate(lines):
            if self._is_empty and i == 0:
                cursor.setBlockFormat(block_fmt)
                self._is_empty = False
            else:
                cursor.insertBlock(block_fmt)
            cursor.insertText(line)
            self._block_roles[cursor.block().blockNumber()] = role_info

        self._messages.append(message)
        self._scroll_to_bottom()

    def begin_streaming(self, message: Message) -> None:
        self._streaming_msg = message
        self._streaming_block_fmt = self._make_block_fmt(message)
        role_info = (message.role, message.provider)

        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)

        if self._is_empty:
            cursor.setBlockFormat(self._streaming_block_fmt)
            self._is_empty = False
        else:
            cursor.insertBlock(self._streaming_block_fmt)

        self._block_roles[cursor.block().blockNumber()] = role_info
        self._messages.append(message)
        self._scroll_to_bottom()

    def append_token(self, token: str) -> None:
        if not self._streaming_msg:
            return
        self._streaming_msg.content += token

        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)

        role_info = (self._streaming_msg.role, self._streaming_msg.provider)
        parts = token.split("\n")
        for i, part in enumerate(parts):
            if i > 0:
                cursor.insertBlock(self._streaming_block_fmt)
                self._block_roles[cursor.block().blockNumber()] = role_info
            if part:
                cursor.insertText(part)

        self._scroll_to_bottom()

    def end_streaming(self) -> Message | None:
        if self._streaming_msg:
            msg = self._streaming_msg
            self._streaming_msg = None
            self._streaming_block_fmt = None
            return msg
        return None

    def clear_messages(self) -> None:
        self.clear()
        self._messages.clear()
        self._block_roles.clear()
        self._streaming_msg = None
        self._streaming_block_fmt = None
        self._is_empty = True

    def update_font_size(self, size: int) -> None:
        self._font_size = size
        self._apply_default_font()

    # ------------------------------------------------------------------
    # Copy with //user, //claude, //gpt prefixes at speaker transitions
    # ------------------------------------------------------------------

    def createMimeDataFromSelection(self) -> QMimeData:
        cursor = self.textCursor()
        if not cursor.hasSelection():
            return super().createMimeDataFromSelection()

        start = cursor.selectionStart()
        end = cursor.selectionEnd()

        doc = self.document()
        block = doc.findBlock(start)

        result_lines: list[str] = []
        prev_role_info: tuple[Role, Provider | None] | None = None

        while block.isValid() and block.position() < end:
            role_info = self._block_roles.get(block.blockNumber())

            # Insert prefix when speaker changes
            if role_info and role_info != prev_role_info:
                role, provider = role_info
                if role == Role.USER:
                    result_lines.append("//user")
                elif provider == Provider.CLAUDE:
                    result_lines.append("//claude")
                elif provider == Provider.OPENAI:
                    result_lines.append("//gpt")
                else:
                    result_lines.append("//assistant")
                prev_role_info = role_info

            # Extract selected portion of this block
            block_start = block.position()
            block_end = block_start + block.length() - 1  # exclude block separator
            sel_start = max(start, block_start)
            sel_end = min(end, block_end)

            if sel_start <= sel_end:
                text = block.text()
                result_lines.append(text[sel_start - block_start : sel_end - block_start])

            block = block.next()

        mime = QMimeData()
        mime.setText("\n".join(result_lines))
        return mime

    # ------------------------------------------------------------------

    def _scroll_to_bottom(self) -> None:
        QTimer.singleShot(
            50,
            lambda: self.verticalScrollBar().setValue(
                self.verticalScrollBar().maximum()
            ),
        )
