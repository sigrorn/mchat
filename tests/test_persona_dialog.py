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


class TestModelOverrideCombo:
    """#81 — per-persona model override via a combo in PersonaDialog.

    The dialog should show a QComboBox populated from the provider's
    model list, with a 'Use provider default' option that maps to
    model_override=None."""

    @pytest.fixture
    def dialog_with_models(self, qtbot, db, config, conv):
        from mchat.ui.persona_dialog import PersonaDialog
        cache = {
            Provider.CLAUDE: ["claude-sonnet-4", "claude-opus-4", "claude-haiku-4"],
            Provider.OPENAI: ["gpt-4.1", "gpt-4.1-mini"],
        }
        d = PersonaDialog(db, config, conv.id, models_cache=cache)
        qtbot.addWidget(d)
        return d

    def test_model_combo_exists(self, dialog_with_models):
        """PersonaDialog should have a _model_combo QComboBox."""
        assert hasattr(dialog_with_models, "_model_combo")
        from PySide6.QtWidgets import QComboBox
        assert isinstance(dialog_with_models._model_combo, QComboBox)

    def test_model_combo_has_use_provider_default(self, dialog_with_models, db, conv):
        """The first item in the model combo should be 'Use provider default'."""
        dialog_with_models.create_persona(
            provider=Provider.CLAUDE, name="Test",
        )
        dialog_with_models._refresh_list()
        dialog_with_models._list.setCurrentRow(0)
        assert dialog_with_models._model_combo.itemText(0) == "Use provider default"

    def test_model_combo_populated_from_cache(self, dialog_with_models, db, conv):
        """Model combo should contain the provider's models from cache."""
        dialog_with_models.create_persona(
            provider=Provider.CLAUDE, name="Test",
        )
        dialog_with_models._refresh_list()
        dialog_with_models._list.setCurrentRow(0)
        items = [
            dialog_with_models._model_combo.itemText(i)
            for i in range(dialog_with_models._model_combo.count())
        ]
        assert "claude-sonnet-4" in items
        assert "claude-opus-4" in items

    def test_persona_with_none_override_shows_default_selected(
        self, dialog_with_models, db, conv,
    ):
        """A persona with model_override=None should show 'Use provider
        default' selected."""
        dialog_with_models.create_persona(
            provider=Provider.CLAUDE, name="Test", model_override=None,
        )
        dialog_with_models._refresh_list()
        dialog_with_models._list.setCurrentRow(0)
        assert dialog_with_models._model_combo.currentText() == "Use provider default"

    def test_persona_with_explicit_override_shows_that_model(
        self, dialog_with_models, db, conv,
    ):
        """A persona with model_override set should show that model selected."""
        dialog_with_models.create_persona(
            provider=Provider.CLAUDE, name="Test",
            model_override="claude-opus-4",
        )
        dialog_with_models._refresh_list()
        dialog_with_models._list.setCurrentRow(0)
        assert dialog_with_models._model_combo.currentText() == "claude-opus-4"

    def test_save_with_explicit_model_writes_override(
        self, dialog_with_models, db, conv,
    ):
        """Saving with a specific model selected should write model_override."""
        dialog_with_models.create_persona(
            provider=Provider.CLAUDE, name="Test",
        )
        dialog_with_models._refresh_list()
        dialog_with_models._list.setCurrentRow(0)
        # Select a specific model
        idx = dialog_with_models._model_combo.findText("claude-opus-4")
        dialog_with_models._model_combo.setCurrentIndex(idx)
        dialog_with_models._on_save_clicked()
        p = db.list_personas(conv.id)[0]
        assert p.model_override == "claude-opus-4"

    def test_save_with_default_writes_none(
        self, dialog_with_models, db, conv,
    ):
        """Saving with 'Use provider default' selected should write None."""
        dialog_with_models.create_persona(
            provider=Provider.CLAUDE, name="Test",
            model_override="claude-opus-4",
        )
        dialog_with_models._refresh_list()
        dialog_with_models._list.setCurrentRow(0)
        # Select "Use provider default"
        dialog_with_models._model_combo.setCurrentIndex(0)
        dialog_with_models._on_save_clicked()
        p = db.list_personas(conv.id)[0]
        assert p.model_override is None


class TestProviderModelSwitch:
    """#82 — changing a persona's provider in the dialog should
    repopulate the model combo and, on save, write both provider
    and model_override atomically. persona_id stays stable."""

    @pytest.fixture
    def dialog_with_models(self, qtbot, db, config, conv):
        from mchat.ui.persona_dialog import PersonaDialog
        cache = {
            Provider.CLAUDE: ["claude-sonnet-4", "claude-opus-4"],
            Provider.OPENAI: ["gpt-4.1", "gpt-4.1-mini"],
            Provider.MISTRAL: ["mistral-large-latest", "mistral-small-latest"],
        }
        d = PersonaDialog(db, config, conv.id, models_cache=cache)
        qtbot.addWidget(d)
        return d

    def test_changing_provider_repopulates_model_combo(
        self, dialog_with_models, db, conv,
    ):
        """When the provider combo changes, the model combo should
        list the new provider's models."""
        dialog_with_models.create_persona(
            provider=Provider.CLAUDE, name="Switcher",
        )
        dialog_with_models._refresh_list()
        dialog_with_models._list.setCurrentRow(0)
        # Switch provider to OpenAI
        idx = dialog_with_models._provider_combo.findData(Provider.OPENAI)
        dialog_with_models._provider_combo.setCurrentIndex(idx)
        items = [
            dialog_with_models._model_combo.itemText(i)
            for i in range(dialog_with_models._model_combo.count())
        ]
        assert "gpt-4.1" in items
        assert "claude-sonnet-4" not in items
        assert items[0] == "Use provider default"

    def test_changing_provider_resets_model_to_default(
        self, dialog_with_models, db, conv,
    ):
        """After a provider switch, the model combo should reset to
        'Use provider default'."""
        dialog_with_models.create_persona(
            provider=Provider.CLAUDE, name="Switcher",
            model_override="claude-opus-4",
        )
        dialog_with_models._refresh_list()
        dialog_with_models._list.setCurrentRow(0)
        idx = dialog_with_models._provider_combo.findData(Provider.OPENAI)
        dialog_with_models._provider_combo.setCurrentIndex(idx)
        assert dialog_with_models._model_combo.currentText() == "Use provider default"

    def test_save_after_provider_switch_writes_both(
        self, dialog_with_models, db, conv,
    ):
        """Saving after a provider switch should write both the new
        provider and the new model_override."""
        dialog_with_models.create_persona(
            provider=Provider.CLAUDE, name="Switcher",
        )
        dialog_with_models._refresh_list()
        dialog_with_models._list.setCurrentRow(0)
        original_id = db.list_personas(conv.id)[0].id
        # Switch to Mistral + pick a specific model
        idx = dialog_with_models._provider_combo.findData(Provider.MISTRAL)
        dialog_with_models._provider_combo.setCurrentIndex(idx)
        midx = dialog_with_models._model_combo.findText("mistral-large-latest")
        dialog_with_models._model_combo.setCurrentIndex(midx)
        dialog_with_models._on_save_clicked()
        p = db.list_personas(conv.id)[0]
        assert p.id == original_id  # persona_id unchanged
        assert p.provider == Provider.MISTRAL
        assert p.model_override == "mistral-large-latest"

    def test_persona_id_unchanged_after_provider_switch(
        self, dialog_with_models, db, conv,
    ):
        """persona_id must be stable across provider switches."""
        dialog_with_models.create_persona(
            provider=Provider.CLAUDE, name="Switcher",
        )
        dialog_with_models._refresh_list()
        dialog_with_models._list.setCurrentRow(0)
        original_id = db.list_personas(conv.id)[0].id
        idx = dialog_with_models._provider_combo.findData(Provider.OPENAI)
        dialog_with_models._provider_combo.setCurrentIndex(idx)
        dialog_with_models._on_save_clicked()
        p = db.list_personas(conv.id)[0]
        assert p.id == original_id
        assert p.provider == Provider.OPENAI
