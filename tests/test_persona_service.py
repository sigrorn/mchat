# ------------------------------------------------------------------
# Component: test_persona_service
# Responsibility: Tests for the extracted PersonaService — service-
#                 level persona operations without Qt dependency.
# Collaborators: services.persona_service, db, config
# ------------------------------------------------------------------
from __future__ import annotations

import pytest

from mchat.config import Config
from mchat.db import Database
from mchat.models.message import Provider


@pytest.fixture
def db(tmp_path):
    d = Database(db_path=tmp_path / "dialog.db")
    yield d
    d.close()


@pytest.fixture
def config(tmp_path):
    cfg = Config(config_path=tmp_path / "cfg.json")
    cfg.set("anthropic_api_key", "fake-key")
    cfg.set("system_prompt_claude", "be helpful")
    cfg.set("claude_model", "claude-sonnet-4")
    cfg.save()
    return cfg


@pytest.fixture
def conv(db):
    return db.create_conversation(system_prompt="test prompt")


class TestPersonaServiceExtracted:
    """#160 — PersonaService is a standalone class extracted from PersonaDialog."""

    def test_module_importable(self):
        from mchat.services.persona_service import PersonaService
        assert PersonaService is not None

    def test_no_qt_dependency(self):
        """PersonaService must not import any Qt module."""
        import importlib
        mod = importlib.import_module("mchat.services.persona_service")
        source = open(mod.__file__, "r").read()
        assert "PySide6" not in source
        assert "QtWidgets" not in source

    def test_create_and_list(self, db, config, conv):
        from mchat.services.persona_service import PersonaService
        svc = PersonaService(db, config, conv.id)
        p = svc.create_persona(provider=Provider.CLAUDE, name="Alpha")
        assert p.name == "Alpha"
        items = svc.list_items()
        assert len(items) == 1
        assert items[0].id == p.id

    def test_sequential_sort_order(self, db, config, conv):
        from mchat.services.persona_service import PersonaService
        svc = PersonaService(db, config, conv.id)
        svc.create_persona(provider=Provider.CLAUDE, name="A")
        svc.create_persona(provider=Provider.OPENAI, name="B")
        svc.create_persona(provider=Provider.GEMINI, name="C")
        orders = [p.sort_order for p in svc.list_items()]
        assert orders == [0, 1, 2]

    def test_update_persona(self, db, config, conv):
        from mchat.services.persona_service import PersonaService
        svc = PersonaService(db, config, conv.id)
        p = svc.create_persona(provider=Provider.CLAUDE, name="Bot")
        svc.update_persona(p.id, system_prompt_override="Be concise")
        updated = svc.list_items()[0]
        assert updated.system_prompt_override == "Be concise"

    def test_remove_persona(self, db, config, conv):
        from mchat.services.persona_service import PersonaService
        svc = PersonaService(db, config, conv.id)
        p = svc.create_persona(provider=Provider.CLAUDE, name="Bot")
        svc.remove_persona(p.id)
        assert svc.list_items() == []

    def test_move_up_down(self, db, config, conv):
        from mchat.services.persona_service import PersonaService
        svc = PersonaService(db, config, conv.id)
        svc.create_persona(provider=Provider.CLAUDE, name="A")
        svc.create_persona(provider=Provider.OPENAI, name="B")
        items = svc.list_items()
        svc.move_persona_down(items[0].id)
        names = [p.name for p in svc.list_items()]
        assert names == ["B", "A"]

    def test_export_import_roundtrip(self, db, config, conv):
        from mchat.services.persona_service import PersonaService
        svc = PersonaService(db, config, conv.id)
        svc.create_persona(
            provider=Provider.CLAUDE, name="Partner",
            system_prompt_override="Be kind",
        )
        md = svc.export_personas_md()
        assert "Partner" in md
        assert "Be kind" in md
        # Import into a new conversation
        conv2 = db.create_conversation()
        svc2 = PersonaService(db, config, conv2.id)
        svc2.import_personas_md(md)
        items = svc2.list_items()
        assert len(items) == 1
        assert items[0].name == "Partner"

    def test_effective_prompt(self, db, config, conv):
        from mchat.services.persona_service import PersonaService
        svc = PersonaService(db, config, conv.id)
        p = svc.create_persona(provider=Provider.CLAUDE, name="Bot")
        # No override → falls back to provider prompt
        prompt = svc.effective_prompt(p)
        assert isinstance(prompt, str)

    def test_effective_model(self, db, config, conv):
        from mchat.services.persona_service import PersonaService
        svc = PersonaService(db, config, conv.id)
        p = svc.create_persona(provider=Provider.CLAUDE, name="Bot")
        model = svc.effective_model(p)
        assert isinstance(model, str)


class TestRunsAfterField:
    """#167 — runs_after field on Persona and DB round-trip."""

    def test_persona_has_runs_after_field(self):
        from mchat.models.persona import Persona
        p = Persona(
            conversation_id=1, id="p_test", provider=Provider.CLAUDE,
            name="Bot", name_slug="bot", runs_after="p_other",
        )
        assert p.runs_after == "p_other"

    def test_runs_after_defaults_to_none(self):
        from mchat.models.persona import Persona
        p = Persona(
            conversation_id=1, id="p_test", provider=Provider.CLAUDE,
            name="Bot", name_slug="bot",
        )
        assert p.runs_after is None

    def test_create_with_runs_after(self, db, config, conv):
        from mchat.services.persona_service import PersonaService
        svc = PersonaService(db, config, conv.id)
        a = svc.create_persona(provider=Provider.CLAUDE, name="A")
        b = svc.create_persona(provider=Provider.OPENAI, name="B", runs_after=a.id)
        items = svc.list_items()
        assert items[1].runs_after == a.id

    def test_update_runs_after(self, db, config, conv):
        from mchat.services.persona_service import PersonaService
        svc = PersonaService(db, config, conv.id)
        a = svc.create_persona(provider=Provider.CLAUDE, name="A")
        b = svc.create_persona(provider=Provider.OPENAI, name="B")
        svc.update_persona(b.id, runs_after=a.id)
        updated = svc.list_items()[1]
        assert updated.runs_after == a.id

    def test_clear_runs_after(self, db, config, conv):
        from mchat.services.persona_service import PersonaService
        svc = PersonaService(db, config, conv.id)
        a = svc.create_persona(provider=Provider.CLAUDE, name="A")
        b = svc.create_persona(provider=Provider.OPENAI, name="B", runs_after=a.id)
        svc.update_persona(b.id, runs_after=None)
        updated = svc.list_items()[1]
        assert updated.runs_after is None

    def test_db_roundtrip(self, db, config, conv):
        from mchat.services.persona_service import PersonaService
        svc = PersonaService(db, config, conv.id)
        a = svc.create_persona(provider=Provider.CLAUDE, name="A")
        b = svc.create_persona(provider=Provider.OPENAI, name="B", runs_after=a.id)
        # Re-read from DB directly
        personas = db.list_personas(conv.id)
        assert personas[1].runs_after == a.id

    def test_migration_preserves_null(self, db, config, conv):
        """Existing personas (created before migration) get runs_after=NULL."""
        from mchat.services.persona_service import PersonaService
        svc = PersonaService(db, config, conv.id)
        p = svc.create_persona(provider=Provider.CLAUDE, name="Legacy")
        assert p.runs_after is None


class TestValidateDag:
    """#167 — validate_dag cycle detection and constraint checking."""

    def test_empty_list_is_valid(self):
        from mchat.services.persona_service import validate_dag
        assert validate_dag([]) == []

    def test_all_roots_is_valid(self):
        from mchat.services.persona_service import validate_dag
        from mchat.models.persona import Persona
        personas = [
            Persona(conversation_id=1, id="a", provider=Provider.CLAUDE, name="A", name_slug="a"),
            Persona(conversation_id=1, id="b", provider=Provider.OPENAI, name="B", name_slug="b"),
        ]
        assert validate_dag(personas) == []

    def test_valid_chain(self):
        from mchat.services.persona_service import validate_dag
        from mchat.models.persona import Persona
        personas = [
            Persona(conversation_id=1, id="a", provider=Provider.CLAUDE, name="A", name_slug="a"),
            Persona(conversation_id=1, id="b", provider=Provider.OPENAI, name="B", name_slug="b", runs_after="a"),
            Persona(conversation_id=1, id="c", provider=Provider.GEMINI, name="C", name_slug="c", runs_after="b"),
        ]
        assert validate_dag(personas) == []

    def test_valid_forest(self):
        from mchat.services.persona_service import validate_dag
        from mchat.models.persona import Persona
        personas = [
            Persona(conversation_id=1, id="a", provider=Provider.CLAUDE, name="A", name_slug="a"),
            Persona(conversation_id=1, id="b", provider=Provider.OPENAI, name="B", name_slug="b"),
            Persona(conversation_id=1, id="c", provider=Provider.GEMINI, name="C", name_slug="c", runs_after="a"),
            Persona(conversation_id=1, id="d", provider=Provider.MISTRAL, name="D", name_slug="d", runs_after="b"),
        ]
        assert validate_dag(personas) == []

    def test_cycle_detected(self):
        from mchat.services.persona_service import validate_dag
        from mchat.models.persona import Persona
        # A→B→C→A with one root to isolate cycle detection from no-root check
        personas = [
            Persona(conversation_id=1, id="r", provider=Provider.CLAUDE, name="Root", name_slug="root"),
            Persona(conversation_id=1, id="a", provider=Provider.CLAUDE, name="A", name_slug="a", runs_after="c"),
            Persona(conversation_id=1, id="b", provider=Provider.OPENAI, name="B", name_slug="b", runs_after="a"),
            Persona(conversation_id=1, id="c", provider=Provider.GEMINI, name="C", name_slug="c", runs_after="b"),
        ]
        errors = validate_dag(personas)
        assert len(errors) > 0
        assert any("cycle" in e.lower() for e in errors)

    def test_self_reference_detected(self):
        from mchat.services.persona_service import validate_dag
        from mchat.models.persona import Persona
        personas = [
            Persona(conversation_id=1, id="a", provider=Provider.CLAUDE, name="A", name_slug="a", runs_after="a"),
        ]
        errors = validate_dag(personas)
        assert len(errors) > 0

    def test_no_root_detected(self):
        from mchat.services.persona_service import validate_dag
        from mchat.models.persona import Persona
        personas = [
            Persona(conversation_id=1, id="a", provider=Provider.CLAUDE, name="A", name_slug="a", runs_after="b"),
            Persona(conversation_id=1, id="b", provider=Provider.OPENAI, name="B", name_slug="b", runs_after="a"),
        ]
        errors = validate_dag(personas)
        assert any("root" in e.lower() or "cycle" in e.lower() for e in errors)

    def test_dangling_reference_detected(self):
        from mchat.services.persona_service import validate_dag
        from mchat.models.persona import Persona
        personas = [
            Persona(conversation_id=1, id="a", provider=Provider.CLAUDE, name="A", name_slug="a", runs_after="nonexistent"),
        ]
        errors = validate_dag(personas)
        assert len(errors) > 0


class TestGetAncestorPersonaIds:
    """#167 — get_ancestor_persona_ids returns the ancestor chain."""

    def test_root_has_no_ancestors(self):
        from mchat.services.persona_service import get_ancestor_persona_ids
        from mchat.models.persona import Persona
        personas = [
            Persona(conversation_id=1, id="a", provider=Provider.CLAUDE, name="A", name_slug="a"),
        ]
        assert get_ancestor_persona_ids("a", personas) == set()

    def test_child_has_parent(self):
        from mchat.services.persona_service import get_ancestor_persona_ids
        from mchat.models.persona import Persona
        personas = [
            Persona(conversation_id=1, id="a", provider=Provider.CLAUDE, name="A", name_slug="a"),
            Persona(conversation_id=1, id="b", provider=Provider.OPENAI, name="B", name_slug="b", runs_after="a"),
        ]
        assert get_ancestor_persona_ids("b", personas) == {"a"}

    def test_grandchild_has_full_chain(self):
        from mchat.services.persona_service import get_ancestor_persona_ids
        from mchat.models.persona import Persona
        personas = [
            Persona(conversation_id=1, id="a", provider=Provider.CLAUDE, name="A", name_slug="a"),
            Persona(conversation_id=1, id="b", provider=Provider.OPENAI, name="B", name_slug="b", runs_after="a"),
            Persona(conversation_id=1, id="c", provider=Provider.GEMINI, name="C", name_slug="c", runs_after="b"),
        ]
        assert get_ancestor_persona_ids("c", personas) == {"a", "b"}


class TestRemoveClearsDependents:
    """#167 — removing a persona clears dependents' runs_after."""

    def test_remove_clears_child_runs_after(self, db, config, conv):
        from mchat.services.persona_service import PersonaService
        svc = PersonaService(db, config, conv.id)
        a = svc.create_persona(provider=Provider.CLAUDE, name="A")
        b = svc.create_persona(provider=Provider.OPENAI, name="B", runs_after=a.id)
        svc.remove_persona(a.id)
        # B should now be a root (runs_after cleared)
        remaining = svc.list_items()
        assert len(remaining) == 1
        assert remaining[0].id == b.id
        assert remaining[0].runs_after is None


class TestExportImportRunsAfter:
    """#167 — export/import preserves runs_after relationships."""

    def test_export_includes_runs_after(self, db, config, conv):
        from mchat.services.persona_service import PersonaService
        svc = PersonaService(db, config, conv.id)
        a = svc.create_persona(provider=Provider.CLAUDE, name="Critic")
        b = svc.create_persona(provider=Provider.OPENAI, name="Partner", runs_after=a.id)
        md = svc.export_personas_md()
        assert "Runs after: Critic" in md

    def test_export_root_shows_prompt(self, db, config, conv):
        from mchat.services.persona_service import PersonaService
        svc = PersonaService(db, config, conv.id)
        svc.create_persona(provider=Provider.CLAUDE, name="Critic")
        md = svc.export_personas_md()
        assert "Runs after: (prompt)" in md

    def test_import_roundtrip_preserves_dag(self, db, config, conv):
        from mchat.services.persona_service import PersonaService
        svc = PersonaService(db, config, conv.id)
        a = svc.create_persona(provider=Provider.CLAUDE, name="Critic")
        svc.create_persona(provider=Provider.OPENAI, name="Partner", runs_after=a.id)
        md = svc.export_personas_md()

        # Import into a new conversation
        conv2 = db.create_conversation()
        svc2 = PersonaService(db, config, conv2.id)
        svc2.import_personas_md(md)
        items = svc2.list_items()
        assert len(items) == 2
        critic = next(p for p in items if p.name == "Critic")
        partner = next(p for p in items if p.name == "Partner")
        assert critic.runs_after is None
        assert partner.runs_after == critic.id
