# ------------------------------------------------------------------
# Component: ChatWidget
# Responsibility: The scrollable chat view as a QTextEdit subclass.
#                 Owns state (message list, colours, shading config,
#                 font size) and the public widget API (add_message,
#                 load_messages, clear_messages, scroll_to_message,
#                 add_note, add_mark_list, find_text). Delegates
#                 low-level document mutation to ChatDocumentMixin
#                 and HTML export + copy-with-prefix to ChatExportMixin.
# Collaborators: PySide6, markdown, chat_document, chat_export
# ------------------------------------------------------------------
from __future__ import annotations

import markdown

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QTextBlockFormat,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
)
from PySide6.QtWidgets import QTextEdit, QWidget

from mchat.models.message import Message
from mchat.ui.chat_document import ChatDocumentMixin
from mchat.ui.chat_export import ChatExportMixin

# Default background colours per participant
COLOR_USER = "#d4d4d4"
COLOR_CLAUDE = "#b0b0b0"
COLOR_OPENAI = "#e8e8e8"
COLOR_GEMINI = "#c8d8e8"
COLOR_PERPLEXITY = "#d8c8e8"

# Document-level stylesheet applied to rendered HTML
_DOC_CSS = """
    code  { background-color: rgba(0,0,0,0.06); padding: 1px 4px;
            font-family: Consolas, 'Courier New', monospace; }
    pre   { background-color: rgba(0,0,0,0.06); padding: 8px;
            font-family: Consolas, 'Courier New', monospace;
            white-space: pre-wrap; }
    table { border-collapse: collapse; margin: 0; }
    th, td { border: 1px solid #999; padding: 4px 8px; }
    th    { background-color: rgba(0,0,0,0.08); font-weight: bold; }
"""


class ChatWidget(ChatDocumentMixin, ChatExportMixin, QTextEdit):
    def __init__(
        self,
        font_size: int = 14,
        color_user: str = COLOR_USER,
        color_claude: str = COLOR_CLAUDE,
        color_openai: str = COLOR_OPENAI,
        color_gemini: str = COLOR_GEMINI,
        color_perplexity: str = COLOR_PERPLEXITY,
        exclude_shade_mode: str = "darken",
        exclude_shade_amount: int = 20,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self._font_size = font_size
        self._colors: dict[str, str] = {
            "user": color_user,
            "claude": color_claude,
            "openai": color_openai,
            "gemini": color_gemini,
            "perplexity": color_perplexity,
        }
        self._exclude_shade_mode = exclude_shade_mode
        self._exclude_shade_amount = exclude_shade_amount
        # Optional callback for column-aware rebuild (set by MainWindow)
        self._rebuild_callback = None
        self._messages: list[Message] = []
        self._message_positions: list[int] = []  # document position of each message start
        # Message indices excluded from provider context (e.g. before //limit).
        # Rendered with paler colours.
        self._excluded_indices: set[int] = set()
        self._block_roles: dict[int, tuple] = {}
        self._is_empty = True
        self._md = markdown.Markdown(
            extensions=["tables", "fenced_code", "sane_lists"]
        )
        self._build_ui()

    def _build_ui(self) -> None:
        self.setStyleSheet("QTextEdit { border: none; }")
        # Set viewport background so block format backgrounds are the
        # primary colour source — no white bleeding through
        pal = self.palette()
        pal.setColor(pal.ColorRole.Base, QColor("#f5f5f5"))
        self.setPalette(pal)
        self.document().setDocumentMargin(16)
        self.document().setDefaultStyleSheet(_DOC_CSS)
        self._apply_default_font()

    # ------------------------------------------------------------------
    # Widget plumbing
    # ------------------------------------------------------------------

    def find_text(self, text: str, backward: bool = False) -> bool:
        """Find and select text in the document. Returns True if found."""
        if not text:
            return False
        flags = QTextDocument.FindFlag(0)
        if backward:
            flags |= QTextDocument.FindFlag.FindBackward
        return self.find(text, flags)

    def resizeEvent(self, event) -> None:
        """Keep document width matching viewport so block backgrounds span full width."""
        super().resizeEvent(event)
        self.document().setTextWidth(self.viewport().width())

    def mousePressEvent(self, event) -> None:
        """Handle clicks on mark links (mchat-mark:<index>)."""
        anchor = self.anchorAt(event.pos())
        if anchor.startswith("mchat-mark:"):
            try:
                msg_index = int(anchor.split(":", 1)[1])
                self.scroll_to_message(msg_index)
            except (ValueError, IndexError):
                pass
            return
        super().mousePressEvent(event)

    def _apply_default_font(self) -> None:
        font = self.document().defaultFont()
        font.setPixelSize(self._font_size)
        self.document().setDefaultFont(font)

    # ------------------------------------------------------------------
    # Public API (notes, marks, scroll, bulk ops)
    # ------------------------------------------------------------------

    def scroll_to_message(self, index: int) -> None:
        """Scroll the view so that message at the given index is visible."""
        if 0 <= index < len(self._message_positions):
            pos = self._message_positions[index]
            cursor = self.textCursor()
            cursor.setPosition(pos)
            self.setTextCursor(cursor)
            self.ensureCursorVisible()

    def add_mark_list(self, marks: list[tuple[str, int]]) -> None:
        """Insert a clickable list of marks. Each mark name is a link."""
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)

        fmt = QTextBlockFormat()
        fmt.setBackground(QColor("#f5f5f5"))

        if self._is_empty:
            cursor.setBlockFormat(fmt)
            self._is_empty = False
        else:
            cursor.insertBlock(fmt)

        char_fmt = cursor.charFormat()
        char_fmt.setForeground(QColor("#888"))
        cursor.insertText("  — Marks —", char_fmt)

        if not marks:
            cursor.insertBlock(fmt)
            cursor.insertText("    (no marks set)", char_fmt)
        else:
            for name, msg_count in marks:
                cursor.insertBlock(fmt)
                label = name if name else "(unnamed)"
                cursor.insertHtml(
                    f"&nbsp;&nbsp;&nbsp;&nbsp;"
                    f'<a href="mchat-mark:{msg_count}" '
                    f'style="color: #4a90d9; text-decoration: underline;">'
                    f"{label}</a>"
                    f'<span style="color: #888;"> — at message {msg_count}</span>'
                )

        self._scroll_to_bottom()

    def add_note(self, text: str) -> None:
        """Insert an ephemeral visual note (not part of _messages, lost on rebuild)."""
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)

        fmt = QTextBlockFormat()
        fmt.setBackground(QColor("#f5f5f5"))

        if self._is_empty:
            cursor.setBlockFormat(fmt)
            self._is_empty = False
        else:
            cursor.insertBlock(fmt)

        char_fmt = QTextCharFormat()
        char_fmt.setForeground(QColor("#888"))
        char_fmt.setBackground(QColor("#f5f5f5"))
        cursor.insertText(f"  — {text} —", char_fmt)
        self._scroll_to_bottom()

    def add_message(self, message: Message) -> None:
        self._messages.append(message)
        self._insert_rendered(message)
        self._scroll_to_bottom()

    def load_messages(self, messages: list[Message]) -> None:
        """Bulk-load messages with suppressed layout updates for speed."""
        self.clear_messages()
        self.setUpdatesEnabled(False)
        try:
            for msg in messages:
                self._messages.append(msg)
                self._insert_rendered(msg)
        finally:
            self.setUpdatesEnabled(True)
        self._scroll_to_bottom()

    def clear_messages(self) -> None:
        self.clear()
        self._messages.clear()
        self._message_positions.clear()
        self._block_roles.clear()
        self._excluded_indices.clear()
        self._is_empty = True

    def update_font_size(self, size: int) -> None:
        self._font_size = size
        self._apply_default_font()
        self._rebuild()

    def update_colors(self, **colors: str) -> None:
        """Update colors by key: color_user, color_claude, etc."""
        for key, value in colors.items():
            # Accept both "color_claude" and "claude" forms
            name = key.removeprefix("color_")
            self._colors[name] = value
        self._rebuild()

    def update_shading(self, mode: str, amount: int) -> None:
        """Update exclude shading mode and amount, re-render."""
        self._exclude_shade_mode = mode
        self._exclude_shade_amount = amount
        self._rebuild()

    def _scroll_to_bottom(self) -> None:
        QTimer.singleShot(
            50,
            lambda: self.verticalScrollBar().setValue(
                self.verticalScrollBar().maximum()
            ),
        )
