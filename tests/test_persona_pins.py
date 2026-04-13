# ------------------------------------------------------------------
# Component: test_persona_pins
# Responsibility: Tests for the extracted ensure_persona_pins function.
# Collaborators: ui.persona_pins, db, models
# ------------------------------------------------------------------
from __future__ import annotations

import pytest

from mchat.config import Config
from mchat.db import Database
from mchat.models.message import Provider, Role
from mchat.models.persona import Persona, generate_persona_id


@pytest.fixture
def db(tmp_path):
    d = Database(db_path=tmp_path / "test.db")
    yield d
    d.close()


@pytest.fixture
def config(tmp_path):
    cfg = Config(config_path=tmp_path / "cfg.json")
    cfg.save()
    return cfg


class TestEnsurePersonaPinsExtracted:
    """#162 — ensure_persona_pins is a standalone function."""

    def test_module_importable(self):
        from mchat.ui.persona_pins import ensure_persona_pins
        assert callable(ensure_persona_pins)

    def test_creates_pins_for_new_persona(self, db):
        from mchat.ui.persona_pins import ensure_persona_pins
        from mchat.ui.state import SelectionState

        conv = db.create_conversation()
        p = Persona(
            conversation_id=conv.id,
            id=generate_persona_id(),
            provider=Provider.CLAUDE,
            name="Bot",
            name_slug="bot",
        )
        db.create_persona(p)
        sel = SelectionState()
        messages = []

        ensure_persona_pins(db, conv, messages, sel)

        # Should have created 2 pinned messages (name + setup note)
        all_msgs = db.get_messages(conv.id)
        pinned = [m for m in all_msgs if m.pinned]
        assert len(pinned) == 2
        assert any("use Bot as your name" in m.content for m in pinned)

    def test_does_not_duplicate_pins(self, db):
        from mchat.ui.persona_pins import ensure_persona_pins
        from mchat.ui.state import SelectionState

        conv = db.create_conversation()
        p = Persona(
            conversation_id=conv.id,
            id=generate_persona_id(),
            provider=Provider.CLAUDE,
            name="Bot",
            name_slug="bot",
        )
        db.create_persona(p)
        sel = SelectionState()
        messages = []

        ensure_persona_pins(db, conv, messages, sel)
        # Call again — should not create duplicate pins
        messages = db.get_messages(conv.id)
        ensure_persona_pins(db, conv, messages, sel)

        all_msgs = db.get_messages(conv.id)
        pinned = [m for m in all_msgs if m.pinned]
        assert len(pinned) == 2  # still just 2, no duplicates

    def test_adds_persona_to_selection(self, db):
        from mchat.ui.persona_pins import ensure_persona_pins
        from mchat.ui.state import SelectionState

        conv = db.create_conversation()
        p = Persona(
            conversation_id=conv.id,
            id=generate_persona_id(),
            provider=Provider.CLAUDE,
            name="Bot",
            name_slug="bot",
        )
        db.create_persona(p)
        sel = SelectionState()
        messages = []

        ensure_persona_pins(db, conv, messages, sel)

        assert len(sel.selection) == 1
        assert sel.selection[0].persona_id == p.id
