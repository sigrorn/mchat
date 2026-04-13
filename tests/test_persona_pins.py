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


class TestRenameUpdatesIdentityPins:
    """#163 — renaming a persona must update existing identity pins,
    not create contradictory duplicates."""

    def test_rename_updates_identity_pin_content(self, db):
        from mchat.ui.persona_pins import ensure_persona_pins
        from mchat.ui.state import SelectionState

        conv = db.create_conversation()
        p = Persona(
            conversation_id=conv.id,
            id=generate_persona_id(),
            provider=Provider.CLAUDE,
            name="OldName",
            name_slug="oldname",
        )
        db.create_persona(p)
        sel = SelectionState()
        messages = []

        # First call — creates pins for OldName
        ensure_persona_pins(db, conv, messages, sel)
        all_msgs = db.get_messages(conv.id)
        pinned = [m for m in all_msgs if m.pinned]
        assert len(pinned) == 2
        assert any("use OldName as your name" in m.content for m in pinned)

        # Simulate rename: update persona name in DB
        p.name = "NewName"
        p.name_slug = "newname"
        db.update_persona(p)

        # Second call — should UPDATE the existing pin, not add a new one
        messages = db.get_messages(conv.id)
        ensure_persona_pins(db, conv, messages, sel)

        all_msgs = db.get_messages(conv.id)
        pinned = [m for m in all_msgs if m.pinned]
        # Must still be exactly 2 pins, not 4
        assert len(pinned) == 2, (
            f"Expected 2 pins after rename, got {len(pinned)}: "
            + " | ".join(m.content[:60] for m in pinned)
        )
        # The identity pin must reference the new name
        identity_pins = [m for m in pinned if "as your name" in m.content]
        assert len(identity_pins) == 1
        assert "use NewName as your name" in identity_pins[0].content
        # Old name must NOT appear in any pin
        assert not any("OldName" in m.content for m in pinned)

    def test_rename_does_not_lose_other_persona_pins(self, db):
        """Two personas: renaming one must not affect the other's pins."""
        from mchat.ui.persona_pins import ensure_persona_pins
        from mchat.ui.state import SelectionState

        conv = db.create_conversation()
        p1 = Persona(
            conversation_id=conv.id,
            id=generate_persona_id(),
            provider=Provider.CLAUDE,
            name="Alpha",
            name_slug="alpha",
        )
        p2 = Persona(
            conversation_id=conv.id,
            id=generate_persona_id(),
            provider=Provider.OPENAI,
            name="Beta",
            name_slug="beta",
            sort_order=1,
        )
        db.create_persona(p1)
        db.create_persona(p2)
        sel = SelectionState()
        messages = []

        ensure_persona_pins(db, conv, messages, sel)
        # 4 pins total (2 per persona)
        all_msgs = db.get_messages(conv.id)
        assert len([m for m in all_msgs if m.pinned]) == 4

        # Rename Alpha → Gamma
        p1.name = "Gamma"
        p1.name_slug = "gamma"
        db.update_persona(p1)

        messages = db.get_messages(conv.id)
        ensure_persona_pins(db, conv, messages, sel)

        all_msgs = db.get_messages(conv.id)
        pinned = [m for m in all_msgs if m.pinned]
        # Still 4 pins
        assert len(pinned) == 4
        # Gamma present, Alpha gone, Beta unchanged
        assert any("use Gamma as your name" in m.content for m in pinned)
        assert not any("Alpha" in m.content for m in pinned)
        assert any("use Beta as your name" in m.content for m in pinned)
