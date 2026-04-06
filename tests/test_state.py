# ------------------------------------------------------------------
# Component: test_state
# Responsibility: Tests for the application-state objects
#                 (ConversationSession, SelectionState, ModelCatalog)
#                 introduced to replace MainWindow-as-service-locator.
#                 Uses qtbot.waitSignal to exercise the Qt signal
#                 surface. SelectionState was renamed from
#                 ProviderSelectionState in Stage 2.4 of the personas
#                 feature and now holds list[PersonaTarget].
# Collaborators: ui.state, models, PySide6
# ------------------------------------------------------------------
from __future__ import annotations

import pytest

from mchat.models.conversation import Conversation
from mchat.models.message import Message, Provider, Role
from mchat.ui.persona_target import PersonaTarget, synthetic_default
from mchat.ui.state import ConversationSession, ModelCatalog, SelectionState


# pytest-qt provides qtbot; we need a QApplication via qtbot.


class TestConversationSession:
    def test_empty_initially(self, qtbot):
        s = ConversationSession()
        assert s.current is None
        assert s.is_active() is False
        assert s.messages == []

    def test_set_current_fires_conversation_changed(self, qtbot):
        s = ConversationSession()
        conv = Conversation(id=1, title="t")
        with qtbot.waitSignal(s.conversation_changed, timeout=500) as blocker:
            s.set_current(conv)
        assert blocker.args[0] is conv
        assert s.current is conv

    def test_set_current_with_messages_replaces_list(self, qtbot):
        s = ConversationSession()
        conv = Conversation(id=1)
        msgs = [Message(role=Role.USER, content="hi", conversation_id=1)]
        s.set_current(conv, messages=msgs)
        assert s.messages == msgs

    def test_append_message_emits_messages_changed(self, qtbot):
        s = ConversationSession()
        conv = Conversation(id=1)
        s.set_current(conv, messages=[])
        with qtbot.waitSignal(s.messages_changed, timeout=500):
            s.append_message(Message(role=Role.USER, content="hi", conversation_id=1))
        assert len(s.messages) == 1

    def test_append_on_empty_session_noop(self, qtbot):
        s = ConversationSession()
        # Should not raise, should not emit.
        s.append_message(Message(role=Role.USER, content="x"))
        assert s.messages == []

    def test_set_title_emits(self, qtbot):
        s = ConversationSession()
        s.set_current(Conversation(id=1, title="old"))
        with qtbot.waitSignal(s.title_changed, timeout=500) as blocker:
            s.set_title("new")
        assert blocker.args[0] == "new"
        assert s.current.title == "new"

    def test_set_limit_mark_emits_messages_changed(self, qtbot):
        s = ConversationSession()
        s.set_current(Conversation(id=1))
        with qtbot.waitSignal(s.messages_changed, timeout=500):
            s.set_limit_mark("#3")
        assert s.current.limit_mark == "#3"

    def test_clear_fires_with_none(self, qtbot):
        s = ConversationSession()
        s.set_current(Conversation(id=1))
        with qtbot.waitSignal(s.conversation_changed, timeout=500) as blocker:
            s.clear()
        assert blocker.args[0] is None
        assert s.current is None


class TestSelectionState:
    """Stage 2.4 — selection state now holds list[PersonaTarget]
    instead of list[Provider]. Synthetic-default targets are the
    direct replacement for bare providers.
    """

    def _claude(self) -> PersonaTarget:
        return synthetic_default(Provider.CLAUDE)

    def _openai(self) -> PersonaTarget:
        return synthetic_default(Provider.OPENAI)

    def _gemini(self) -> PersonaTarget:
        return synthetic_default(Provider.GEMINI)

    def test_initial_empty(self, qtbot):
        s = SelectionState()
        assert s.selection == []
        assert s.providers_only() == []

    def test_initial_with_default(self, qtbot):
        s = SelectionState([self._claude()])
        assert s.selection == [self._claude()]
        assert s.providers_only() == [Provider.CLAUDE]

    def test_set_emits_when_changed(self, qtbot):
        s = SelectionState([self._claude()])
        with qtbot.waitSignal(s.selection_changed, timeout=500) as blocker:
            s.set([self._openai(), self._gemini()])
        assert blocker.args[0] == [self._openai(), self._gemini()]
        assert s.selection == [self._openai(), self._gemini()]
        assert s.providers_only() == [Provider.OPENAI, Provider.GEMINI]

    def test_set_noop_on_equal_value(self, qtbot):
        s = SelectionState([self._claude()])
        received = []
        s.selection_changed.connect(lambda v: received.append(v))
        s.set([self._claude()])
        assert received == []  # identical set → no signal

    def test_set_empty_clears_selection(self, qtbot):
        """Stage 3A.4 — set([]) must write through so new chats can
        start with zero providers selected."""
        s = SelectionState([self._claude()])
        with qtbot.waitSignal(s.selection_changed, timeout=500) as blocker:
            s.set([])
        assert blocker.args[0] == []
        assert s.selection == []

    def test_selection_returns_copy_not_ref(self, qtbot):
        s = SelectionState([self._claude()])
        got = s.selection
        got.append(self._openai())
        assert s.selection == [self._claude()]  # untouched

    def test_providers_only_deduplicates(self, qtbot):
        """Two PersonaTargets sharing a provider should collapse to
        one entry in providers_only — Router's public .selection
        preserves list[Provider] semantics via this dedup."""
        from mchat.models.persona import generate_persona_id
        # Two distinct explicit-persona targets both backed by Claude
        t1 = PersonaTarget(persona_id=generate_persona_id(), provider=Provider.CLAUDE)
        t2 = PersonaTarget(persona_id=generate_persona_id(), provider=Provider.CLAUDE)
        s = SelectionState([t1, t2, self._openai()])
        assert s.providers_only() == [Provider.CLAUDE, Provider.OPENAI]


class TestModelCatalog:
    def test_empty_initially(self, qtbot):
        c = ModelCatalog()
        assert c.get(Provider.CLAUDE) == []
        assert c.all() == {}

    def test_set_emits(self, qtbot):
        c = ModelCatalog()
        with qtbot.waitSignal(c.models_changed, timeout=500) as blocker:
            c.set(Provider.CLAUDE, ["sonnet", "opus"])
        assert blocker.args[0] == Provider.CLAUDE
        assert c.get(Provider.CLAUDE) == ["sonnet", "opus"]

    def test_set_noop_on_equal(self, qtbot):
        c = ModelCatalog()
        c.set(Provider.CLAUDE, ["a"])
        received = []
        c.models_changed.connect(lambda p: received.append(p))
        c.set(Provider.CLAUDE, ["a"])
        assert received == []

    def test_get_returns_copy(self, qtbot):
        c = ModelCatalog()
        c.set(Provider.CLAUDE, ["a"])
        got = c.get(Provider.CLAUDE)
        got.append("b")
        assert c.get(Provider.CLAUDE) == ["a"]

    def test_all_returns_independent_snapshot(self, qtbot):
        c = ModelCatalog()
        c.set(Provider.CLAUDE, ["a"])
        snap = c.all()
        snap[Provider.CLAUDE].append("b")
        snap[Provider.OPENAI] = ["x"]
        assert c.get(Provider.CLAUDE) == ["a"]
        assert c.get(Provider.OPENAI) == []
