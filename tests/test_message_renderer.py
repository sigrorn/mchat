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

    def test_grouped_shading_uses_tracked_indices_not_value_lookup(self, renderer, chat, db):
        """Regression for #50: duplicate-valued assistant messages must
        not confuse exclusion shading for grouped responses.

        History: (a, b) round 1, (a, b) round 2 — the second pair is
        structurally identical to the first. The renderer must resolve
        the column-group's indices from its tracked position, not via
        messages.index(), so shading decisions work independently for
        each group even when their Message values are equal.
        """
        # Build equal-valued messages WITHOUT going through the DB so
        # they don't get distinguishing auto-assigned ids. This is the
        # exact condition that makes messages.index() ambiguous.
        from datetime import datetime, timezone
        fixed_ts = datetime(2026, 1, 1, tzinfo=timezone.utc)

        def _make(content, provider):
            return Message(
                role=Role.ASSISTANT, content=content,
                provider=provider, display_mode="cols",
                created_at=fixed_ts,
            )
        messages = [
            _make("a", Provider.CLAUDE),
            _make("b", Provider.OPENAI),
            _make("a", Provider.CLAUDE),
            _make("b", Provider.OPENAI),
        ]
        assert messages[0] == messages[2], "pre-req: messages must be value-equal"
        chat.set_excluded_indices({0, 1})

        # Spy on _render_column_group to capture the indices passed in.
        calls: list[list[int]] = []
        original = renderer._render_column_group

        def spy(ordered, group_indices, **kwargs):
            calls.append(list(group_indices))
            return original(ordered, group_indices, **kwargs)

        renderer._render_column_group = spy
        renderer.display_messages(
            None, messages, column_mode=True,
            configured_providers={Provider.CLAUDE, Provider.OPENAI},
        )
        # Two groups must have been rendered with disjoint index sets:
        # first group {0,1} (excluded), second group {2,3} (not excluded).
        assert len(calls) == 2
        assert set(calls[0]) == {0, 1}
        assert set(calls[1]) == {2, 3}

    def test_clear_then_rerender(self, renderer, chat):
        msgs1 = [Message(role=Role.USER, content="first")]
        msgs2 = [Message(role=Role.USER, content="second")]
        renderer.display_messages(None, msgs1, column_mode=False, configured_providers=set())
        renderer.display_messages(None, msgs2, column_mode=False, configured_providers=set())
        text = chat.toPlainText()
        assert "second" in text
        assert "first" not in text
        assert len(chat._messages) == 1


class TestPersonaAwareRendering:
    """Stage 1.3 — the renderer labels messages by persona name and
    groups by (persona_id or provider.value) so two same-provider
    personas render as distinct columns. Tombstoned personas still
    resolve their historical labels. See docs/plans/personas.md § 1.4.
    """

    def _make_persona(self, conv_id, **overrides):
        from mchat.models.persona import Persona, generate_persona_id
        fields = dict(
            conversation_id=conv_id,
            id=generate_persona_id(),
            provider=Provider.CLAUDE,
            name="Partner",
            name_slug="partner",
        )
        fields.update(overrides)
        return Persona(**fields)

    def test_message_with_persona_id_renders_persona_name_as_label(
        self, renderer, chat, db
    ):
        conv = db.create_conversation()
        p = db.create_persona(self._make_persona(
            conv.id, name="Evaluator", name_slug="evaluator",
        ))
        msgs = [
            Message(role=Role.USER, content="q", conversation_id=conv.id),
            Message(
                role=Role.ASSISTANT,
                content="hi",
                provider=Provider.CLAUDE,
                conversation_id=conv.id,
                persona_id=p.id,
            ),
            # second assistant to force multi-persona label behaviour
            Message(
                role=Role.ASSISTANT,
                content="hello",
                provider=Provider.OPENAI,
                conversation_id=conv.id,
            ),
        ]
        conv_obj = db.get_conversation(conv.id)
        conv_obj.messages = msgs
        renderer.display_messages(
            conv_obj, msgs, column_mode=False, configured_providers=set(Provider),
        )
        text = chat.toPlainText()
        # Persona name appears as the label for the persona-tagged message
        assert "Evaluator" in text

    def test_two_same_provider_personas_render_as_distinct_column_groups(
        self, renderer, chat, db
    ):
        """The grouping key changes from msg.provider to
        (msg.persona_id or msg.provider.value). Two Claude personas
        in the same group → they must form a two-column table.
        """
        conv = db.create_conversation()
        partner = db.create_persona(self._make_persona(
            conv.id, name="Partner", name_slug="partner",
        ))
        evaluator = db.create_persona(self._make_persona(
            conv.id, name="Evaluator", name_slug="evaluator",
        ))
        msgs = [
            Message(role=Role.USER, content="q", conversation_id=conv.id),
            Message(
                role=Role.ASSISTANT,
                content="partner-reply",
                provider=Provider.CLAUDE,
                conversation_id=conv.id,
                persona_id=partner.id,
                display_mode="cols",
            ),
            Message(
                role=Role.ASSISTANT,
                content="evaluator-reply",
                provider=Provider.CLAUDE,
                conversation_id=conv.id,
                persona_id=evaluator.id,
                display_mode="cols",
            ),
        ]
        conv_obj = db.get_conversation(conv.id)
        conv_obj.messages = msgs

        # Spy on _render_column_group to capture what it received.
        calls: list[list] = []
        original = renderer._render_column_group

        def spy(ordered, group_indices, **kwargs):
            calls.append([(m.persona_id, m.content) for m in ordered])
            return original(ordered, group_indices, **kwargs)

        renderer._render_column_group = spy
        renderer.display_messages(
            conv_obj, msgs, column_mode=True, configured_providers=set(Provider),
        )
        # Exactly one column group call with both personas in it
        assert len(calls) == 1
        persona_ids = {pid for pid, _ in calls[0]}
        assert persona_ids == {partner.id, evaluator.id}

    def test_legacy_messages_without_persona_id_render_unchanged(
        self, renderer, chat, db
    ):
        """A message with persona_id=None falls back to the provider
        display name. This is the regression guard for existing chats.
        """
        conv = db.create_conversation()
        msgs = [
            Message(role=Role.USER, content="q", conversation_id=conv.id),
            Message(
                role=Role.ASSISTANT,
                content="legacy",
                provider=Provider.CLAUDE,
                conversation_id=conv.id,
                # persona_id defaults to None
            ),
            Message(
                role=Role.ASSISTANT,
                content="gpt",
                provider=Provider.OPENAI,
                conversation_id=conv.id,
            ),
        ]
        conv_obj = db.get_conversation(conv.id)
        conv_obj.messages = msgs
        renderer.display_messages(
            conv_obj, msgs, column_mode=False, configured_providers=set(Provider),
        )
        text = chat.toPlainText()
        # Labels fall back to the provider display names
        assert "Claude" in text
        assert "GPT" in text

    def test_tombstoned_persona_still_labels_historical_messages(
        self, renderer, chat, db
    ):
        """After a persona is tombstoned, messages tagged with its id
        should still render with its name — the renderer reads via
        list_personas_including_deleted per the plan.
        """
        conv = db.create_conversation()
        p = db.create_persona(self._make_persona(
            conv.id, name="Removed Persona", name_slug="removed",
        ))
        msgs = [
            Message(role=Role.USER, content="q", conversation_id=conv.id),
            Message(
                role=Role.ASSISTANT,
                content="historical",
                provider=Provider.CLAUDE,
                conversation_id=conv.id,
                persona_id=p.id,
            ),
            Message(
                role=Role.ASSISTANT,
                content="other",
                provider=Provider.OPENAI,
                conversation_id=conv.id,
            ),
        ]
        db.tombstone_persona(conv.id, p.id)

        conv_obj = db.get_conversation(conv.id)
        conv_obj.messages = msgs
        renderer.display_messages(
            conv_obj, msgs, column_mode=False, configured_providers=set(Provider),
        )
        text = chat.toPlainText()
        # Historical label survives the tombstone
        assert "Removed Persona" in text

    def test_single_persona_message_no_group_still_labels(self, renderer, chat, db):
        """A solo assistant message (not a multi-provider group) with a
        persona_id should still render with the persona name. This
        exercises the single-message branch in display_messages."""
        conv = db.create_conversation()
        p = db.create_persona(self._make_persona(
            conv.id, name="SoloRole", name_slug="solorole",
        ))
        msgs = [
            Message(role=Role.USER, content="q", conversation_id=conv.id),
            Message(
                role=Role.ASSISTANT,
                content="only-reply",
                provider=Provider.CLAUDE,
                conversation_id=conv.id,
                persona_id=p.id,
            ),
        ]
        conv_obj = db.get_conversation(conv.id)
        conv_obj.messages = msgs
        renderer.display_messages(
            conv_obj, msgs, column_mode=False, configured_providers=set(Provider),
        )
        # The renderer has stored the message and it's present in text
        assert "only-reply" in chat.toPlainText()


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
