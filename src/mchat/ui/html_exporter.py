# ------------------------------------------------------------------
# Component: HtmlExporter
# Responsibility: Pure, non-Qt serialization of a conversation to a
#                 standalone HTML document. Takes a list of Message
#                 objects plus presentation config (colour map, font
#                 size) and returns the HTML as a string. Intended for
#                 "Save as HTML..." and any other export use case;
#                 must not depend on QTextEdit/QTextDocument.
# Collaborators: markdown, config, models.message, ui.chat_export
# ------------------------------------------------------------------
from __future__ import annotations

import html as html_mod
from dataclasses import dataclass

import markdown as _md

from mchat.config import PROVIDER_META
from mchat.models.message import Message, Provider, Role
from mchat.ui.chat_export import short_model


@dataclass(frozen=True)
class ExportColors:
    """Per-participant background colours used for the exported HTML."""

    user: str
    claude: str
    openai: str
    gemini: str
    perplexity: str

    def color_for(self, message: Message) -> str:
        if message.role == Role.USER:
            return self.user
        if message.provider is None:
            return self.user
        return getattr(self, message.provider.value, self.user)


class HtmlExporter:
    """Converts a list of Message objects into a standalone HTML document.

    Has no Qt dependency — uses the same ``markdown`` library the widget
    uses for rendering, but never touches QTextEdit or QTextDocument.
    Constructed once per export so the internal Markdown converter can
    be reset cleanly between messages.
    """

    def __init__(self, colors: ExportColors, font_size: int = 14) -> None:
        self._colors = colors
        self._font_size = font_size
        self._md = _md.Markdown(
            extensions=["tables", "fenced_code", "sane_lists"]
        )

    def export(self, messages: list[Message]) -> str:
        """Return a standalone HTML document rendering ``messages``."""
        parts: list[str] = []
        for msg in messages:
            colour = self._colors.color_for(msg)
            content = self._render(msg)
            label = self._label_for(msg)
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

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _render(self, msg: Message) -> str:
        if msg.role == Role.ASSISTANT and msg.content:
            self._md.reset()
            return self._md.convert(msg.content)
        text = html_mod.escape(msg.content) if msg.content else ""
        return text.replace("\n", "<br>")

    @staticmethod
    def _label_for(msg: Message) -> str:
        if msg.role == Role.USER:
            return "You"
        if msg.provider:
            display = PROVIDER_META.get(msg.provider.value, {}).get(
                "display", msg.provider.value
            )
            return f"{display} ({short_model(msg.model)})" if msg.model else display
        return "Assistant"


def exporter_from_config(config) -> HtmlExporter:
    """Convenience: build an HtmlExporter using the colours and font
    size stored in the given Config. The returned instance is
    single-use (one ``.export()`` call per instance is recommended
    because the internal Markdown converter carries state)."""
    colors = ExportColors(
        user=config.get("color_user"),
        claude=config.get("color_claude"),
        openai=config.get("color_openai"),
        gemini=config.get("color_gemini"),
        perplexity=config.get("color_perplexity"),
    )
    font_size = int(config.get("font_size") or 14)
    return HtmlExporter(colors, font_size)
