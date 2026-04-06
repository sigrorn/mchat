# ------------------------------------------------------------------
# Component: test_edit_command
# Responsibility: Tests for //edit [n] — edit and replay user messages.
# Collaborators: ui.commands.history, db, send_controller
# ------------------------------------------------------------------
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from mchat.config import Config
from mchat.db import Database
from mchat.models.message import Message, Provider, Role


@pytest.fixture
def db(tmp_path):
    d = Database(db_path=tmp_path / "edit.db")
    yield d
    d.close()


@pytest.fixture
def config(tmp_path):
    return Config(config_path=tmp_path / "cfg.json")


def _build_host(db):
    """Build a minimal CommandHost mock for edit tests."""
    h = MagicMock()
    h._db = db
    conv = db.create_conversation()
    h._current_conv = conv
    h._current_conv.messages = []
    h._chat.notes = []
    h._chat.add_note = lambda text: h._chat.notes.append(text)
    h._display_messages = MagicMock()
    h._on_new_chat = MagicMock()
    return h


def _add_user_msg(db, conv, text, addressed_to="claude"):
    msg = Message(
        role=Role.USER, content=text,
        conversation_id=conv.id, addressed_to=addressed_to,
    )
    db.add_message(msg)
    conv.messages.append(msg)
    # Refresh to get the id
    conv.messages[-1] = db.get_messages(conv.id)[-1]
    return conv.messages[-1]


def _add_asst_msg(db, conv, text, provider=Provider.CLAUDE, persona_id="claude"):
    msg = Message(
        role=Role.ASSISTANT, content=text,
        provider=provider, persona_id=persona_id,
        conversation_id=conv.id,
    )
    db.add_message(msg)
    conv.messages.append(msg)
    conv.messages[-1] = db.get_messages(conv.id)[-1]
    return conv.messages[-1]


class TestEditParsing:
    """//edit with various argument forms."""

    def test_edit_no_arg_loads_last_user_message(self, db):
        host = _build_host(db)
        _add_user_msg(db, host._current_conv, "first question")
        _add_asst_msg(db, host._current_conv, "answer")
        _add_user_msg(db, host._current_conv, "second question")
        _add_asst_msg(db, host._current_conv, "answer 2")

        from mchat.ui.commands.history import handle_edit
        handle_edit("", host)
        # Edit state should target the last user message
        assert host._edit_state is not None
        assert host._edit_state["original_msg"].content == "second question"

    def test_edit_absolute_number(self, db):
        host = _build_host(db)
        _add_user_msg(db, host._current_conv, "first")
        _add_asst_msg(db, host._current_conv, "reply")
        _add_user_msg(db, host._current_conv, "second")

        from mchat.ui.commands.history import handle_edit
        handle_edit("1", host)
        assert host._edit_state is not None
        assert host._edit_state["original_msg"].content == "first"

    def test_edit_negative_offset(self, db):
        """//edit -2 loads the 2nd-last user message."""
        host = _build_host(db)
        _add_user_msg(db, host._current_conv, "first")
        _add_asst_msg(db, host._current_conv, "reply")
        _add_user_msg(db, host._current_conv, "second")
        _add_asst_msg(db, host._current_conv, "reply 2")
        _add_user_msg(db, host._current_conv, "third")

        from mchat.ui.commands.history import handle_edit
        handle_edit("-2", host)
        assert host._edit_state is not None
        assert host._edit_state["original_msg"].content == "second"

    def test_edit_negative_too_large_errors(self, db):
        """//edit -5 with only 2 user messages → error."""
        host = _build_host(db)
        _add_user_msg(db, host._current_conv, "first")
        _add_asst_msg(db, host._current_conv, "reply")
        _add_user_msg(db, host._current_conv, "second")

        from mchat.ui.commands.history import handle_edit
        handle_edit("-5", host)
        assert any("out of range" in n.lower() or "not enough" in n.lower()
                    for n in host._chat.notes)

    def test_edit_out_of_range_errors(self, db):
        host = _build_host(db)
        _add_user_msg(db, host._current_conv, "only one")

        from mchat.ui.commands.history import handle_edit
        handle_edit("999", host)
        assert any("out of range" in n.lower() for n in host._chat.notes)

    def test_edit_assistant_message_errors(self, db):
        host = _build_host(db)
        _add_user_msg(db, host._current_conv, "question")
        _add_asst_msg(db, host._current_conv, "answer")

        from mchat.ui.commands.history import handle_edit
        handle_edit("2", host)
        assert any("not a user message" in n.lower() for n in host._chat.notes)

    def test_edit_no_messages_errors(self, db):
        host = _build_host(db)

        from mchat.ui.commands.history import handle_edit
        handle_edit("", host)
        assert any("no user message" in n.lower() for n in host._chat.notes)


class TestEditHidesOldResponses:
    """After //edit, the assistant responses following the edited
    message should be hidden (not deleted)."""

    def test_responses_after_edited_msg_are_hidden(self, db):
        host = _build_host(db)
        _add_user_msg(db, host._current_conv, "question")
        asst = _add_asst_msg(db, host._current_conv, "old answer")

        from mchat.ui.commands.history import handle_edit
        handle_edit("1", host)

        # The assistant message should be hidden in DB
        all_msgs = db.get_messages(host._current_conv.id, include_hidden=True)
        visible = db.get_messages(host._current_conv.id, include_hidden=False)
        hidden_ids = {m.id for m in all_msgs} - {m.id for m in visible}
        assert asst.id in hidden_ids
