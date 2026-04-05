# ------------------------------------------------------------------
# Component: test_persona_dialog
# Responsibility: pytest-qt tests for PersonaDialog — the modal
#                 editor for the persona list in a conversation.
#                 Exercises the dialog's service-level operations
#                 (create/edit/tombstone/reorder) rather than the
#                 pixel-level Qt event loop, matching the style of
#                 test_matrix_panel.py.
# Collaborators: ui.persona_dialog, db, config
# ------------------------------------------------------------------
from __future__ import annotations

import pytest

from mchat.config import Config
from mchat.db import Database
from mchat.models.message import Provider
from mchat.models.persona import Persona, generate_persona_id


@pytest.fixture
def db(tmp_path):
    d = Database(db_path=tmp_path / "dialog.db")
    yield d
    d.close()


@pytest.fixture
def config(tmp_path):
    cfg = Config(config_path=tmp_path / "cfg.json")
    # Set some globals so the "effective value" display has something
    # to show when overrides are None.
    cfg.set("system_prompt_claude", "Global Claude prompt")
    cfg.set("claude_model", "claude-sonnet-global")
    cfg.set("color_claude", "#b0b0b0")
    cfg.save()
    return cfg


@pytest.fixture
def conv(db):
    return db.create_conversation()


@pytest.fixture
def dialog(qtbot, db, config, conv):
    from mchat.ui.persona_dialog import PersonaDialog
    d = PersonaDialog(db, config, conv.id)
    qtbot.addWidget(d)
    return d


class TestPersonaDialogLoad:
    def test_empty_conversation_shows_no_personas(self, dialog, db, conv):
        assert dialog.list_items() == []

    def test_existing_personas_populate_the_list(
        self, qtbot, db, config, conv,
    ):
        from mchat.ui.persona_dialog import PersonaDialog
        p1 = Persona(
            conversation_id=conv.id, id=generate_persona_id(),
            provider=Provider.CLAUDE, name="Partner", name_slug="partner",
        )
        p2 = Persona(
            conversation_id=conv.id, id=generate_persona_id(),
            provider=Provider.CLAUDE, name="Evaluator", name_slug="evaluator",
        )
        db.create_persona(p1)
        db.create_persona(p2)

        d = PersonaDialog(db, config, conv.id)
        qtbot.addWidget(d)
        items = d.list_items()
        assert len(items) == 2
        names = [i.name for i in items]
        assert "Partner" in names
        assert "Evaluator" in names

    def test_tombstoned_personas_not_shown(self, qtbot, db, config, conv):
        from mchat.ui.persona_dialog import PersonaDialog
        active = Persona(
            conversation_id=conv.id, id=generate_persona_id(),
            provider=Provider.CLAUDE, name="Active", name_slug="active",
        )
        gone = Persona(
            conversation_id=conv.id, id=generate_persona_id(),
            provider=Provider.OPENAI, name="Gone", name_slug="gone",
        )
        db.create_persona(active)
        db.create_persona(gone)
        db.tombstone_persona(conv.id, gone.id)

        d = PersonaDialog(db, config, conv.id)
        qtbot.addWidget(d)
        names = [i.name for i in d.list_items()]
        assert "Active" in names
        assert "Gone" not in names


class TestCreatePersona:
    def test_create_persists_to_db(self, dialog, db, conv):
        dialog.create_persona(
            provider=Provider.CLAUDE,
            name="NewPersona",
            system_prompt_override="be terse",
            model_override="claude-haiku-4-5",
            color_override="#ff00ff",
        )
        personas = db.list_personas(conv.id)
        assert len(personas) == 1
        p = personas[0]
        assert p.name == "NewPersona"
        assert p.name_slug == "newpersona"
        assert p.system_prompt_override == "be terse"
        assert p.model_override == "claude-haiku-4-5"
        assert p.color_override == "#ff00ff"

    def test_create_with_null_overrides(self, dialog, db, conv):
        """Creating a persona with every override None means it
        inherits the global provider defaults at resolution time."""
        dialog.create_persona(
            provider=Provider.CLAUDE,
            name="Inheritor",
            system_prompt_override=None,
            model_override=None,
            color_override=None,
        )
        p = db.list_personas(conv.id)[0]
        assert p.system_prompt_override is None
        assert p.model_override is None
        assert p.color_override is None

    def test_create_duplicate_slug_raises(self, dialog, db, conv):
        import sqlite3
        dialog.create_persona(provider=Provider.CLAUDE, name="Dup")
        with pytest.raises(sqlite3.IntegrityError):
            dialog.create_persona(provider=Provider.CLAUDE, name="Dup")


class TestEditPersona:
    def test_edit_updates_all_override_fields(self, dialog, db, conv):
        dialog.create_persona(
            provider=Provider.CLAUDE, name="P",
            system_prompt_override="old", model_override="old-model",
            color_override="#111111",
        )
        p = db.list_personas(conv.id)[0]

        dialog.update_persona(
            p.id,
            system_prompt_override="new",
            model_override="new-model",
            color_override="#ffffff",
        )
        updated = db.list_personas(conv.id)[0]
        assert updated.system_prompt_override == "new"
        assert updated.model_override == "new-model"
        assert updated.color_override == "#ffffff"

    def test_edit_can_clear_overrides_to_none(self, dialog, db, conv):
        """Clearing an override via the dialog sets the DB value to
        None, which means "inherit from global" per D6."""
        dialog.create_persona(
            provider=Provider.CLAUDE, name="P",
            system_prompt_override="initial",
            model_override="claude-opus",
        )
        p = db.list_personas(conv.id)[0]

        dialog.update_persona(
            p.id,
            system_prompt_override=None,
            model_override=None,
        )
        updated = db.list_personas(conv.id)[0]
        assert updated.system_prompt_override is None
        assert updated.model_override is None


class TestRemovePersona:
    def test_remove_tombstones_not_hard_delete(self, dialog, db, conv):
        dialog.create_persona(provider=Provider.CLAUDE, name="Gone")
        p = db.list_personas(conv.id)[0]

        dialog.remove_persona(p.id)

        assert db.list_personas(conv.id) == []
        # But still present in list_personas_including_deleted
        all_personas = db.list_personas_including_deleted(conv.id)
        assert len(all_personas) == 1
        assert all_personas[0].deleted_at is not None


class TestEffectiveValueDisplay:
    """The dialog shows a 'currently effective' value next to each
    override field so the user can see what inherit → global would
    actually produce."""

    def test_effective_prompt_shows_override_when_set(self, dialog, config):
        from mchat.models.persona import Persona
        p = Persona(
            conversation_id=1, id="p_x",
            provider=Provider.CLAUDE, name="X", name_slug="x",
            system_prompt_override="overridden",
        )
        assert dialog.effective_prompt(p) == "overridden"

    def test_effective_prompt_shows_global_when_override_none(
        self, dialog, config,
    ):
        from mchat.models.persona import Persona
        p = Persona(
            conversation_id=1, id="p_x",
            provider=Provider.CLAUDE, name="X", name_slug="x",
            system_prompt_override=None,
        )
        assert dialog.effective_prompt(p) == "Global Claude prompt"

    def test_effective_model_shows_override_when_set(self, dialog, config):
        from mchat.models.persona import Persona
        p = Persona(
            conversation_id=1, id="p_x",
            provider=Provider.CLAUDE, name="X", name_slug="x",
            model_override="claude-opus-4",
        )
        assert dialog.effective_model(p) == "claude-opus-4"

    def test_effective_model_shows_global_when_override_none(
        self, dialog, config,
    ):
        from mchat.models.persona import Persona
        p = Persona(
            conversation_id=1, id="p_x",
            provider=Provider.CLAUDE, name="X", name_slug="x",
            model_override=None,
        )
        assert dialog.effective_model(p) == "claude-sonnet-global"

    def test_effective_color_shows_override_when_set(self, dialog, config):
        from mchat.models.persona import Persona
        p = Persona(
            conversation_id=1, id="p_x",
            provider=Provider.CLAUDE, name="X", name_slug="x",
            color_override="#abcdef",
        )
        assert dialog.effective_color(p) == "#abcdef"

    def test_effective_color_shows_global_when_override_none(
        self, dialog, config,
    ):
        from mchat.models.persona import Persona
        p = Persona(
            conversation_id=1, id="p_x",
            provider=Provider.CLAUDE, name="X", name_slug="x",
            color_override=None,
        )
        assert dialog.effective_color(p) == "#b0b0b0"
