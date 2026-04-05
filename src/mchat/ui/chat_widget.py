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
from PySide6.QtGui import QColor, QTextBlockFormat, QTextCharFormat, QTextCursor, QTextDocument, QTextLength
from PySide6.QtWidgets import (
    QTextEdit,
    QWidget,
)

from mchat.models.message import Message, Provider, Role

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
        # Set of message indices that are excluded from provider context
        # (e.g. before //limit). Rendered with paler colours.
        self._excluded_indices: set[int] = set()
        self._block_roles: dict[int, _RoleInfo] = {}
        self._is_empty = True
        self._md = markdown.Markdown(
            extensions=["tables", "fenced_code", "sane_lists"]
        )
        self._build_ui()

    def _build_ui(self) -> None:
        self.setStyleSheet(
            "QTextEdit { border: none; }"
        )
        # Set viewport background so block format backgrounds are the
        # primary colour source — no white bleeding through
        pal = self.palette()
        pal.setColor(pal.ColorRole.Base, QColor("#f5f5f5"))
        self.setPalette(pal)
        self.document().setDocumentMargin(16)
        self.document().setDefaultStyleSheet(_DOC_CSS)
        self._apply_default_font()

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
    # Colour helpers
    # ------------------------------------------------------------------

    def _color_for(self, message: Message) -> str:
        if message.role == Role.USER:
            return self._colors["user"]
        if message.provider:
            return self._colors.get(message.provider.value, self._colors["user"])
        return self._colors["user"]

    @staticmethod
    def _blend_toward_white(hex_color: str, amount: float = 0.6) -> str:
        """Blend a hex colour toward white by the given amount (0..1)."""
        c = QColor(hex_color)
        r = int(c.red() + (255 - c.red()) * amount)
        g = int(c.green() + (255 - c.green()) * amount)
        b = int(c.blue() + (255 - c.blue()) * amount)
        return f"#{r:02x}{g:02x}{b:02x}"

    @staticmethod
    def _darken(hex_color: str, amount: float = 0.2) -> str:
        """Darken a hex colour by the given amount (0..1)."""
        c = QColor(hex_color)
        r = int(c.red() * (1 - amount))
        g = int(c.green() * (1 - amount))
        b = int(c.blue() * (1 - amount))
        return f"#{r:02x}{g:02x}{b:02x}"

    def _shade(self, hex_color: str) -> str:
        """Apply the configured shading to a colour."""
        amount = max(0.0, min(1.0, self._exclude_shade_amount / 100.0))
        if self._exclude_shade_mode == "lighten":
            return self._blend_toward_white(hex_color, amount)
        return self._darken(hex_color, amount)

    def _effective_color_for(self, message: Message, index: int) -> str:
        """Return the (possibly shaded) colour for a message at this index."""
        base = self._color_for(message)
        if index in self._excluded_indices:
            return self._shade(base)
        return base

    def _effective_text_color(self, index: int) -> str:
        """Return text colour for a message at this index."""
        # Use the same dark text — background darkening provides the visual cue
        return "#1a1a1a"

    def set_excluded_indices(self, indices: set[int]) -> None:
        """Set which message indices are excluded from provider context.

        Called before rendering messages; messages at these indices are
        shaded to indicate they won't be sent to providers.
        """
        self._excluded_indices = set(indices)

    def _make_block_fmt_for_index(self, message: Message, index: int) -> QTextBlockFormat:
        fmt = QTextBlockFormat()
        fmt.setBackground(QColor(self._effective_color_for(message, index)))
        return fmt

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

    def _apply_bg_to_range(
        self, start_block: int, end_block: int,
        block_fmt: QTextBlockFormat, info: _RoleInfo, color: QColor,
        text_color: str = "#1a1a1a",
    ) -> None:
        """Apply background colour and role to all blocks and table cells in range."""
        doc = self.document()
        tables_seen: set[int] = set()  # keyed by document position of table start

        # Char format to set background and text colour
        match_fmt = QTextCharFormat()
        match_fmt.setBackground(color)
        match_fmt.setForeground(QColor(text_color))

        for bn in range(start_block, end_block + 1):
            block = doc.findBlockByNumber(bn)
            if not block.isValid():
                continue
            bc = QTextCursor(block)
            bc.setBlockFormat(block_fmt)
            self._block_roles[bn] = info

            # Set char-level backgrounds to match block background
            bc.movePosition(QTextCursor.MoveOperation.StartOfBlock)
            bc.movePosition(QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor)
            bc.mergeCharFormat(match_fmt)

            # If this block is inside a table, colour all cells
            table = bc.currentTable()
            if table:
                table_pos = table.firstCursorPosition().position()
                if table_pos not in tables_seen:
                    tables_seen.add(table_pos)

                    # Set table frame: no margin/spacing, full width, matching bg
                    tf = table.format()
                    tf.setMargin(0)
                    tf.setCellSpacing(0)
                    tf.setBackground(color)
                    tf.setWidth(QTextLength(QTextLength.Type.PercentageLength, 100))
                    table.setFormat(tf)

                    for row in range(table.rows()):
                        for col in range(table.columns()):
                            cell = table.cellAt(row, col)
                            fmt = cell.format()
                            fmt.setBackground(color)
                            cell.setFormat(fmt)

    def _insert_rendered(self, message: Message) -> None:
        """Insert a message as rendered HTML with background colour on every block."""
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)

        # Index of this message (it's the next one to be appended)
        msg_index = len(self._messages) - 1  # already appended by caller
        if msg_index < 0:
            msg_index = 0

        block_fmt = self._make_block_fmt_for_index(message, msg_index)
        info = self._role_info(message)
        color = QColor(self._effective_color_for(message, msg_index))
        text_color = self._effective_text_color(msg_index)

        # Start a new block (or reuse the initial empty one)
        if self._is_empty:
            cursor.setBlockFormat(block_fmt)
            self._is_empty = False
        else:
            cursor.insertBlock(block_fmt)

        # Record document position for scroll-to-message
        self._message_positions.append(cursor.position())
        start_block = cursor.block().blockNumber()

        # Prefix user messages with message number
        if message.role == Role.USER:
            msg_num = len(self._messages)  # 1-based (appended before _insert_rendered)
            char_fmt = cursor.charFormat()
            char_fmt.setForeground(QColor("#888"))
            cursor.insertText(f"{msg_num} — ", char_fmt)
            # Reset to default text colour for the actual content
            char_fmt.setForeground(QColor(text_color))
            cursor.setCharFormat(char_fmt)

        # Insert HTML content
        rendered = self._render(message)
        cursor.insertHtml(rendered)

        end_block = cursor.block().blockNumber()
        self._apply_bg_to_range(start_block, end_block, block_fmt, info, color, text_color)

    def _rebuild(self) -> None:
        """Re-render all messages with markdown formatting.

        If a rebuild callback is set (by MainWindow), delegate to it so
        multi-provider groups are rendered with the correct layout mode.
        """
        if self._rebuild_callback is not None:
            self._rebuild_callback()
            return
        saved = list(self._messages)
        self.clear()
        self._messages.clear()
        self._message_positions.clear()
        self._block_roles.clear()
        self._is_empty = True
        self.setUpdatesEnabled(False)
        try:
            for msg in saved:
                self._messages.append(msg)
                self._insert_rendered(msg)
        finally:
            self.setUpdatesEnabled(True)
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
            elif msg.provider:
                from mchat.config import PROVIDER_META
                display = PROVIDER_META.get(msg.provider.value, {}).get("display", msg.provider.value)
                label = f"{display} ({_short_model(msg.model)})" if msg.model else display
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
    # Public API
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
                    f'&nbsp;&nbsp;&nbsp;&nbsp;'
                    f'<a href="mchat-mark:{msg_count}" '
                    f'style="color: #4a90d9; text-decoration: underline;">'
                    f'{label}</a>'
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

    def _insert_column_table(self, table_html: str, provider_colors: list[str]) -> None:
        """Insert a pre-built HTML table for column-mode multi-provider responses."""
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)

        fmt = QTextBlockFormat()
        fmt.setBackground(QColor("#f5f5f5"))

        if self._is_empty:
            cursor.setBlockFormat(fmt)
            self._is_empty = False
        else:
            cursor.insertBlock(fmt)

        start_block = cursor.block().blockNumber()
        cursor.insertHtml(table_html)
        end_block = cursor.block().blockNumber()

        # Apply backgrounds programmatically — Qt ignores most inline CSS.
        # Process tables separately, then skip their blocks to avoid
        # overwriting cell colours with the neutral background.
        doc = self.document()
        table_block_ranges: list[tuple[int, int]] = []  # (first_bn, last_bn) per table

        # First pass: find and process all tables
        bn = start_block
        while bn <= end_block:
            block = doc.findBlockByNumber(bn)
            if not block.isValid():
                bn += 1
                continue
            bc = QTextCursor(block)
            table = bc.currentTable()
            if table:
                # Record the block range for this table
                table_first = doc.findBlock(table.firstCursorPosition().position()).blockNumber()
                table_last = doc.findBlock(table.lastCursorPosition().position()).blockNumber()
                table_block_ranges.append((table_first, table_last))

                # Table frame: no gaps, full width, equal columns
                tf = table.format()
                tf.setMargin(0)
                tf.setCellSpacing(0)
                tf.setCellPadding(8)
                tf.setWidth(QTextLength(QTextLength.Type.PercentageLength, 100))
                tf.setBackground(QColor("#f5f5f5"))
                num_cols = table.columns()
                if num_cols > 0:
                    col_pct = 100.0 / num_cols
                    tf.setColumnWidthConstraints(
                        [QTextLength(QTextLength.Type.PercentageLength, col_pct)] * num_cols
                    )
                table.setFormat(tf)

                # Per-cell background: each column gets its provider colour
                for row in range(table.rows()):
                    for col in range(table.columns()):
                        cell = table.cellAt(row, col)
                        cell_fmt = cell.format()
                        cell_color = QColor(provider_colors[col]) if col < len(provider_colors) else QColor("#f5f5f5")
                        cell_fmt.setBackground(cell_color)
                        cell.setFormat(cell_fmt)

                        # Set block format for every paragraph inside the cell
                        block_bg = QTextBlockFormat()
                        block_bg.setBackground(cell_color)
                        cell_start = cell.firstCursorPosition().position()
                        cell_end = cell.lastCursorPosition().position()
                        blk = doc.findBlock(cell_start)
                        while blk.isValid() and blk.position() <= cell_end:
                            blk_cursor = QTextCursor(blk)
                            blk_cursor.setBlockFormat(block_bg)
                            blk = blk.next()

                        # Set char backgrounds inside cell to match
                        char_bg = QTextCharFormat()
                        char_bg.setBackground(cell_color)
                        cell_cursor = cell.firstCursorPosition()
                        cell_cursor.setPosition(
                            cell_end,
                            QTextCursor.MoveMode.KeepAnchor,
                        )
                        cell_cursor.mergeCharFormat(char_bg)

                # Skip past the table
                bn = table_last + 1
            else:
                # Non-table block: apply neutral background
                bc.setBlockFormat(fmt)
                bn += 1

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

    # ------------------------------------------------------------------
    # Copy with //user, //claude (<model>), //gpt (<model>) prefixes
    # ------------------------------------------------------------------

    _COPY_PREFIX = {
        Provider.CLAUDE: "claude",
        Provider.OPENAI: "gpt",
        Provider.GEMINI: "gemini",
        Provider.PERPLEXITY: "perplexity",
    }

    @staticmethod
    def _prefix_for(role_info: _RoleInfo) -> str:
        role, provider, model = role_info
        short = _short_model(model)
        if role == Role.USER:
            return "//user"
        tag = ChatWidget._COPY_PREFIX.get(provider, "assistant")
        return f"//{tag} ({short})" if short else f"//{tag}"

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
