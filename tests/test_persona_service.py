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
