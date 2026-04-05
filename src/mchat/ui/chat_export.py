# ------------------------------------------------------------------
# Component: ChatExportMixin
# Responsibility: HTML export and selection-copy with //speaker
#                 prefixes. Split out from ChatWidget so the export
#                 format and copy-with-prefix logic have a clear home
#                 distinct from document rendering.
# Collaborators: PySide6 (QTextEdit / QMimeData), config, models.message
# ------------------------------------------------------------------
from __future__ import annotations

import re

from PySide6.QtCore import QMimeData

from mchat.config import PROVIDER_META
from mchat.models.message import Provider, Role

_RoleInfo = tuple[Role, Provider | None, str | None]

_COPY_PREFIX = {
    Provider.CLAUDE: "claude",
    Provider.OPENAI: "gpt",
    Provider.GEMINI: "gemini",
    Provider.PERPLEXITY: "perplexity",
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
    """Export-to-HTML and copy-with-prefix behaviour for ChatWidget.

    Expects the host to provide ``_messages``, ``_block_roles``,
    ``_font_size``, ``_color_for()``, ``_render()``, and standard
    QTextEdit behaviour (``document``, ``textCursor``,
    ``createMimeDataFromSelection`` as a super method).
    """

    def export_html(self) -> str:
        """Return a standalone HTML document with all messages."""
        parts: list[str] = []
        for msg in self._messages:
            colour = self._color_for(msg)
            content = self._render(msg)

            if msg.role == Role.USER:
                label = "You"
            elif msg.provider:
                display = PROVIDER_META.get(msg.provider.value, {}).get(
                    "display", msg.provider.value
                )
                label = (
                    f"{display} ({short_model(msg.model)})" if msg.model else display
                )
            else:
                label = "Assistant"

            parts.append(
                f'<div style="background-color:{colour}; padding:12px 16px; '
                f'margin:0; border-radius:0;">'
                f'<div style="font-size:0.85em; color:#444; font-weight:bold; '
                f'margin-bottom:4px;">{label}</div>'
                f"{content}"
                f"</div>"
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

        while block.isValid() and block.position() < end:
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
