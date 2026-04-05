# ------------------------------------------------------------------
# Component: test_db
# Responsibility: Tests for database persistence
# Collaborators: db, models
# ------------------------------------------------------------------
from __future__ import annotations

from pathlib import Path

import pytest

from mchat.db import Database
from mchat.models.message import Message, Provider, Role


@pytest.fixture
def db(tmp_path):
    database = Database(db_path=tmp_path / "test.db")
    yield database
    database.close()


class TestDatabase:
    def test_create_and_list_conversations(self, db):
        conv = db.create_conversation("Test Chat")
        convs = db.list_conversations()
        assert len(convs) == 1
        assert convs[0].title == "Test Chat"
        assert convs[0].id == conv.id

    def test_get_conversation(self, db):
        conv = db.create_conversation("Test Chat")
        fetched = db.get_conversation(conv.id)
        assert fetched is not None
        assert fetched.title == "Test Chat"
        assert fetched.id == conv.id

    def test_get_conversation_missing(self, db):
        assert db.get_conversation(9999) is None

    def test_delete_conversation(self, db):
        conv = db.create_conversation("To Delete")
        db.delete_conversation(conv.id)
        assert len(db.list_conversations()) == 0

    def test_add_and_get_messages(self, db):
        conv = db.create_conversation()
        msg = Message(
            role=Role.USER,
            content="Hello",
            conversation_id=conv.id,
        )
        saved = db.add_message(msg)
        assert saved.id is not None

        messages = db.get_messages(conv.id)
        assert len(messages) == 1
        assert messages[0].content == "Hello"
        assert messages[0].role == Role.USER

    def test_message_with_provider(self, db):
        conv = db.create_conversation()
        msg = Message(
            role=Role.ASSISTANT,
            content="Hi there",
            provider=Provider.CLAUDE,
            model="claude-sonnet-4-20250514",
            conversation_id=conv.id,
        )
        db.add_message(msg)

        messages = db.get_messages(conv.id)
        assert messages[0].provider == Provider.CLAUDE
        assert messages[0].model == "claude-sonnet-4-20250514"

    def test_update_conversation_title(self, db):
        conv = db.create_conversation("Old Title")
        db.update_conversation_title(conv.id, "New Title")
        convs = db.list_conversations()
        assert convs[0].title == "New Title"

    def test_cascade_delete(self, db):
        conv = db.create_conversation()
        db.add_message(Message(role=Role.USER, content="test", conversation_id=conv.id))
        db.delete_conversation(conv.id)
        assert db.get_messages(conv.id) == []


class TestConversationSpend:
    def test_add_and_get_spend(self, db):
        conv = db.create_conversation()
        db.add_conversation_spend(conv.id, "claude", 0.005)
        db.add_conversation_spend(conv.id, "claude", 0.003)
        db.add_conversation_spend(conv.id, "openai", 0.010)

        spend = db.get_conversation_spend(conv.id)
        assert abs(spend["claude"][0] - 0.008) < 1e-9
        assert spend["claude"][1] is False
        assert abs(spend["openai"][0] - 0.010) < 1e-9

    def test_spend_defaults_to_zero(self, db):
        conv = db.create_conversation()
        spend = db.get_conversation_spend(conv.id)
        assert spend == {}

    def test_spend_for_new_providers(self, db):
        conv = db.create_conversation()
        db.add_conversation_spend(conv.id, "gemini", 0.002, estimated=True)
        db.add_conversation_spend(conv.id, "perplexity", 0.001)
        spend = db.get_conversation_spend(conv.id)
        assert abs(spend["gemini"][0] - 0.002) < 1e-9
        assert spend["gemini"][1] is True
        assert abs(spend["perplexity"][0] - 0.001) < 1e-9
        assert spend["perplexity"][1] is False

    def test_estimated_flag_sticky(self, db):
        """Once any contribution is estimated, the flag stays True."""
        conv = db.create_conversation()
        db.add_conversation_spend(conv.id, "gemini", 0.001, estimated=False)
        db.add_conversation_spend(conv.id, "gemini", 0.002, estimated=True)
        spend = db.get_conversation_spend(conv.id)
        assert spend["gemini"][1] is True

    def test_spend_deleted_with_conversation(self, db):
        conv = db.create_conversation()
        db.add_conversation_spend(conv.id, "claude", 0.005)
        db.delete_conversation(conv.id)
        spend = db.get_conversation_spend(conv.id)
        assert spend == {}


class TestDeleteMessages:
    def test_delete_messages_by_ids(self, db):
        conv = db.create_conversation()
        m1 = db.add_message(Message(role=Role.USER, content="q1", conversation_id=conv.id))
        m2 = db.add_message(Message(role=Role.ASSISTANT, content="a1", provider=Provider.CLAUDE, conversation_id=conv.id))
        m3 = db.add_message(Message(role=Role.USER, content="q2", conversation_id=conv.id))

        db.delete_messages([m2.id, m3.id])
        remaining = db.get_messages(conv.id)
        assert len(remaining) == 1
        assert remaining[0].content == "q1"

    def test_delete_empty_list(self, db):
        conv = db.create_conversation()
        db.add_message(Message(role=Role.USER, content="q1", conversation_id=conv.id))
        db.delete_messages([])
        assert len(db.get_messages(conv.id)) == 1


class TestHideUnhide:
    def test_hide_messages(self, db):
        conv = db.create_conversation()
        m1 = db.add_message(Message(role=Role.USER, content="q1", conversation_id=conv.id))
        m2 = db.add_message(Message(role=Role.ASSISTANT, content="a1", provider=Provider.CLAUDE, conversation_id=conv.id))
        m3 = db.add_message(Message(role=Role.USER, content="q2", conversation_id=conv.id))

        db.hide_messages([m1.id, m2.id])
        visible = db.get_messages(conv.id)
        assert len(visible) == 1
        assert visible[0].content == "q2"

        all_msgs = db.get_messages(conv.id, include_hidden=True)
        assert len(all_msgs) == 3

    def test_unhide_all(self, db):
        conv = db.create_conversation()
        m1 = db.add_message(Message(role=Role.USER, content="q1", conversation_id=conv.id))
        m2 = db.add_message(Message(role=Role.ASSISTANT, content="a1", provider=Provider.CLAUDE, conversation_id=conv.id))
        db.hide_messages([m1.id, m2.id])

        db.unhide_all_messages(conv.id)
        visible = db.get_messages(conv.id)
        assert len(visible) == 2


class TestPinning:
    def test_add_pinned_message(self, db):
        conv = db.create_conversation()
        msg = Message(
            role=Role.USER,
            content="always reply in bullet points",
            conversation_id=conv.id,
            pinned=True,
            pin_target="claude",
        )
        db.add_message(msg)
        messages = db.get_messages(conv.id)
        assert len(messages) == 1
        assert messages[0].pinned is True
        assert messages[0].pin_target == "claude"

    def test_default_message_is_not_pinned(self, db):
        conv = db.create_conversation()
        db.add_message(Message(role=Role.USER, content="hi", conversation_id=conv.id))
        messages = db.get_messages(conv.id)
        assert messages[0].pinned is False
        assert messages[0].pin_target is None

    def test_set_pinned_unpins(self, db):
        conv = db.create_conversation()
        m = db.add_message(Message(
            role=Role.USER,
            content="be concise",
            conversation_id=conv.id,
            pinned=True,
            pin_target="all",
        ))
        db.set_pinned(m.id, False, None)
        messages = db.get_messages(conv.id)
        assert messages[0].pinned is False
        assert messages[0].pin_target is None

    def test_set_pinned_updates_target(self, db):
        conv = db.create_conversation()
        m = db.add_message(Message(
            role=Role.USER,
            content="rule",
            conversation_id=conv.id,
            pinned=True,
            pin_target="claude",
        ))
        db.set_pinned(m.id, True, "claude,openai")
        messages = db.get_messages(conv.id)
        assert messages[0].pinned is True
        assert messages[0].pin_target == "claude,openai"
