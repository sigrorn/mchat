# ------------------------------------------------------------------
# Component: HtmlExporter
# Responsibility: Pure, non-Qt serialization of a conversation to a
#                 standalone HTML document. Takes a list of Message
#                 objects plus presentation config (colour map, font
#                 size) and returns the HTML as a string. Intended for
#                 "Save as HTML..." and any other export use case;
#                 must not depend on QTextEdit/QTextDocument.
# Collaborators: config, models.message, ui.chat_export  (external: markdown)
# ------------------------------------------------------------------
from __future__ import annotations

import base64 as _base64
import html as html_mod
import re
from dataclasses import dataclass

import markdown as _md

from mchat import dot_renderer, mermaid_renderer
from mchat.config import PROVIDER_META
from mchat.models.message import Message, Provider, Role
from mchat.models.persona import Persona
from mchat.ui.chat_export import short_model
from mchat.ui.dot_markdown_ext import DOT_SOURCE_MAP, DotExtension
from mchat.ui.mermaid_markdown_ext import MERMAID_SOURCE_MAP, MermaidExtension

# Match the mchat-graph://<hash>.png URL scheme the DOT markdown
# extension stashes in <img> tags.
_DOT_IMG_RE = re.compile(
    r'<img([^>]*?)src="mchat-graph://([0-9a-f]+)\.svg"([^>]*)/?>'
)
_MERMAID_IMG_RE = re.compile(
    r'<img([^>]*?)src="mchat-mermaid://([0-9a-f]+)\.png"([^>]*)/?>'
)


@dataclass(frozen=True)
class ExportColors:
    """Per-participant background colours used for the exported HTML."""

    user: str
    claude: str
    openai: str
    gemini: str
    perplexity: str
    mistral: str
    apertus: str

    def color_for(
        self,
        message: Message,
        personas_by_id: dict[str, Persona] | None = None,
    ) -> str:
        """Return the background colour for a message.

        #90: if the message has a persona_id matching a persona with
        ``color_override`` set, that override wins over the provider
        default — matching the in-app chat display behaviour (the
        legacy exporter was on a provider-only path and lost custom
        per-persona colours on export).
        """
        if message.role == Role.USER:
            return self.user
        if personas_by_id and message.persona_id is not None:
            persona = personas_by_id.get(message.persona_id)
            if persona is not None and persona.color_override:
                return persona.color_override
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
            extensions=[
                "tables", "fenced_code", "sane_lists",
                DotExtension(), MermaidExtension(),
            ]
        )
        # #146: bumped by _inline_dot_images / _inline_mermaid_images
        # every time a block can't be rendered. Consumed by export()
        # to decide whether to prepend a degradation banner.
        self._dot_render_failures: int = 0
        self._mermaid_render_failures: int = 0

    def export(
        self,
        messages: list[Message],
        personas: list[Persona] | None = None,
    ) -> str:
        """Return a standalone HTML document rendering ``messages``.

        ``personas`` is an optional list of Persona rows for this
        conversation (typically ``db.list_personas_including_deleted(conv_id)``
        so tombstoned personas still label their historical messages).
        When provided, any message whose ``persona_id`` matches a row
        uses that persona's ``name`` as its label. Messages without a
        persona_id, or whose persona_id doesn't match any row, fall
        back to the provider display name.
        """
        personas_by_id = {p.id: p for p in (personas or [])}
        # #146/#150: reset per-export so each export counts only its own
        # render failures.
        self._dot_render_failures = 0
        self._mermaid_render_failures = 0
        parts: list[str] = []
        for msg in messages:
            colour = self._colors.color_for(msg, personas_by_id)
            content = self._render(msg)
            label = self._label_for(msg, personas_by_id)
            parts.append(
                f'<div style="background-color:{colour}; padding:12px 16px; '
                f'margin:0; border-radius:0;">'
                f'<div style="font-size:0.85em; color:#444; font-weight:bold; '
                f'margin-bottom:4px;">{label}</div>'
                f"{content}"
                f"</div>"
            )

        # #146/#150: if any diagram blocks failed to render, prepend a
        # visible warning so the reader knows graphics are missing.
        banners: list[str] = []
        if self._dot_render_failures > 0:
            n = self._dot_render_failures
            banners.append(
                f'\u26a0 Graphviz not available at export time; {n} '
                f'DOT diagram{"s" if n != 1 else ""} shown as source only.'
            )
        if self._mermaid_render_failures > 0:
            n = self._mermaid_render_failures
            banners.append(
                f'\u26a0 Mermaid CLI (mmdc) not available at export time; {n} '
                f'mermaid diagram{"s" if n != 1 else ""} shown as source only.'
            )
        if banners:
            banner_html = (
                f'<div style="background-color:#fff3cd; color:#664d03; '
                f'padding:12px 16px; border-bottom:2px solid #ffc107; '
                f'font-weight:bold;">'
                + "<br>".join(banners)
                + '</div>'
            )
            parts.insert(0, banner_html)

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
            rendered = self._md.convert(msg.content)
            if "mchat-graph://" in rendered:
                rendered = self._inline_dot_images(rendered)
            if "mchat-mermaid://" in rendered:
                rendered = self._inline_mermaid_images(rendered)
            return rendered
        text = html_mod.escape(msg.content) if msg.content else ""
        return text.replace("\n", "<br>")

    # ------------------------------------------------------------------
    # DOT graphics inlining (#145)
    # ------------------------------------------------------------------

    def _inline_dot_images(self, html: str) -> str:
        """Rewrite every <img src="mchat-graph://<hash>.svg"> in
        ``html`` to a self-contained data:image/svg+xml;base64 URI so
        the exported file has no app-internal URLs left.

        SVG output means the browser can scale diagrams losslessly
        (#152). On render failure the <img> tag is dropped and the
        <details> source fallback carries the graph."""

        def repl(match: re.Match[str]) -> str:
            digest = match.group(2)
            source = DOT_SOURCE_MAP.get(digest)
            if source is None:
                self._dot_render_failures += 1
                return ""
            svg = dot_renderer.render_dot(source)
            if not svg:
                self._dot_render_failures += 1
                return ""
            b64 = _base64.b64encode(svg).decode("ascii")
            pre_attrs = match.group(1) or ""
            post_attrs = match.group(3) or ""
            return (
                f'<img{pre_attrs}src="data:image/svg+xml;base64,{b64}"'
                f'{post_attrs}/>'
            )

        return _DOT_IMG_RE.sub(repl, html)

    # ------------------------------------------------------------------
    # Mermaid graphics inlining (#150)
    # ------------------------------------------------------------------

    def _inline_mermaid_images(self, html: str) -> str:
        """Rewrite every <img src="mchat-mermaid://<hash>.png"> to a
        self-contained data:image/png;base64 URI.

        #153: mermaid stays PNG (not SVG) because mermaid's SVG uses
        <foreignObject> which many viewers can't handle inline."""

        def repl(match: re.Match[str]) -> str:
            digest = match.group(2)
            source = MERMAID_SOURCE_MAP.get(digest)
            if source is None:
                self._mermaid_render_failures += 1
                return ""
            png = mermaid_renderer.render_mermaid(source)
            if not png:
                self._mermaid_render_failures += 1
                return ""
            b64 = _base64.b64encode(png).decode("ascii")
            pre_attrs = match.group(1) or ""
            post_attrs = match.group(3) or ""
            return (
                f'<img{pre_attrs}src="data:image/png;base64,{b64}"'
                f'{post_attrs}/>'
            )

        return _MERMAID_IMG_RE.sub(repl, html)

    @staticmethod
    def _label_for(
        msg: Message, personas_by_id: dict[str, Persona] | None = None,
    ) -> str:
        if msg.role == Role.USER:
            return "You"
        # Persona name wins when persona_id is set and we have a
        # matching row (including tombstoned personas per D3).
        if msg.persona_id is not None and personas_by_id:
            p = personas_by_id.get(msg.persona_id)
            if p is not None:
                return f"{p.name} ({short_model(msg.model)})" if msg.model else p.name
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
        mistral=config.get("color_mistral"),
        apertus=config.get("color_apertus"),
    )
    font_size = int(config.get("font_size") or 14)
    return HtmlExporter(colors, font_size)
