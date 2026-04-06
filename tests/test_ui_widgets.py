# ------------------------------------------------------------------
# Component: test_ui_widgets
# Responsibility: pytest-qt regression tests for high-churn widgets
#                 (ChatWidget rendering, MatrixPanel state round-trip)
# Collaborators: ui.chat_widget, ui.matrix_panel, models.message
# ------------------------------------------------------------------
from __future__ import annotations

import pytest

from mchat.models.message import Message, Provider, Role
from mchat.ui.chat_widget import ChatWidget
from mchat.ui.matrix_panel import MatrixPanel


# pytest-qt provides the qtbot fixture which manages a QApplication.


@pytest.fixture
def chat(qtbot):
    widget = ChatWidget(font_size=14)
    qtbot.addWidget(widget)
    return widget


@pytest.fixture
def panel(qtbot):
    widget = MatrixPanel()
    qtbot.addWidget(widget)
    return widget


class TestChatWidgetRendering:
    def test_load_messages_list_mode(self, chat):
        msgs = [
            Message(role=Role.USER, content="hello"),
            Message(role=Role.ASSISTANT, content="hi there", provider=Provider.CLAUDE),
            Message(role=Role.USER, content="one more"),
        ]
        chat.load_messages(msgs)
        assert chat._messages == msgs
        # The document should contain all three message bodies.
        doc_text = chat.toPlainText()
        assert "hello" in doc_text
        assert "hi there" in doc_text
        assert "one more" in doc_text

    def test_clear_messages_resets_state(self, chat):
        chat.load_messages([Message(role=Role.USER, content="x")])
        assert chat._messages
        chat.clear_messages()
        assert chat._messages == []
        assert chat._message_positions == []
        assert chat._block_roles == {}
        assert chat._excluded_indices == set()
        assert chat._is_empty is True
        assert chat.toPlainText() == ""

    def test_add_message_appends(self, chat):
        chat.add_message(Message(role=Role.USER, content="first"))
        chat.add_message(Message(role=Role.ASSISTANT, content="second", provider=Provider.OPENAI))
        assert len(chat._messages) == 2
        doc_text = chat.toPlainText()
        assert "first" in doc_text
        assert "second" in doc_text

    def test_add_note_does_not_mutate_messages(self, chat):
        chat.add_message(Message(role=Role.USER, content="payload"))
        chat.add_note("a note")
        # Notes are ephemeral — not part of _messages
        assert len(chat._messages) == 1
        assert chat._messages[0].content == "payload"
        # But they do appear in the visual document
        assert "a note" in chat.toPlainText()

    def test_set_excluded_indices_tracked(self, chat):
        chat.load_messages([
            Message(role=Role.USER, content="a"),
            Message(role=Role.ASSISTANT, content="b", provider=Provider.CLAUDE),
            Message(role=Role.USER, content="c"),
        ])
        chat.set_excluded_indices({0, 1})
        assert chat._excluded_indices == {0, 1}

    def test_update_colors_triggers_rebuild_without_message_loss(self, chat):
        msgs = [
            Message(role=Role.USER, content="keep me"),
            Message(role=Role.ASSISTANT, content="and me", provider=Provider.GEMINI),
        ]
        chat.load_messages(msgs)
        chat.update_colors(color_user="#ffffff", color_gemini="#000000")
        # Messages survive the rebuild
        assert len(chat._messages) == 2
        doc_text = chat.toPlainText()
        assert "keep me" in doc_text
        assert "and me" in doc_text


class TestMatrixPanel:
    def test_hidden_when_fewer_than_two_providers(self, panel):
        panel.set_providers([Provider.CLAUDE])
        assert panel.isVisible() is False

    def test_visible_with_two_or_more_providers(self, panel, qtbot):
        panel.set_providers([Provider.CLAUDE, Provider.OPENAI])
        panel.show()
        qtbot.waitExposed(panel)
        # Off-diagonal checkboxes exist for both directions (string keys)
        assert ("claude", "openai") in panel._checkboxes
        assert ("openai", "claude") in panel._checkboxes
        # Diagonal exists and is disabled
        diag = panel._checkboxes[("claude", "claude")]
        assert diag.isChecked() is True
        assert diag.isEnabled() is False

    def test_load_matrix_applies_restrictions(self, panel):
        panel.set_providers([Provider.CLAUDE, Provider.OPENAI, Provider.GEMINI])
        panel.load_matrix({"openai": ["claude"]})  # openai cannot see gemini
        assert panel._checkboxes[("openai", "claude")].isChecked() is True
        assert panel._checkboxes[("openai", "gemini")].isChecked() is False
        # Other observers remain fully visible (full visibility)
        assert panel._checkboxes[("claude", "openai")].isChecked() is True
        assert panel._checkboxes[("claude", "gemini")].isChecked() is True

    def test_to_matrix_omits_full_visibility(self, panel):
        panel.set_providers([Provider.CLAUDE, Provider.OPENAI, Provider.GEMINI])
        panel.load_matrix({"openai": ["claude"]})
        result = panel.to_matrix()
        assert "openai" in result
        assert result["openai"] == ["claude"]
        # Full-visibility observers are not stored
        assert "claude" not in result
        assert "gemini" not in result

    def test_roundtrip_load_then_to_matrix(self, panel):
        panel.set_providers([Provider.CLAUDE, Provider.OPENAI, Provider.GEMINI])
        original = {"openai": ["claude"], "gemini": []}
        panel.load_matrix(original)
        result = panel.to_matrix()
        # Gemini with empty allowlist stays empty (no external sources)
        assert result.get("gemini") == []
        assert result.get("openai") == ["claude"]

    def test_state_cached_across_rebuild(self, panel):
        # Start with all providers, uncheck claude->openai
        panel.set_providers(list(Provider))
        panel._checkboxes[("claude", "openai")].setChecked(False)
        # Remove perplexity (simulating API key removal) and put it back
        panel.set_providers([Provider.CLAUDE, Provider.OPENAI, Provider.GEMINI])
        panel.set_providers(list(Provider))
        # The claude->openai restriction must have survived the rebuild
        assert panel._checkboxes[("claude", "openai")].isChecked() is False

    def test_toggle_emits_signal(self, panel, qtbot):
        panel.set_providers([Provider.CLAUDE, Provider.OPENAI])
        with qtbot.waitSignal(panel.matrix_changed, timeout=1000) as blocker:
            panel._checkboxes[("claude", "openai")].setChecked(False)
        emitted_matrix = blocker.args[0]
        assert "claude" in emitted_matrix
        assert "openai" not in emitted_matrix["claude"]
