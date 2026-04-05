# ------------------------------------------------------------------
# Component: test_message_renderer
# Responsibility: pytest-qt regression tests for MessageRenderer —
#                 full re-render with multi-provider group detection,
#                 list vs column mode, and echoed-heading stripping.
# Collaborators: ui.message_renderer, ui.chat_widget, db, config
# ------------------------------------------------------------------
from __future__ import annotations

import pytest

from mchat.config import Config
from mchat.db import Database
from mchat.models.message import Message, Provider, Role
from mchat.ui.chat_widget import ChatWidget
from mchat.ui.message_renderer import (
    MessageRenderer,
    strip_echoed_heading,
)


@pytest.fixture
def db(tmp_path):
    d = Database(db_path=tmp_path / "r.db")
    yield d
    d.close()


@pytest.fixture
def config(tmp_path):
    return Config(config_path=tmp_path / "cfg.json")


@pytest.fixture
def chat(qtbot):
    widget = ChatWidget(font_size=14)
    qtbot.addWidget(widget)
    return widget


@pytest.fixture
def renderer(chat, config, db):
    return MessageRenderer(chat, config, db)


class TestStripEchoedHeading:
    def test_strips_claude_heading(self):
        assert strip_echoed_heading("**Claude's take:**\n\nbody") == "body"

    def test_strips_gpt_heading(self):
        assert strip_echoed_heading("GPT's take:\nbody") == "body"

    def test_leaves_normal_text_alone(self):
        assert strip_echoed_heading("hello world") == "hello world"

    def test_handles_case_insensitive(self):
        assert strip_echoed_heading("**claude's TAKE:**\nbody") == "body"


class TestDisplayMessages:
    def test_single_user_message(self, renderer, chat):
        msgs = [Message(role=Role.USER, content="hello")]
        renderer.display_messages(None, msgs, column_mode=False, configured_providers=set())
        assert "hello" in chat.toPlainText()
        assert len(chat._messages) == 1

    def test_single_assistant_message(self, renderer, chat):
        msgs = [
            Message(role=Role.USER, content="q"),
            Message(role=Role.ASSISTANT, content="a", provider=Provider.CLAUDE),
        ]
        renderer.display_messages(None, msgs, column_mode=False, configured_providers=set())
        text = chat.toPlainText()
        assert "q" in text
        assert "a" in text
        assert len(chat._messages) == 2

    def test_multi_provider_group_list_mode_adds_headings(self, renderer, chat):
        msgs = [
            Message(role=Role.USER, content="q"),
            Message(role=Role.ASSISTANT, content="claude-body", provider=Provider.CLAUDE),
            Message(role=Role.ASSISTANT, content="gpt-body", provider=Provider.OPENAI),
        ]
        renderer.display_messages(None, msgs, column_mode=False, configured_providers=set())
        text = chat.toPlainText()
        # List mode prepends "X's take" headings for multi-provider groups
        assert "Claude" in text
        assert "GPT" in text
        assert "claude-body" in text
        assert "gpt-body" in text

    def test_stored_display_mode_wins_over_global_toggle(self, renderer, chat):
        # Two messages stored with display_mode="lines" must render as
        # list even when the global toggle says column_mode=True.
        msgs = [
            Message(role=Role.ASSISTANT, content="a1", provider=Provider.CLAUDE, display_mode="lines"),
            Message(role=Role.ASSISTANT, content="a2", provider=Provider.OPENAI, display_mode="lines"),
        ]
        renderer.display_messages(None, msgs, column_mode=True, configured_providers=set())
        # Both bodies must be in the plain text; list mode adds headings
        text = chat.toPlainText()
        assert "a1" in text
        assert "a2" in text

    def test_echoed_heading_stripped_in_list_rendering(self, renderer, chat):
        msgs = [
            Message(
                role=Role.ASSISTANT,
                content="**Claude's take:**\n\nreal-body",
                provider=Provider.CLAUDE,
            ),
            Message(
                role=Role.ASSISTANT,
                content="gpt-body",
                provider=Provider.OPENAI,
            ),
        ]
        renderer.display_messages(None, msgs, column_mode=False, configured_providers=set())
        text = chat.toPlainText()
        # The echoed "Claude's take:" should have been stripped before
        # the renderer re-added the heading, so it must not appear twice.
        assert text.count("Claude's take") == 1
        assert "real-body" in text

    def test_clear_then_rerender(self, renderer, chat):
        msgs1 = [Message(role=Role.USER, content="first")]
        msgs2 = [Message(role=Role.USER, content="second")]
        renderer.display_messages(None, msgs1, column_mode=False, configured_providers=set())
        renderer.display_messages(None, msgs2, column_mode=False, configured_providers=set())
        text = chat.toPlainText()
        assert "second" in text
        assert "first" not in text
        assert len(chat._messages) == 1


class TestIncrementalRendering:
    def test_render_list_responses_appends_all(self, renderer, chat):
        chat.load_messages([Message(role=Role.USER, content="q")])
        responses = [
            Message(role=Role.ASSISTANT, content="r1", provider=Provider.CLAUDE, display_mode="lines"),
            Message(role=Role.ASSISTANT, content="r2", provider=Provider.OPENAI, display_mode="lines"),
        ]
        renderer.render_list_responses(responses)
        text = chat.toPlainText()
        assert "r1" in text
        assert "r2" in text
        # User message + two responses
        assert len(chat._messages) == 3

    def test_render_column_responses_appends_all(self, renderer, chat):
        chat.load_messages([Message(role=Role.USER, content="q")])
        responses = [
            Message(role=Role.ASSISTANT, content="r1", provider=Provider.CLAUDE, display_mode="cols"),
            Message(role=Role.ASSISTANT, content="r2", provider=Provider.OPENAI, display_mode="cols"),
        ]
        renderer.render_column_responses(responses)
        assert len(chat._messages) == 3
