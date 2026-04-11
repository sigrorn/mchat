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


# A real 91-byte PNG of a 1x1 red pixel, used as a drop-in for
# dot_renderer.render_dot when graphviz isn't available in the
# test environment.
import base64 as _b64

_MINI_PNG = _b64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAACXBIWXMAAA9hAAAP"
    "YQGoP6dpAAAADUlEQVQImWP4z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg=="
)


class TestHtmlExporterDotGraphs:
    """#145 — HTML export must inline DOT graph PNGs as base64 data
    URIs so the exported .html file is self-contained and viewable
    in any browser without the app."""

    def test_dot_block_becomes_base64_data_uri(self, exporter, monkeypatch):
        from mchat import dot_renderer

        monkeypatch.setattr(
            dot_renderer, "render_dot",
            lambda source, **kw: _MINI_PNG,
        )

        msgs = [
            Message(
                role=Role.ASSISTANT,
                content="```dot\ndigraph { a -> b }\n```",
                provider=Provider.CLAUDE,
            )
        ]
        html = exporter.export(msgs)
        assert "data:image/png;base64," in html
        # The mchat-graph:// URL should have been replaced, not left
        # in place alongside the data URI.
        assert "mchat-graph://" not in html

    def test_data_uri_decodes_to_valid_png(self, exporter, monkeypatch):
        import re as _re

        from mchat import dot_renderer

        monkeypatch.setattr(
            dot_renderer, "render_dot",
            lambda source, **kw: _MINI_PNG,
        )

        msgs = [
            Message(
                role=Role.ASSISTANT,
                content="```dot\ndigraph { x -> y }\n```",
                provider=Provider.CLAUDE,
            )
        ]
        html = exporter.export(msgs)
        m = _re.search(r'data:image/png;base64,([A-Za-z0-9+/=]+)', html)
        assert m is not None
        decoded = _b64.b64decode(m.group(1))
        assert decoded.startswith(b"\x89PNG\r\n\x1a\n")
        assert decoded == _MINI_PNG

    def test_details_source_fallback_preserved(self, exporter, monkeypatch):
        """The <details> source block must survive into the export
        even on the happy path so the user can still read the raw
        DOT if they want."""
        from mchat import dot_renderer

        monkeypatch.setattr(
            dot_renderer, "render_dot",
            lambda source, **kw: _MINI_PNG,
        )

        msgs = [
            Message(
                role=Role.ASSISTANT,
                content="```dot\ndigraph { a -> b }\n```",
                provider=Provider.CLAUDE,
            )
        ]
        html = exporter.export(msgs)
        assert "<details" in html
        assert "dot source" in html

    def test_no_dot_block_unchanged(self, exporter, monkeypatch):
        """Regression: a message with no DOT block must round-trip
        through export unchanged — no base64 URI, no broken markup."""
        from mchat import dot_renderer

        # render_dot would fail the test if accidentally called.
        def boom(*a, **kw):
            raise AssertionError("render_dot should not be called")

        monkeypatch.setattr(dot_renderer, "render_dot", boom)

        msgs = [
            Message(
                role=Role.ASSISTANT,
                content="Just **bold** text, no graphs.",
                provider=Provider.CLAUDE,
            )
        ]
        html = exporter.export(msgs)
        assert "data:image/png" not in html
        assert "mchat-graph://" not in html
        assert "<strong>bold</strong>" in html

    def test_render_failure_leaves_source_fallback_only(
        self, exporter, monkeypatch
    ):
        """When render_dot returns None (graphviz missing, bad source,
        timeout, …) the exporter must NOT produce a broken
        <img src="mchat-graph://..."> — it must either drop the tag
        or fall back to the <details> source block alone."""
        from mchat import dot_renderer

        monkeypatch.setattr(
            dot_renderer, "render_dot", lambda source, **kw: None,
        )

        msgs = [
            Message(
                role=Role.ASSISTANT,
                content="```dot\ndigraph { a -> b }\n```",
                provider=Provider.CLAUDE,
            )
        ]
        html = exporter.export(msgs)
        assert "mchat-graph://" not in html  # no broken img remains
        assert "data:image/png" not in html
        # Source fallback is still in the output.
        assert "digraph" in html

    def test_degradation_warning_when_render_fails(
        self, exporter, monkeypatch
    ):
        """#146 — when at least one DOT block can't be rendered the
        exported file must carry a visible warning at the top so
        whoever opens the file realises graphics are missing."""
        from mchat import dot_renderer

        monkeypatch.setattr(
            dot_renderer, "render_dot", lambda source, **kw: None,
        )

        msgs = [
            Message(
                role=Role.ASSISTANT,
                content="```dot\ndigraph { a -> b }\n```",
                provider=Provider.CLAUDE,
            )
        ]
        html = exporter.export(msgs)
        assert "Graphviz" in html or "graphviz" in html
        assert "shown as source" in html or "source only" in html
        # Should be BEFORE the first message so it can't be missed.
        warn_pos = html.lower().find("graphviz")
        msg_pos = html.find("digraph")
        assert warn_pos != -1 and warn_pos < msg_pos

    def test_no_degradation_warning_on_happy_path(
        self, exporter, monkeypatch
    ):
        from mchat import dot_renderer

        monkeypatch.setattr(
            dot_renderer, "render_dot",
            lambda source, **kw: _MINI_PNG,
        )

        msgs = [
            Message(
                role=Role.ASSISTANT,
                content="```dot\ndigraph { a -> b }\n```",
                provider=Provider.CLAUDE,
            )
        ]
        html = exporter.export(msgs)
        assert "shown as source" not in html
        assert "source only" not in html

    def test_no_degradation_warning_when_no_dot_blocks(
        self, exporter, monkeypatch
    ):
        from mchat import dot_renderer

        # render_dot should never be called — if it is, explode.
        def boom(*a, **kw):
            raise AssertionError("render_dot should not be called")

        monkeypatch.setattr(dot_renderer, "render_dot", boom)

        msgs = [
            Message(
                role=Role.ASSISTANT,
                content="Plain **text**, no graphs.",
                provider=Provider.CLAUDE,
            )
        ]
        html = exporter.export(msgs)
        assert "shown as source" not in html
        assert "source only" not in html

    def test_uses_disk_cache_so_no_subprocess_on_repeat_export(
        self, exporter, monkeypatch, tmp_path
    ):
        """The exporter reads through dot_renderer.render_dot, which
        consults the in-memory + disk cache before shelling out. A
        second export of the same DOT source must not re-run the
        subprocess."""
        from mchat import dot_renderer

        # Redirect disk cache at a tmp path and wipe in-memory state.
        monkeypatch.setattr(
            dot_renderer, "cache_dir",
            lambda: tmp_path / "graph_cache",
        )
        dot_renderer._MEMORY_CACHE.clear()
        dot_renderer.is_graphviz_available.cache_clear()

        # Pretend graphviz is installed.
        monkeypatch.setattr(
            dot_renderer.shutil, "which",
            lambda n: "/usr/bin/dot" if n == "dot" else None,
        )

        import subprocess as _sp
        counter = {"n": 0}

        def fake_run(cmd, **kw):
            counter["n"] += 1
            return _sp.CompletedProcess(
                cmd, returncode=0, stdout=_MINI_PNG, stderr=b""
            )

        monkeypatch.setattr(dot_renderer.subprocess, "run", fake_run)

        msgs = [
            Message(
                role=Role.ASSISTANT,
                content="```dot\ndigraph { x -> y }\n```",
                provider=Provider.CLAUDE,
            )
        ]
        exporter.export(msgs)
        assert counter["n"] == 1

        # Second exporter instance → fresh Markdown state, but the
        # disk cache should still serve the render.
        colors = ExportColors(
            user="#d4d4d4",
            claude="#b0b0b0",
            openai="#e8e8e8",
            gemini="#c8d8e8",
            perplexity="#d8c8e8",
            mistral="#ffe0c8",
        )
        exporter2 = HtmlExporter(colors, font_size=14)
        # Wipe in-memory cache to force the disk cache path.
        dot_renderer._MEMORY_CACHE.clear()
        exporter2.export(msgs)
        assert counter["n"] == 1  # still 1 — disk served it


class TestHelpTextMentionsGraphviz:
    """#146 — //help must tell the user that DOT graphs require
    graphviz, so they know why their graphs aren't rendering."""

    def test_help_commands_string_mentions_graphviz(self):
        from mchat.ui.commands.help import HELP_COMMANDS

        assert "raphviz" in HELP_COMMANDS or "DOT graph" in HELP_COMMANDS
