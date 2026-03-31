# ------------------------------------------------------------------
# Component: ChatWidget
# Responsibility: Scrollable chat area rendered as a single QTextEdit
#                 with per-line background colour by speaker and
#                 markdown rendering for assistant messages
# Collaborators: PySide6, markdown, models.message
# ------------------------------------------------------------------
from __future__ import annotations

import html as html_mod
import re

import markdown

from PySide6.QtCore import QMimeData, Qt, QTimer
from PySide6.QtGui import QColor, QTextBlockFormat, QTextCursor
from PySide6.QtWidgets import QTextEdit

from mchat.models.message import Message, Provider, Role

# Background colours per participant
COLOR_USER = "#d4d4d4"
COLOR_CLAUDE = "#b0b0b0"
COLOR_OPENAI = "#e8e8e8"

# Document-level stylesheet applied to rendered HTML
_DOC_CSS = """
    code  { background-color: rgba(0,0,0,0.06); padding: 1px 4px;
            font-family: Consolas, 'Courier New', monospace; }
    pre   { background-color: rgba(0,0,0,0.06); padding: 8px;
            font-family: Consolas, 'Courier New', monospace;
            white-space: pre-wrap; }
    table { border-collapse: collapse; margin: 4px 0; }
    th, td { border: 1px solid #999; padding: 4px 8px; }
    th    { background-color: rgba(0,0,0,0.08); font-weight: bold; }
"""



def _short_model(model: str | None) -> str:
    """Shorten a model id for the copy prefix.

    claude-sonnet-4-20250514 -> sonnet-4
    gpt-4.1-mini             -> 4.1-mini
    o3-mini                  -> o3-mini
    """
    if not model:
        return ""
    # Claude: strip 'claude-' prefix and date/version suffix
    m = re.match(r"^claude-(.+?)(-\d[\d-]*)?$", model)
    if m:
        return m.group(1)
    # GPT: strip 'gpt-' prefix
    if model.startswith("gpt-"):
        return model[4:]
    return model


# Role info stored per text-block: (role, provider, model)
_RoleInfo = tuple[Role, Provider | None, str | None]


class ChatWidget(QTextEdit):
    def __init__(
        self,
        font_size: int = 14,
        color_user: str = COLOR_USER,
        color_claude: str = COLOR_CLAUDE,
        color_openai: str = COLOR_OPENAI,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self._font_size = font_size
        self._color_user = color_user
        self._color_claude = color_claude
        self._color_openai = color_openai
        self._messages: list[Message] = []
        self._block_roles: dict[int, _RoleInfo] = {}
        self._streaming_msg: Message | None = None
        self._streaming_block_fmt: QTextBlockFormat | None = None
        self._streaming_start_pos: int = 0
        self._streaming_rendered_len: int = 0
        self._is_empty = True
        self._md = markdown.Markdown(
            extensions=["tables", "fenced_code", "sane_lists"]
        )
        self._build_ui()

    def _build_ui(self) -> None:
        self.setStyleSheet(
            "QTextEdit { border: none; background-color: #f5f5f5; }"
        )
        self.document().setDocumentMargin(16)
        self.document().setDefaultStyleSheet(_DOC_CSS)
        self._apply_default_font()

    def _apply_default_font(self) -> None:
        font = self.document().defaultFont()
        font.setPixelSize(self._font_size)
        self.document().setDefaultFont(font)

    # ------------------------------------------------------------------
    # Colour helpers
    # ------------------------------------------------------------------

    def _color_for(self, message: Message) -> str:
        if message.role == Role.USER:
            return self._color_user
        if message.provider == Provider.CLAUDE:
            return self._color_claude
        if message.provider == Provider.OPENAI:
            return self._color_openai
        return self._color_user

    def _make_block_fmt(self, message: Message) -> QTextBlockFormat:
        fmt = QTextBlockFormat()
        fmt.setBackground(QColor(self._color_for(message)))
        return fmt

    @staticmethod
    def _role_info(message: Message) -> _RoleInfo:
        return (message.role, message.provider, message.model)

    # ------------------------------------------------------------------
    # Markdown rendering
    # ------------------------------------------------------------------

    def _render(self, message: Message) -> str:
        """Return HTML for a message: markdown for assistants, escaped for user."""
        if message.role == Role.ASSISTANT and message.content:
            self._md.reset()
            return self._md.convert(message.content)
        text = html_mod.escape(message.content) if message.content else ""
        return text.replace("\n", "<br>")

    # ------------------------------------------------------------------
    # Inserting a fully-rendered message
    # ------------------------------------------------------------------

    def _insert_rendered(self, message: Message) -> None:
        """Insert a message as rendered HTML with background colour on every block."""
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)

        block_fmt = self._make_block_fmt(message)
        info = self._role_info(message)

        # Start a new block (or reuse the initial empty one)
        if self._is_empty:
            cursor.setBlockFormat(block_fmt)
            self._is_empty = False
        else:
            cursor.insertBlock(block_fmt)

        start_block = cursor.block().blockNumber()

        # Insert HTML content
        rendered = self._render(message)
        cursor.insertHtml(rendered)

        # Walk all blocks that were created and apply background + role
        end_block = cursor.block().blockNumber()
        doc = self.document()
        for bn in range(start_block, end_block + 1):
            block = doc.findBlockByNumber(bn)
            if block.isValid():
                bc = QTextCursor(block)
                bc.setBlockFormat(block_fmt)
                self._block_roles[bn] = info

    def _rebuild(self) -> None:
        """Re-render all messages with markdown formatting."""
        saved = list(self._messages)
        self.clear()
        self._messages.clear()
        self._block_roles.clear()
        self._is_empty = True
        for msg in saved:
            self._messages.append(msg)
            self._insert_rendered(msg)
        self._scroll_to_bottom()

    # ------------------------------------------------------------------
    # HTML export
    # ------------------------------------------------------------------

    def export_html(self) -> str:
        """Return a standalone HTML document with all messages."""
        parts: list[str] = []
        for msg in self._messages:
            colour = self._color_for(msg)
            content = self._render(msg)

            if msg.role == Role.USER:
                label = "You"
            elif msg.provider == Provider.CLAUDE:
                label = f"Claude ({_short_model(msg.model)})" if msg.model else "Claude"
            elif msg.provider == Provider.OPENAI:
                label = f"GPT ({_short_model(msg.model)})" if msg.model else "GPT"
            else:
                label = "Assistant"

            parts.append(
                f'<div style="background-color:{colour}; padding:12px 16px; '
                f'margin:0; border-radius:0;">'
                f'<div style="font-size:0.85em; color:#444; font-weight:bold; '
                f'margin-bottom:4px;">{label}</div>'
                f'{content}'
                f'</div>'
            )

        body = "\n".join(parts)
        return (
            "<!DOCTYPE html>\n"
            "<html><head><meta charset='utf-8'>\n"
            "<style>\n"
            "  body { font-family: -apple-system, Segoe UI, sans-serif;\n"
            f"         font-size: {self._font_size}px; margin: 0; padding: 0;\n"
            "         background: #f5f5f5; color: #1a1a1a; }\n"
            "  code { background: rgba(0,0,0,0.06); padding: 1px 4px;\n"
            "         font-family: Consolas, 'Courier New', monospace; }\n"
            "  pre  { background: rgba(0,0,0,0.06); padding: 8px;\n"
            "         font-family: Consolas, 'Courier New', monospace;\n"
            "         white-space: pre-wrap; overflow-x: auto; }\n"
            "  table { border-collapse: collapse; margin: 4px 0; }\n"
            "  th, td { border: 1px solid #999; padding: 4px 8px; }\n"
            "  th { background: rgba(0,0,0,0.08); font-weight: bold; }\n"
            "</style>\n"
            "</head><body>\n"
            f"{body}\n"
            "</body></html>"
        )

    # ------------------------------------------------------------------
    # Incremental streaming render (paragraph-based)
    # ------------------------------------------------------------------

    def _rerender_streaming(self) -> None:
        """Replace the streaming message's blocks with markdown-rendered HTML."""
        if not self._streaming_msg or not self._streaming_msg.content:
            return

        doc = self.document()
        start_block_num = doc.findBlock(self._streaming_start_pos).blockNumber()

        # Clear stale role entries for streaming blocks
        for bn in list(self._block_roles):
            if bn >= start_block_num:
                del self._block_roles[bn]

        # Delete current streaming content
        cursor = self.textCursor()
        cursor.setPosition(self._streaming_start_pos)
        cursor.movePosition(
            QTextCursor.MoveOperation.End, QTextCursor.MoveMode.KeepAnchor
        )
        cursor.removeSelectedText()

        # Re-insert as rendered markdown HTML
        block_fmt = self._streaming_block_fmt
        info = self._role_info(self._streaming_msg)
        rendered = self._render(self._streaming_msg)
        cursor.insertHtml(rendered)

        # Apply background and role to all new blocks
        end_block = cursor.block().blockNumber()
        for bn in range(start_block_num, end_block + 1):
            block = doc.findBlockByNumber(bn)
            if block.isValid():
                bc = QTextCursor(block)
                bc.setBlockFormat(block_fmt)
                self._block_roles[bn] = info

        self._streaming_rendered_len = len(self._streaming_msg.content)
        self._scroll_to_bottom()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_message(self, message: Message) -> None:
        self._messages.append(message)
        self._insert_rendered(message)
        self._scroll_to_bottom()

    def begin_streaming(self, message: Message) -> None:
        """Start streaming — re-renders on paragraph breaks."""
        self._streaming_msg = message
        self._streaming_block_fmt = self._make_block_fmt(message)
        info = self._role_info(message)
        self._messages.append(message)
        self._streaming_rendered_len = 0

        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)

        if self._is_empty:
            cursor.setBlockFormat(self._streaming_block_fmt)
            self._is_empty = False
        else:
            cursor.insertBlock(self._streaming_block_fmt)

        self._streaming_start_pos = cursor.position()
        self._block_roles[cursor.block().blockNumber()] = info
        self._scroll_to_bottom()

    def append_token(self, token: str) -> None:
        if not self._streaming_msg:
            return
        self._streaming_msg.content += token

        # Append plain text for immediate feedback
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)

        info = self._role_info(self._streaming_msg)
        parts = token.split("\n")
        for i, part in enumerate(parts):
            if i > 0:
                cursor.insertBlock(self._streaming_block_fmt)
                self._block_roles[cursor.block().blockNumber()] = info
            if part:
                cursor.insertText(part)

        self._scroll_to_bottom()

        # Re-render when a paragraph break appears in new content
        content = self._streaming_msg.content
        new_text = content[self._streaming_rendered_len:]
        if "\n\n" in new_text:
            self._rerender_streaming()

    def end_streaming(self) -> Message | None:
        """Finish streaming — final render."""
        if self._streaming_msg:
            msg = self._streaming_msg
            self._streaming_msg = None
            self._streaming_block_fmt = None
            self._streaming_rendered_len = 0
            self._rebuild()
            return msg
        return None

    def clear_messages(self) -> None:
        self.clear()
        self._messages.clear()
        self._block_roles.clear()
        self._streaming_msg = None
        self._streaming_block_fmt = None
        self._streaming_start_pos = 0
        self._streaming_rendered_len = 0
        self._is_empty = True

    def update_font_size(self, size: int) -> None:
        self._font_size = size
        self._apply_default_font()
        self._rebuild()

    def update_colors(self, color_user: str, color_claude: str, color_openai: str) -> None:
        self._color_user = color_user
        self._color_claude = color_claude
        self._color_openai = color_openai
        self._rebuild()

    # ------------------------------------------------------------------
    # Copy with //user, //claude (<model>), //gpt (<model>) prefixes
    # ------------------------------------------------------------------

    @staticmethod
    def _prefix_for(role_info: _RoleInfo) -> str:
        role, provider, model = role_info
        short = _short_model(model)
        if role == Role.USER:
            return "//user"
        if provider == Provider.CLAUDE:
            return f"//claude ({short})" if short else "//claude"
        if provider == Provider.OPENAI:
            return f"//gpt ({short})" if short else "//gpt"
        return "//assistant"

    def createMimeDataFromSelection(self) -> QMimeData:
        cursor = self.textCursor()
        if not cursor.hasSelection():
            return super().createMimeDataFromSelection()

        start = cursor.selectionStart()
        end = cursor.selectionEnd()

        doc = self.document()
        block = doc.findBlock(start)

        result_lines: list[str] = []
        prev_role_info: _RoleInfo | None = None

        while block.isValid() and block.position() < end:
            role_info = self._block_roles.get(block.blockNumber())

            # Insert prefix when speaker changes
            if role_info and role_info != prev_role_info:
                result_lines.append(self._prefix_for(role_info))
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
