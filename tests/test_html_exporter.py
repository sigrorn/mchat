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
        mistral="#ffe0c8",
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
            ExportColors("#fff", "#fff", "#fff", "#fff", "#fff", "#fff"),
            font_size=22,
        )
        html = exporter.export([Message(role=Role.USER, content="x")])
        assert "font-size: 22px" in html

    def test_color_for_unknown_provider_falls_back_to_user(self):
        colors = ExportColors("#aaa", "#bbb", "#ccc", "#ddd", "#eee", "#fff")
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


class TestMistralExportColor:
    """#108 — Mistral messages should use color_mistral, not user colour."""

    def test_mistral_color_in_export(self):
        colors = ExportColors(
            user="#d4d4d4",
            claude="#b0b0b0",
            openai="#e8e8e8",
            gemini="#c8d8e8",
            perplexity="#d8c8e8",
            mistral="#ffe0c8",
        )
        msg = Message(role=Role.ASSISTANT, content="hi", provider=Provider.MISTRAL)
        assert colors.color_for(msg) == "#ffe0c8"

    def test_exporter_from_config_includes_mistral(self, tmp_path):
        cfg = Config(config_path=tmp_path / "cfg.json")
        cfg.set("color_mistral", "#ffe0c8")
        cfg.save()
        exp = exporter_from_config(cfg)
        assert exp._colors.mistral == "#ffe0c8"


class TestHtmlExporterPersonas:
    """Stage 1.3 — export labels reflect persona name when persona_id
    is set, falling back to the provider display name for legacy
    messages."""

    def _make_persona(self, **overrides):
        from mchat.models.persona import Persona, generate_persona_id
        fields = dict(
            conversation_id=1,
            id=generate_persona_id(),
            provider=Provider.CLAUDE,
            name="Partner",
            name_slug="partner",
        )
        fields.update(overrides)
        return Persona(**fields)

    def test_persona_label_used_when_persona_id_matches(self, exporter):
        p = self._make_persona(name="Evaluator", name_slug="evaluator")
        msgs = [
            Message(
                role=Role.ASSISTANT,
                content="critique",
                provider=Provider.CLAUDE,
                persona_id=p.id,
            ),
        ]
        html = exporter.export(msgs, personas=[p])
        assert "Evaluator" in html
        # The provider display name should NOT be the chosen label
        # (though "Claude" may still appear in the CSS/colour comments)
        assert ">Evaluator<" in html

    def test_legacy_message_without_persona_id_uses_provider_label(
        self, exporter
    ):
        msgs = [
            Message(
                role=Role.ASSISTANT,
                content="hi",
                provider=Provider.CLAUDE,
                # persona_id defaults to None
            ),
        ]
        html = exporter.export(msgs)
        assert "Claude" in html

    def test_persona_argument_optional_and_defaults_to_empty(self, exporter):
        """export() must still work without a personas kwarg — callers
        that haven't been updated yet keep working."""
        msgs = [Message(role=Role.USER, content="hi")]
        html = exporter.export(msgs)  # no personas kwarg
        assert ">You<" in html

    def test_tombstoned_persona_still_labels_message(self, exporter):
        """The exporter should accept tombstoned personas in its
        personas list (the db helper list_personas_including_deleted
        returns them) and use their names for historical labels."""
        from datetime import datetime, timezone
        p = self._make_persona(
            name="Archived",
            name_slug="archived",
            deleted_at=datetime.now(timezone.utc),
        )
        msgs = [
            Message(
                role=Role.ASSISTANT,
                content="old reply",
                provider=Provider.CLAUDE,
                persona_id=p.id,
            ),
        ]
        html = exporter.export(msgs, personas=[p])
        assert "Archived" in html

    def test_persona_id_with_no_matching_row_falls_back_to_provider(
        self, exporter
    ):
        """Defensive: if a message has a persona_id but no matching
        persona was passed in (shouldn't happen with the standard
        db.list_personas_including_deleted call, but be robust), fall
        back to the provider display name."""
        msgs = [
            Message(
                role=Role.ASSISTANT,
                content="orphan",
                provider=Provider.CLAUDE,
                persona_id="p_nonexistent",
            ),
        ]
        html = exporter.export(msgs, personas=[])
        assert "Claude" in html


class TestHtmlExporterPersonaColorOverride:
    """#90 — the HTML export must honour persona.color_override when
    the persona has one set, falling back to the provider colour
    otherwise. The in-app chat display already honours the override;
    the exporter was on a legacy provider-only path."""

    def _make_persona(self, **overrides):
        from mchat.models.persona import Persona, generate_persona_id
        fields = dict(
            conversation_id=1,
            id=generate_persona_id(),
            provider=Provider.CLAUDE,
            name="Partner",
            name_slug="partner",
        )
        fields.update(overrides)
        return Persona(**fields)

    @pytest.fixture
    def exporter(self):
        colors = ExportColors(
            user="#d4d4d4",
            claude="#b0b0b0",
            openai="#e8e8e8",
            gemini="#c8d8e8",
            perplexity="#d8c8e8",
            mistral="#ffe0c8",
        )
        return HtmlExporter(colors, font_size=14)

    def test_persona_color_override_used_in_export(self, exporter):
        """A persona with color_override='#abcdef' must have its
        messages rendered with that background colour, NOT the
        provider default."""
        p = self._make_persona(color_override="#abcdef")
        msgs = [
            Message(
                role=Role.ASSISTANT,
                content="critique",
                provider=Provider.CLAUDE,
                persona_id=p.id,
            ),
        ]
        html = exporter.export(msgs, personas=[p])
        assert "#abcdef" in html, (
            "persona.color_override must appear in the exported HTML"
        )

    def test_persona_without_override_falls_back_to_provider_color(
        self, exporter,
    ):
        """A persona with color_override=None must fall back to the
        provider colour from ExportColors."""
        p = self._make_persona(color_override=None)
        msgs = [
            Message(
                role=Role.ASSISTANT,
                content="hi",
                provider=Provider.CLAUDE,
                persona_id=p.id,
            ),
        ]
        html = exporter.export(msgs, personas=[p])
        assert "#b0b0b0" in html  # claude provider colour from fixture

    def test_override_applies_per_message_not_globally(self, exporter):
        """Two personas backing the same provider: one with override,
        one without. Each message must get its own colour."""
        p_custom = self._make_persona(
            name="Custom", color_override="#112233",
        )
        p_default = self._make_persona(
            name="Default", color_override=None,
        )
        msgs = [
            Message(
                role=Role.ASSISTANT,
                content="a",
                provider=Provider.CLAUDE,
                persona_id=p_custom.id,
            ),
            Message(
                role=Role.ASSISTANT,
                content="b",
                provider=Provider.CLAUDE,
                persona_id=p_default.id,
            ),
        ]
        html = exporter.export(msgs, personas=[p_custom, p_default])
        assert "#112233" in html  # custom override
        assert "#b0b0b0" in html  # claude provider default

    def test_tombstoned_persona_color_override_still_applied(self, exporter):
        """A tombstoned persona with color_override should still paint
        its historical messages with the override colour."""
        from datetime import datetime, timezone
        p = self._make_persona(
            name="Archived",
            name_slug="archived",
            color_override="#fedcba",
            deleted_at=datetime.now(timezone.utc),
        )
        msgs = [
            Message(
                role=Role.ASSISTANT,
                content="old",
                provider=Provider.CLAUDE,
                persona_id=p.id,
            ),
        ]
        html = exporter.export(msgs, personas=[p])
        assert "#fedcba" in html
