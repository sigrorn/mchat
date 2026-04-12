# ------------------------------------------------------------------
# Component: ChatDocumentMixin
# Responsibility: Low-level QTextDocument mutation for ChatWidget —
#                 colour policy, markdown rendering, per-message block
#                 insertion, column-table painting, and full rebuild.
#                 Isolated from ChatWidget's widget shell so the most
#                 bug-prone code in the UI layer has a clear home.
# Collaborators: PySide6 (QTextEdit/QTextDocument), models.message
# ------------------------------------------------------------------
from __future__ import annotations

import html as html_mod
import re

from PySide6.QtCore import QUrl
from PySide6.QtGui import (
    QColor,
    QImage,
    QPainter,
    QTextBlockFormat,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QTextLength,
)
from PySide6.QtSvg import QSvgRenderer

from mchat import dot_renderer, mermaid_renderer
from mchat.models.message import Message, Provider, Role
from mchat.ui.dot_markdown_ext import DOT_SOURCE_MAP
from mchat.ui.mermaid_markdown_ext import MERMAID_SOURCE_MAP

# Match the mchat-graph://<hash>.png URL scheme we stash in DOT
# <img> tags. The hash is a 64-char hex sha256; we accept the full
# hex alphabet here for defensive pattern matching.
_DOT_URL_RE = re.compile(r'mchat-graph://([0-9a-f]+)\.svg')
_MERMAID_URL_RE = re.compile(r'mchat-mermaid://([0-9a-f]+)\.png')

# Maximum rasterization width for SVG → QImage conversion.
# Large enough for legible text in complex diagrams, small enough
# to avoid excessive memory use.
_SVG_RASTER_MAX_WIDTH = 1600


def _svg_to_qimage(svg_bytes: bytes) -> QImage | None:
    """Rasterize SVG bytes to a QImage via QSvgRenderer.

    Scales the SVG proportionally to _SVG_RASTER_MAX_WIDTH pixels wide
    (or its natural size if smaller). Returns None on any failure."""
    renderer = QSvgRenderer(svg_bytes)
    if not renderer.isValid():
        return None
    size = renderer.defaultSize()
    if size.width() <= 0 or size.height() <= 0:
        return None
    if size.width() > _SVG_RASTER_MAX_WIDTH:
        scale = _SVG_RASTER_MAX_WIDTH / size.width()
        size = size * scale
    img = QImage(size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(0)
    painter = QPainter(img)
    renderer.render(painter)
    painter.end()
    return img


# Role info stored per text-block: (role, provider, model)
_RoleInfo = tuple[Role, Provider | None, str | None]


class ChatDocumentMixin:
    """QTextDocument mutation methods for ChatWidget.

    Expects the host class to provide the following state (all set up
    in ChatWidget.__init__): ``_messages``, ``_message_positions``,
    ``_block_roles``, ``_excluded_indices``, ``_is_empty``, ``_colors``,
    ``_exclude_shade_mode``, ``_exclude_shade_amount``, ``_md``, and
    ``_rebuild_callback``. The mixin also uses standard QTextEdit
    methods (``document``, ``textCursor``, ``clear``, ``setUpdatesEnabled``,
    and ``_scroll_to_bottom`` from the host class).
    """

    # ------------------------------------------------------------------
    # Colour helpers
    # ------------------------------------------------------------------

    def _color_for(self, message: Message) -> str:
        # Persona colour resolver (optional — wired by MainWindow in
        # Stage 3A.2+). Returns None when persona-aware resolution
        # doesn't apply; we then fall through to the legacy lookup.
        resolver = getattr(self, "_persona_color_resolver", None)
        if resolver is not None:
            override = resolver.color_for_message(message)
            if override is not None:
                return override
        if message.role == Role.USER:
            return self._colors["user"]
        if message.provider:
            return self._colors.get(message.provider.value, self._colors["user"])
        return self._colors["user"]

    @staticmethod
    def _blend_toward_white(hex_color: str, amount: float = 0.6) -> str:
        c = QColor(hex_color)
        r = int(c.red() + (255 - c.red()) * amount)
        g = int(c.green() + (255 - c.green()) * amount)
        b = int(c.blue() + (255 - c.blue()) * amount)
        return f"#{r:02x}{g:02x}{b:02x}"

    @staticmethod
    def _darken(hex_color: str, amount: float = 0.2) -> str:
        c = QColor(hex_color)
        r = int(c.red() * (1 - amount))
        g = int(c.green() * (1 - amount))
        b = int(c.blue() * (1 - amount))
        return f"#{r:02x}{g:02x}{b:02x}"

    def _shade(self, hex_color: str) -> str:
        amount = max(0.0, min(1.0, self._exclude_shade_amount / 100.0))
        if self._exclude_shade_mode == "lighten":
            return self._blend_toward_white(hex_color, amount)
        return self._darken(hex_color, amount)

    def _effective_color_for(self, message: Message, index: int) -> str:
        base = self._color_for(message)
        if index in self._excluded_indices:
            return self._shade(base)
        return base

    def _effective_text_color(self, index: int) -> str:
        return "#1a1a1a"

    def set_excluded_indices(self, indices: set[int]) -> None:
        """Set which message indices are excluded from provider context."""
        self._excluded_indices = set(indices)

    def apply_excluded_indices(self, new_indices: set[int]) -> None:
        """Update excluded-indices shading in place without re-parsing
        markdown or re-inserting messages (#133).

        For each message whose exclusion state changed (symmetric diff
        of old vs new set), reapply the background colour to its stored
        block range. Column groups share one range across all their
        members: their effective exclusion is ``any(idx in new_set for
        idx in members)``, and all cells are re-shaded together using
        the base colours stored on insert.
        """
        old = set(self._excluded_indices)
        new = set(new_indices)
        changed = old.symmetric_difference(new)
        if not changed:
            self._excluded_indices = new
            return

        # Write the new state first so _effective_color_for and
        # _make_block_fmt_for_index pick it up during reapplication.
        self._excluded_indices = new

        # Collect single-message updates and column-group updates.
        # For column groups: pick the group once per first_msg_idx.
        handled_group_starts: set[int] = set()
        single_indices: set[int] = set()

        for idx in changed:
            # Is this index a member of a column group?
            group_first: int | None = None
            for first, (_s, _e, _bc, members) in self._column_group_info.items():
                if idx in members:
                    group_first = first
                    break
            if group_first is not None:
                handled_group_starts.add(group_first)
            else:
                single_indices.add(idx)

        # --- Apply single-message updates ---
        for idx in single_indices:
            if idx < 0 or idx >= len(self._message_block_starts):
                continue
            msg = self._messages[idx]
            start_block = self._message_block_starts[idx]
            end_block = self._message_block_ends[idx]
            block_fmt = self._make_block_fmt_for_index(msg, idx)
            info = self._role_info(msg)
            color = QColor(self._effective_color_for(msg, idx))
            text_color = self._effective_text_color(idx)
            self._apply_bg_to_range(
                start_block, end_block, block_fmt, info, color, text_color,
            )

        # --- Apply column-group updates ---
        for first in handled_group_starts:
            self._reshade_column_group(first)

    def _reshade_column_group(self, first_msg_idx: int) -> None:
        """Reapply per-cell backgrounds for a column group based on the
        current exclusion state. Used by apply_excluded_indices (#133)
        when the group's exclusion flips without content change.
        """
        info = self._column_group_info.get(first_msg_idx)
        if info is None:
            return
        start_block, end_block, base_colors, members = info
        # The group is excluded if any member is in the excluded set.
        excluded = any(idx in self._excluded_indices for idx in members)
        effective_colors = [
            self._shade(bc) if excluded else bc for bc in base_colors
        ]

        doc = self.document()
        # Walk the range looking for the table.
        bn = start_block
        while bn <= end_block:
            block = doc.findBlockByNumber(bn)
            if not block.isValid():
                bn += 1
                continue
            bc = QTextCursor(block)
            table = bc.currentTable()
            if table:
                table_last = doc.findBlock(
                    table.lastCursorPosition().position()
                ).blockNumber()
                for row in range(table.rows()):
                    for col in range(table.columns()):
                        cell = table.cellAt(row, col)
                        cell_color = (
                            QColor(effective_colors[col])
                            if col < len(effective_colors)
                            else QColor("#f5f5f5")
                        )
                        cell_fmt = cell.format()
                        cell_fmt.setBackground(cell_color)
                        cell.setFormat(cell_fmt)

                        block_bg = QTextBlockFormat()
                        block_bg.setBackground(cell_color)
                        cell_start = cell.firstCursorPosition().position()
                        cell_end = cell.lastCursorPosition().position()
                        blk = doc.findBlock(cell_start)
                        while blk.isValid() and blk.position() <= cell_end:
                            blk_cursor = QTextCursor(blk)
                            blk_cursor.setBlockFormat(block_bg)
                            blk = blk.next()

                        char_bg = QTextCharFormat()
                        char_bg.setBackground(cell_color)
                        cell_cursor = cell.firstCursorPosition()
                        cell_cursor.setPosition(
                            cell_end,
                            QTextCursor.MoveMode.KeepAnchor,
                        )
                        cell_cursor.mergeCharFormat(char_bg)
                bn = table_last + 1
            else:
                bn += 1

    def _make_block_fmt_for_index(
        self, message: Message, index: int
    ) -> QTextBlockFormat:
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
    # DOT graphics resource wiring (#144)
    # ------------------------------------------------------------------

    def _wire_dot_resources(self, html: str) -> None:
        """Scan rendered HTML for mchat-graph://<hash>.svg URLs and
        pre-register matching QImage resources on the document, so
        Qt's layout engine finds them when it lays out the <img> tag.

        #152: renderers now output SVG. We rasterize via QSvgRenderer
        at a high resolution so text stays legible in the app.

        Renders are served from dot_renderer's two-tier cache, so
        this is cheap on re-insertion of the same chat. When
        ``render_dot`` returns None (graphviz missing, bad DOT, etc.)
        no resource is added — the <details> source fallback
        emitted by dot_markdown_ext stays visible as a graceful
        degradation path."""
        if "mchat-graph://" not in html:
            return
        doc = self.document()
        for match in _DOT_URL_RE.finditer(html):
            digest = match.group(1)
            url = match.group(0)
            source = DOT_SOURCE_MAP.get(digest)
            if source is None:
                continue
            svg_bytes = dot_renderer.render_dot(source)
            if not svg_bytes:
                continue
            img = _svg_to_qimage(svg_bytes)
            if img is None or img.isNull():
                continue
            doc.addResource(
                QTextDocument.ResourceType.ImageResource,
                QUrl(url),
                img,
            )

    # ------------------------------------------------------------------
    # Mermaid graphics resource wiring (#150)
    # ------------------------------------------------------------------

    def _wire_mermaid_resources(self, html: str) -> None:
        """Scan rendered HTML for mchat-mermaid://<hash>.png URLs and
        pre-register matching QImage resources on the document.

        #153: mermaid renders to PNG (not SVG) because Qt's
        QSvgRenderer can't handle <foreignObject> text nodes that
        mermaid uses. DOT stays SVG since graphviz uses native <text>."""
        if "mchat-mermaid://" not in html:
            return
        doc = self.document()
        for match in _MERMAID_URL_RE.finditer(html):
            digest = match.group(1)
            url = match.group(0)
            source = MERMAID_SOURCE_MAP.get(digest)
            if source is None:
                continue
            png = mermaid_renderer.render_mermaid(source)
            if not png:
                continue
            img = QImage.fromData(png, "PNG")
            if img.isNull():
                continue
            doc.addResource(
                QTextDocument.ResourceType.ImageResource,
                QUrl(url),
                img,
            )

    # ------------------------------------------------------------------
    # Background application
    # ------------------------------------------------------------------

    def _apply_bg_to_range(
        self,
        start_block: int,
        end_block: int,
        block_fmt: QTextBlockFormat,
        info: _RoleInfo,
        color: QColor,
        text_color: str = "#1a1a1a",
    ) -> None:
        """Apply background colour and role to all blocks and table cells in range."""
        doc = self.document()
        tables_seen: set[int] = set()

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

            bc.movePosition(QTextCursor.MoveOperation.StartOfBlock)
            bc.movePosition(
                QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor
            )
            bc.mergeCharFormat(match_fmt)

            table = bc.currentTable()
            if table:
                table_pos = table.firstCursorPosition().position()
                if table_pos not in tables_seen:
                    tables_seen.add(table_pos)
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

    # ------------------------------------------------------------------
    # Insert a single rendered message
    # ------------------------------------------------------------------

    def _insert_rendered(self, message: Message) -> None:
        """Insert a message as rendered HTML with background colour on every block."""
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)

        msg_index = len(self._messages) - 1  # already appended by caller
        if msg_index < 0:
            msg_index = 0

        block_fmt = self._make_block_fmt_for_index(message, msg_index)
        info = self._role_info(message)
        color = QColor(self._effective_color_for(message, msg_index))
        text_color = self._effective_text_color(msg_index)

        if self._is_empty:
            cursor.setBlockFormat(block_fmt)
            self._is_empty = False
        else:
            cursor.insertBlock(block_fmt)

        self._message_positions.append(cursor.position())
        start_block = cursor.block().blockNumber()

        if message.role == Role.USER:
            msg_num = len(self._messages)  # 1-based (appended before _insert_rendered)
            char_fmt = cursor.charFormat()
            char_fmt.setForeground(QColor("#888"))
            cursor.insertText(f"{msg_num} — ", char_fmt)
            char_fmt.setForeground(QColor(text_color))
            cursor.setCharFormat(char_fmt)

        rendered = self._render(message)
        self._wire_dot_resources(rendered)
        self._wire_mermaid_resources(rendered)
        cursor.insertHtml(rendered)

        end_block = cursor.block().blockNumber()
        self._apply_bg_to_range(start_block, end_block, block_fmt, info, color, text_color)

        # #133: track this message's block range so partial-update
        # paths (apply_excluded_indices) can locate it without a
        # full re-render.
        self._message_block_starts.append(start_block)
        self._message_block_ends.append(end_block)

    # ------------------------------------------------------------------
    # Column table (multi-provider response grid)
    # ------------------------------------------------------------------

    def _insert_column_table(
        self,
        table_html: str,
        provider_colors: list[str],
        group_size: int | None = None,
        base_colors: list[str] | None = None,
    ) -> None:
        """Insert a pre-built HTML table for column-mode multi-provider responses.

        ``group_size`` tells the widget how many messages this table
        represents. ``base_colors`` are the unshaded originals per
        column — stored so ``apply_excluded_indices`` (#133) can
        reapply shaded vs unshaded backgrounds on exclusion flip
        without re-parsing markdown.
        """
        if group_size is None:
            group_size = len(provider_colors)
        if base_colors is None:
            base_colors = list(provider_colors)

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
        self._wire_dot_resources(table_html)
        self._wire_mermaid_resources(table_html)
        cursor.insertHtml(table_html)
        end_block = cursor.block().blockNumber()

        doc = self.document()

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
                table_first = doc.findBlock(
                    table.firstCursorPosition().position()
                ).blockNumber()
                table_last = doc.findBlock(
                    table.lastCursorPosition().position()
                ).blockNumber()

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
                        [QTextLength(QTextLength.Type.PercentageLength, col_pct)]
                        * num_cols
                    )
                table.setFormat(tf)

                for row in range(table.rows()):
                    for col in range(table.columns()):
                        cell = table.cellAt(row, col)
                        cell_fmt = cell.format()
                        cell_color = (
                            QColor(provider_colors[col])
                            if col < len(provider_colors)
                            else QColor("#f5f5f5")
                        )
                        cell_fmt.setBackground(cell_color)
                        cell.setFormat(cell_fmt)

                        block_bg = QTextBlockFormat()
                        block_bg.setBackground(cell_color)
                        cell_start = cell.firstCursorPosition().position()
                        cell_end = cell.lastCursorPosition().position()
                        blk = doc.findBlock(cell_start)
                        while blk.isValid() and blk.position() <= cell_end:
                            blk_cursor = QTextCursor(blk)
                            blk_cursor.setBlockFormat(block_bg)
                            blk = blk.next()

                        char_bg = QTextCharFormat()
                        char_bg.setBackground(cell_color)
                        cell_cursor = cell.firstCursorPosition()
                        cell_cursor.setPosition(
                            cell_end,
                            QTextCursor.MoveMode.KeepAnchor,
                        )
                        cell_cursor.mergeCharFormat(char_bg)

                bn = table_last + 1
            else:
                bc.setBlockFormat(fmt)
                bn += 1

        # #133: record the shared block range for all group members.
        # The group's message indices are the last ``group_size`` entries
        # of self._messages (the caller appends them before calling here).
        first_msg_idx = len(self._messages) - group_size
        if first_msg_idx < 0:
            first_msg_idx = 0
        member_indices = list(range(first_msg_idx, len(self._messages)))
        for _ in member_indices:
            self._message_block_starts.append(start_block)
            self._message_block_ends.append(end_block)
        self._column_group_info[first_msg_idx] = (
            start_block, end_block, list(base_colors), member_indices,
        )

        self._scroll_to_bottom()

    # ------------------------------------------------------------------
    # Full rebuild
    # ------------------------------------------------------------------

    def _rebuild(self) -> None:
        """Re-render all messages.

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
        # #133: per-message block ranges must also reset on rebuild
        self._message_block_starts.clear()
        self._message_block_ends.clear()
        self._column_group_info.clear()
        self._is_empty = True
        self.setUpdatesEnabled(False)
        try:
            for msg in saved:
                self._messages.append(msg)
                self._insert_rendered(msg)
        finally:
            self.setUpdatesEnabled(True)
        self._scroll_to_bottom()
