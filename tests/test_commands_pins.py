# ------------------------------------------------------------------
# Component: test_commands_pins
# Responsibility: Tests for //pin, //unpin, //pins command handlers,
#                 in particular that @-prefixed targets work the
#                 same as bare names (#149).
# Collaborators: ui.commands.pins, db
# ------------------------------------------------------------------
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from mchat.config import Config
from mchat.db import Database
from mchat.models.message import Provider, Role
from mchat.models.persona import Persona, generate_persona_id


@pytest.fixture
def db(tmp_path):
    d = Database(db_path=tmp_path / "pins.db")
    yield d
    d.close()


@pytest.fixture
def config(tmp_path):
    return Config(config_path=tmp_path / "cfg.json")


@pytest.fixture
def host(db, config):
    """Minimal CommandHost fake for pin commands."""
    h = MagicMock()
    h._db = db
    conv = db.create_conversation()
    h._current_conv = conv
    h._current_conv.messages = []

    h._chat.notes = []
    h._chat.add_note = lambda text: h._chat.notes.append(text)
    h._chat.textCursor = MagicMock()
    h._chat._scroll_to_bottom = MagicMock()
    h._display_messages = MagicMock()
    h._on_new_chat = MagicMock()
    return h


def _make_persona(conv_id, name, provider=Provider.CLAUDE):
    return Persona(
        conversation_id=conv_id,
        id=generate_persona_id(),
        provider=provider,
        name=name,
        name_slug=name.lower(),
    )


class TestPinAtPrefixedTargets:
    """#149 — //pin should accept @-prefixed targets in addition
    to bare names, so @all and all both work."""

    def test_pin_at_all_creates_pin_targeting_all(self, host, db):
        from mchat.ui.commands.pins import handle_pin

        handle_pin("@all, be concise", host)
        msgs = db.get_messages(host._current_conv.id)
        pinned = [m for m in msgs if m.pinned]
        assert len(pinned) == 1
        assert pinned[0].pin_target == "all"
        assert pinned[0].content == "be concise"

    def test_pin_bare_all_still_works(self, host, db):
        from mchat.ui.commands.pins import handle_pin

        handle_pin("all, stay focused", host)
        msgs = db.get_messages(host._current_conv.id)
        pinned = [m for m in msgs if m.pinned]
        assert len(pinned) == 1
        assert pinned[0].pin_target == "all"

    def test_pin_at_provider_resolves(self, host, db):
        from mchat.ui.commands.pins import handle_pin

        handle_pin("@claude, use bullet points", host)
        msgs = db.get_messages(host._current_conv.id)
        pinned = [m for m in msgs if m.pinned]
        assert len(pinned) == 1
        assert pinned[0].pin_target == "claude"

    def test_pin_at_persona_name_resolves(self, host, db):
        from mchat.ui.commands.pins import handle_pin

        p = _make_persona(host._current_conv.id, "Partner")
        db.create_persona(p)

        handle_pin("@partner, answer in French", host)
        msgs = db.get_messages(host._current_conv.id)
        pinned = [m for m in msgs if m.pinned]
        assert len(pinned) == 1
        assert pinned[0].pin_target == p.provider.value


class TestPinsAtPrefixedFilter:
    """#149 — //pins should accept @-prefixed filter names."""

    def test_pins_at_provider_lists_pins(self, host, db):
        from mchat.ui.commands.pins import handle_pin, handle_pins

        handle_pin("claude, be concise", host)
        host._chat.notes.clear()
        handle_pins("@claude", host)
        # Should NOT produce an error about unknown persona/provider.
        assert not any("Error" in n for n in host._chat.notes)

    def test_pins_at_persona_lists_pins(self, host, db):
        from mchat.ui.commands.pins import handle_pin, handle_pins

        p = _make_persona(host._current_conv.id, "Critic")
        db.create_persona(p)
        handle_pin("critic, be harsh", host)
        host._chat.notes.clear()
        handle_pins("@critic", host)
        assert not any("Error" in n for n in host._chat.notes)
