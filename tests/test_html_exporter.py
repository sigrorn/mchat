# ------------------------------------------------------------------
# Component: test_html_exporter
# Responsibility: Tests for the standalone (non-Qt) HtmlExporter that
#                 replaced the temp-ChatWidget export hack.
# Collaborators: ui.html_exporter, models.message, config
# ------------------------------------------------------------------
from __future__ import annotations

import pytest

from mchat.config import Config
from mchat.models.message import Message, Provider, Role
from mchat.ui.html_exporter import ExportColors, HtmlExporter, exporter_from_config


@pytest.fixture
def exporter():
    colors = ExportColors(
        user="#d4d4d4",
        claude="#b0b0b0",
        openai="#e8e8e8",
        gemini="#c8d8e8",
        perplexity="#d8c8e8",
    )
    return HtmlExporter(colors, font_size=14)


class TestHtmlExporter:
    def test_empty_conversation_still_valid_html(self, exporter):
        html = exporter.export([])
        assert "<!DOCTYPE html>" in html
        assert "<body>" in html and "</body>" in html

    def test_user_message_appears_as_you(self, exporter):
        msgs = [Message(role=Role.USER, content="hello")]
        html = exporter.export(msgs)
        assert ">You<" in html
        assert "hello" in html

    def test_assistant_message_uses_provider_display_name(self, exporter):
        msgs = [
            Message(
                role=Role.ASSISTANT,
                content="hi",
                provider=Provider.CLAUDE,
                model="claude-sonnet-4-20250514",
            )
        ]
        html = exporter.export(msgs)
        assert "Claude" in html
        # Model gets shortened via short_model() — non-greedy, so
        # "claude-sonnet-4-20250514" becomes just "sonnet".
        assert "(sonnet)" in html
        assert "hi" in html

    def test_markdown_rendered_for_assistant(self, exporter):
        msgs = [
            Message(
                role=Role.ASSISTANT,
                content="**bold** and `code`",
                provider=Provider.CLAUDE,
            )
        ]
        html = exporter.export(msgs)
        assert "<strong>bold</strong>" in html
        assert "<code>code</code>" in html

    def test_user_content_is_html_escaped(self, exporter):
        msgs = [Message(role=Role.USER, content="<script>alert('x')</script>")]
        html = exporter.export(msgs)
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_background_color_per_provider(self, exporter):
        msgs = [
            Message(role=Role.USER, content="q"),
            Message(role=Role.ASSISTANT, content="a", provider=Provider.CLAUDE),
            Message(role=Role.ASSISTANT, content="b", provider=Provider.OPENAI),
        ]
        html = exporter.export(msgs)
        assert "#d4d4d4" in html  # user
        assert "#b0b0b0" in html  # claude
        assert "#e8e8e8" in html  # openai

    def test_font_size_reflected_in_css(self):
        exporter = HtmlExporter(
            ExportColors("#fff", "#fff", "#fff", "#fff", "#fff"),
            font_size=22,
        )
        html = exporter.export([Message(role=Role.USER, content="x")])
        assert "font-size: 22px" in html

    def test_color_for_unknown_provider_falls_back_to_user(self):
        colors = ExportColors("#aaa", "#bbb", "#ccc", "#ddd", "#eee")
        # An assistant message with no provider
        msg = Message(role=Role.ASSISTANT, content="?")
        assert colors.color_for(msg) == "#aaa"

    def test_exporter_from_config(self, tmp_path):
        cfg = Config(config_path=tmp_path / "cfg.json")
        cfg.set("color_user", "#111111")
        cfg.set("color_claude", "#222222")
        cfg.set("font_size", 18)
        cfg.save()
        exp = exporter_from_config(cfg)
        html = exp.export([Message(role=Role.USER, content="x")])
        assert "#111111" in html
        assert "font-size: 18px" in html
