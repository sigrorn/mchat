# ------------------------------------------------------------------
# Component: test_chat_widget
# Responsibility: Tests for ChatWidget's persona-aware colour
#                 resolution (Stage 3A.2). The widget gains a
#                 PersonaColorResolver hook that looks up colour
#                 overrides for persona-tagged messages with a
#                 per-conversation cache.
# Collaborators: ui.chat_widget, ui.persona_color_resolver, db, config
# ------------------------------------------------------------------
from __future__ import annotations

import pytest

from mchat.config import Config
from mchat.db import Database
from mchat.models.message import Message, Provider, Role
from mchat.models.persona import Persona, generate_persona_id
from mchat.ui.chat_widget import ChatWidget


@pytest.fixture
def db(tmp_path):
    d = Database(db_path=tmp_path / "chat.db")
    yield d
    d.close()


@pytest.fixture
def config(tmp_path):
    cfg = Config(config_path=tmp_path / "cfg.json")
    cfg.set("color_claude", "#b0b0b0")  # provider default
    cfg.set("color_openai", "#e8e8e8")
    cfg.save()
    return cfg


@pytest.fixture
def conv(db):
    return db.create_conversation()


@pytest.fixture
def resolver(db, config, conv):
    """PersonaColorResolver bound to a fresh conversation."""
    from mchat.ui.persona_color_resolver import PersonaColorResolver
    r = PersonaColorResolver(db, config)
    r.set_conversation(conv.id)
    return r


@pytest.fixture
def chat(qtbot, resolver):
    widget = ChatWidget(
        font_size=14,
        color_claude="#b0b0b0",
        color_openai="#e8e8e8",
        color_gemini="#c8d8e8",
        color_perplexity="#d8c8e8",
    )
    widget.set_persona_color_resolver(resolver)
    qtbot.addWidget(widget)
    return widget


def _persona(conv_id, name, slug, provider=Provider.CLAUDE,
             color_override=None):
    return Persona(
        conversation_id=conv_id,
        id=generate_persona_id(),
        provider=provider,
        name=name,
        name_slug=slug,
        color_override=color_override,
    )


class TestPersonaColorOverride:
    def test_persona_with_color_override_uses_it(self, chat, db, conv, resolver):
        p = db.create_persona(
            _persona(conv.id, "Partner", "partner", color_override="#ff00ff"),
        )
        resolver.invalidate()  # new persona, refresh cache

        msg = Message(
            role=Role.ASSISTANT,
            content="hi",
            provider=Provider.CLAUDE,
            persona_id=p.id,
        )
        assert chat._color_for(msg) == "#ff00ff"

    def test_persona_with_none_override_uses_provider_default(
        self, chat, db, conv, resolver,
    ):
        p = db.create_persona(
            _persona(conv.id, "Inheritor", "inheritor", color_override=None),
        )
        resolver.invalidate()

        msg = Message(
            role=Role.ASSISTANT,
            content="hi",
            provider=Provider.CLAUDE,
            persona_id=p.id,
        )
        assert chat._color_for(msg) == "#b0b0b0"

    def test_legacy_message_without_persona_id_uses_provider_default(
        self, chat,
    ):
        """Regression guard: messages with persona_id=None render
        exactly as they did before Stage 3A.2."""
        msg = Message(
            role=Role.ASSISTANT,
            content="legacy",
            provider=Provider.CLAUDE,
        )
        assert chat._color_for(msg) == "#b0b0b0"

    def test_user_messages_always_use_user_color(self, chat):
        msg = Message(role=Role.USER, content="hi")
        # ChatWidget.__init__ sets color_user default to COLOR_USER constant
        from mchat.ui.chat_widget import COLOR_USER
        assert chat._color_for(msg) == COLOR_USER

    def test_unknown_persona_id_falls_back_to_provider(self, chat):
        """Defensive: if a message has persona_id pointing at a row
        not in the DB (tombstoned & not loaded, or stale), fall back
        to the provider default. The resolver uses
        list_personas_including_deleted, so this case is rare in
        practice."""
        msg = Message(
            role=Role.ASSISTANT,
            content="orphan",
            provider=Provider.CLAUDE,
            persona_id="p_nonexistent",
        )
        assert chat._color_for(msg) == "#b0b0b0"

    def test_two_personas_same_provider_render_in_distinct_colours(
        self, chat, db, conv, resolver,
    ):
        partner = db.create_persona(
            _persona(conv.id, "Partner", "partner", color_override="#aa0000"),
        )
        evaluator = db.create_persona(
            _persona(conv.id, "Evaluator", "evaluator", color_override="#00aa00"),
        )
        resolver.invalidate()

        m1 = Message(
            role=Role.ASSISTANT, content="a",
            provider=Provider.CLAUDE, persona_id=partner.id,
        )
        m2 = Message(
            role=Role.ASSISTANT, content="b",
            provider=Provider.CLAUDE, persona_id=evaluator.id,
        )
        assert chat._color_for(m1) == "#aa0000"
        assert chat._color_for(m2) == "#00aa00"

    def test_resolver_cache_invalidates_when_persona_changes(
        self, chat, db, conv, resolver,
    ):
        p = db.create_persona(
            _persona(conv.id, "X", "x", color_override="#111111"),
        )
        resolver.invalidate()

        msg = Message(
            role=Role.ASSISTANT, content="hi",
            provider=Provider.CLAUDE, persona_id=p.id,
        )
        assert chat._color_for(msg) == "#111111"

        # Update the override and invalidate the cache
        p.color_override = "#222222"
        db.update_persona(p)
        resolver.invalidate()

        assert chat._color_for(msg) == "#222222"

    def test_tombstoned_persona_keeps_its_colour_for_historical_labels(
        self, chat, db, conv, resolver,
    ):
        """Tombstoned personas still resolve via
        list_personas_including_deleted so historical messages
        render in the correct colour."""
        p = db.create_persona(
            _persona(conv.id, "Gone", "gone", color_override="#333333"),
        )
        resolver.invalidate()

        msg = Message(
            role=Role.ASSISTANT, content="old",
            provider=Provider.CLAUDE, persona_id=p.id,
        )
        assert chat._color_for(msg) == "#333333"

        db.tombstone_persona(conv.id, p.id)
        resolver.invalidate()

        # Still rendered in the persona's colour
        assert chat._color_for(msg) == "#333333"


class TestUndoRedoDisabled:
    """#132 — undo/redo must be disabled on the chat document to
    avoid allocating undo records on every insertion. The chat is a
    read-only transcript; there is nothing to undo."""

    def test_undo_redo_disabled_on_init(self, chat):
        assert chat.document().isUndoRedoEnabled() is False


class TestPartialExclusionUpdate:
    """#133 — chat widget must support updating the excluded-indices
    shading without a full re-render. //limit uses this path so it
    doesn't re-parse markdown and re-insert every message just to
    change background colours."""

    def test_apply_excluded_indices_updates_block_backgrounds(
        self, qtbot, chat, db, conv,
    ):
        """After apply_excluded_indices(new_set), the chat document's
        block backgrounds reflect the new exclusion state without
        content being re-inserted."""
        # Seed three messages
        msgs = [
            Message(role=Role.USER, content="q1"),
            Message(role=Role.ASSISTANT, content="a1", provider=Provider.CLAUDE),
            Message(role=Role.USER, content="q2"),
        ]
        for m in msgs:
            chat.add_message(m)

        # Capture baseline colour of message 0 (no exclusion)
        doc = chat.document()
        msg0_start_block = chat._message_block_starts[0]
        block0 = doc.findBlockByNumber(msg0_start_block)
        baseline_bg = block0.blockFormat().background().color().name()

        # Apply exclusion to message 0
        chat.apply_excluded_indices({0})

        # Message 0 block should now have the shaded colour
        block0 = doc.findBlockByNumber(msg0_start_block)
        shaded_bg = block0.blockFormat().background().color().name()
        assert shaded_bg != baseline_bg

        # Message 1 block should still have its unshaded colour
        msg1_start = chat._message_block_starts[1]
        block1 = doc.findBlockByNumber(msg1_start)
        bg1 = block1.blockFormat().background().color().name()
        # (Not shaded — unchanged from its original)
        assert bg1 != shaded_bg or block1.blockNumber() == block0.blockNumber()

    def test_apply_excluded_indices_no_markdown_re_parsing(
        self, qtbot, chat, monkeypatch,
    ):
        """apply_excluded_indices must NOT call md.convert() — that's
        the expensive path we're trying to avoid."""
        msgs = [
            Message(role=Role.USER, content="q1"),
            Message(
                role=Role.ASSISTANT,
                content="**markdown** here",
                provider=Provider.CLAUDE,
            ),
            Message(role=Role.USER, content="q2"),
        ]
        for m in msgs:
            chat.add_message(m)

        # Count md.convert calls during apply_excluded_indices
        call_count = [0]
        original_convert = chat._md.convert

        def counting_convert(text):
            call_count[0] += 1
            return original_convert(text)

        monkeypatch.setattr(chat._md, "convert", counting_convert)

        chat.apply_excluded_indices({0, 2})
        assert call_count[0] == 0, (
            "apply_excluded_indices must not re-parse markdown"
        )

    def test_apply_excluded_indices_updates_set(self, chat):
        """The helper must update _excluded_indices so subsequent
        renders pick up the new state."""
        msgs = [
            Message(role=Role.USER, content="q"),
            Message(role=Role.ASSISTANT, content="a", provider=Provider.CLAUDE),
        ]
        for m in msgs:
            chat.add_message(m)

        assert chat._excluded_indices == set()
        chat.apply_excluded_indices({0, 1})
        assert chat._excluded_indices == {0, 1}

    def test_message_block_starts_populated_on_insert(self, chat):
        """The chat widget must track each message's starting block
        number so partial updates can locate them."""
        msgs = [
            Message(role=Role.USER, content="q1"),
            Message(role=Role.ASSISTANT, content="a1", provider=Provider.CLAUDE),
        ]
        for m in msgs:
            chat.add_message(m)
        assert hasattr(chat, "_message_block_starts")
        assert len(chat._message_block_starts) == 2
        # Block numbers are non-negative and strictly increasing
        assert chat._message_block_starts[0] >= 0
        assert chat._message_block_starts[1] > chat._message_block_starts[0]
