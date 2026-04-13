# ------------------------------------------------------------------
# Component: test_title_generator
# Responsibility: Tests for the extracted TitleGenerator — auto-title
#                 state and methods formerly living on SendController.
# Collaborators: ui.title_generator, db, config
# ------------------------------------------------------------------
from __future__ import annotations

import pytest

from mchat.config import Config
from mchat.db import Database
from mchat.models.message import Provider


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


class TestTitleGeneratorExtracted:
    """#159 — TitleGenerator is a standalone class extracted from SendController."""

    def test_module_importable(self):
        from mchat.ui.title_generator import TitleGenerator
        assert TitleGenerator is not None

    def test_should_generate_title_for_new_chat(self, db):
        from mchat.ui.title_generator import TitleGenerator
        tg = TitleGenerator(db=db, session=None, sidebar=None)
        conv = db.create_conversation()
        assert tg.should_generate_title(conv.id)

    def test_should_not_generate_after_attempted(self, db):
        from mchat.ui.title_generator import TitleGenerator
        tg = TitleGenerator(db=db, session=None, sidebar=None)
        conv = db.create_conversation()
        tg.mark_attempted(conv.id)
        assert not tg.should_generate_title(conv.id)

    def test_should_not_generate_after_user_rename(self, db):
        from mchat.ui.title_generator import TitleGenerator
        tg = TitleGenerator(db=db, session=None, sidebar=None)
        conv = db.create_conversation()
        db.update_conversation_title(conv.id, "user-set")
        assert not tg.should_generate_title(conv.id)

    def test_should_generate_if_title_is_fallback(self, db):
        from mchat.ui.title_generator import TitleGenerator
        tg = TitleGenerator(db=db, session=None, sidebar=None)
        conv = db.create_conversation()
        fallback = "explain quicksort to me in detail"
        db.update_conversation_title(conv.id, fallback)
        tg.set_fallback_title(conv.id, fallback)
        assert tg.should_generate_title(conv.id)

    def test_set_and_clear_fallback(self, db):
        from mchat.ui.title_generator import TitleGenerator
        tg = TitleGenerator(db=db, session=None, sidebar=None)
        conv = db.create_conversation()
        tg.set_fallback_title(conv.id, "fallback text")
        assert tg._fallback_title_by_conv[conv.id] == "fallback text"
        tg.clear_fallback(conv.id)
        assert conv.id not in tg._fallback_title_by_conv

    def test_stop_all_workers_on_empty(self):
        """stop_all_workers must not crash when there are no workers."""
        from mchat.ui.title_generator import TitleGenerator
        tg = TitleGenerator(db=None, session=None, sidebar=None)
        tg.stop_all_workers()  # must not raise
