# ------------------------------------------------------------------
# Component: test_visibility_command
# Responsibility: Tests for //visibility separated|joined command
# Collaborators: ui.commands, db
# ------------------------------------------------------------------
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from mchat.config import Config
from mchat.db import Database
from mchat.models.message import Provider


@pytest.fixture
def db(tmp_path):
    d = Database(db_path=tmp_path / "vis.db")
    yield d
    d.close()


def _build_host(db):
    h = MagicMock()
    h._db = db
    conv = db.create_conversation()
    h._current_conv = conv
    h._current_conv.messages = []
    h._current_conv.visibility_matrix = {}
    h._chat.notes = []
    h._chat.add_note = lambda text: h._chat.notes.append(text)
    h._display_messages = MagicMock()
    h._sync_matrix_panel = MagicMock()
    return h


class TestVisibilitySeparated:
    def test_separated_sets_empty_allowlists(self, db):
        from mchat.models.persona import Persona, generate_persona_id
        host = _build_host(db)
        conv = host._current_conv
        # Create two personas
        db.create_persona(Persona(
            conversation_id=conv.id, id=generate_persona_id(),
            provider=Provider.CLAUDE, name="A", name_slug="a",
        ))
        db.create_persona(Persona(
            conversation_id=conv.id, id=generate_persona_id(),
            provider=Provider.OPENAI, name="B", name_slug="b",
        ))
        from mchat.ui.commands.selection import handle_visibility
        handle_visibility("separated", host)
        matrix = conv.visibility_matrix
        personas = db.list_personas(conv.id)
        for p in personas:
            assert p.id in matrix
            assert matrix[p.id] == []


class TestVisibilityJoined:
    def test_joined_clears_matrix(self, db):
        from mchat.models.persona import Persona, generate_persona_id
        host = _build_host(db)
        conv = host._current_conv
        p1 = db.create_persona(Persona(
            conversation_id=conv.id, id=generate_persona_id(),
            provider=Provider.CLAUDE, name="A", name_slug="a",
        ))
        # Start with a restrictive matrix
        conv.visibility_matrix = {p1.id: []}
        from mchat.ui.commands.selection import handle_visibility
        handle_visibility("joined", host)
        assert conv.visibility_matrix == {}


class TestVisibilityNoArg:
    def test_no_arg_shows_error(self, db):
        host = _build_host(db)
        from mchat.ui.commands.selection import handle_visibility
        handle_visibility("", host)
        assert any("separated" in n.lower() or "joined" in n.lower()
                    for n in host._chat.notes)
