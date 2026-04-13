# ------------------------------------------------------------------
# Component: ChatExportMixin
# Responsibility: Selection-copy with //speaker prefixes for the
#                 ChatWidget. HTML export lives in ui.html_exporter as
#                 a pure non-Qt module; this file only keeps the copy
#                 behaviour and the shared short_model helper.
# Collaborators: models.message  (external: PySide6)
# ------------------------------------------------------------------
from __future__ import annotations

import re

from PySide6.QtCore import QMimeData

from mchat.models.message import Provider, Role

_RoleInfo = tuple[Role, Provider | None, str | None]

_COPY_PREFIX = {
    Provider.CLAUDE: "claude",
    Provider.OPENAI: "gpt",
    Provider.GEMINI: "gemini",
    Provider.PERPLEXITY: "perplexity",
    Provider.MISTRAL: "mistral",
    Provider.APERTUS: "apertus",
}


def short_model(model: str | None) -> str:
    """Shorten a model id for the copy prefix.

    claude-sonnet-4-20250514 -> sonnet-4
    gpt-4.1-mini             -> 4.1-mini
    o3-mini                  -> o3-mini
    """
    if not model:
        return ""
    m = re.match(r"^claude-(.+?)(-\d[\d-]*)?$", model)
    if m:
        return m.group(1)
    if model.startswith("gpt-"):
        return model[4:]
    return model


def prefix_for(role_info: _RoleInfo) -> str:
    """Return the //speaker prefix line for a given (role, provider, model)."""
    role, provider, model = role_info
    short = short_model(model)
    if role == Role.USER:
        return "//user"
    tag = _COPY_PREFIX.get(provider, "assistant")
    return f"//{tag} ({short})" if short else f"//{tag}"


class ChatExportMixin:
    """Copy-with-//speaker-prefix behaviour for ChatWidget.

    Expects the host to provide ``_block_roles`` and the standard
    QTextEdit ``document``/``textCursor`` surface.
    """

    # Kept as a method so it can be accessed via ChatWidget._prefix_for
    # (preserves the old public-ish API) while the real implementation
    # lives at module level.
    @staticmethod
    def _prefix_for(role_info: _RoleInfo) -> str:
        return prefix_for(role_info)

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
        tables_done: set[int] = set()  # track processed tables by position

        while block.isValid() and block.position() < end:
            # Check if this block is inside a QTextTable
            from PySide6.QtGui import QTextCursor as _QTC
            bc = _QTC(block)
            table = bc.currentTable()
            if table:
                table_pos = table.firstCursorPosition().position()
                if table_pos not in tables_done:
                    tables_done.add(table_pos)
                    # Extract table content column-major (header + body per column)
                    for col in range(table.columns()):
                        for row in range(table.rows()):
                            cell = table.cellAt(row, col)
                            cell_cursor = cell.firstCursorPosition()
                            cell_end = cell.lastCursorPosition().position()
                            cell_block = cell_cursor.block()
                            while cell_block.isValid() and cell_block.position() <= cell_end:
                                text = cell_block.text().strip()
                                if text:
                                    result_lines.append(text)
                                cell_block = cell_block.next()
                # Skip all blocks inside this table
                table_last_pos = table.lastCursorPosition().position()
                while block.isValid() and block.position() <= table_last_pos:
                    block = block.next()
                continue

            role_info = self._block_roles.get(block.blockNumber())

            if role_info and role_info != prev_role_info:
                result_lines.append(prefix_for(role_info))
                prev_role_info = role_info

            block_start = block.position()
            block_end = block_start + block.length() - 1
            sel_start = max(start, block_start)
            sel_end = min(end, block_end)

            if sel_start <= sel_end:
                text = block.text()
                result_lines.append(text[sel_start - block_start : sel_end - block_start])

            block = block.next()

        mime = QMimeData()
        mime.setText("\n".join(result_lines))
        return mime
