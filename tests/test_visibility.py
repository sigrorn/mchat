# ------------------------------------------------------------------
# Component: test_visibility
# Responsibility: Tests for the per-provider visibility filter applied
#                 to message contexts built for a given target provider
# Collaborators: ui.visibility, models.message
# ------------------------------------------------------------------
from __future__ import annotations

from mchat.models.message import Message, Provider, Role
from mchat.ui.visibility import filter_for_provider


def _user(text: str, addressed: str | None) -> Message:
    return Message(role=Role.USER, content=text, addressed_to=addressed)


def _asst(text: str, provider: Provider) -> Message:
    return Message(role=Role.ASSISTANT, content=text, provider=provider)


class TestUserMessageAddressing:
    def test_legacy_user_message_visible_to_all(self):
        msgs = [_user("legacy", None)]
        for p in Provider:
            out = filter_for_provider(msgs, p, matrix={})
            assert out == msgs, f"legacy should be visible to {p}"

    def test_all_addressed_visible_to_all(self):
        msgs = [_user("broadcast", "all")]
        for p in Provider:
            out = filter_for_provider(msgs, p, matrix={})
            assert out == msgs

    def test_addressed_only_visible_to_target(self):
        msgs = [_user("hi claude", "claude")]
        assert filter_for_provider(msgs, Provider.CLAUDE, matrix={}) == msgs
        assert filter_for_provider(msgs, Provider.OPENAI, matrix={}) == []
        assert filter_for_provider(msgs, Provider.GEMINI, matrix={}) == []

    def test_multi_addressed(self):
        msgs = [_user("to two", "claude,openai")]
        assert filter_for_provider(msgs, Provider.CLAUDE, matrix={}) == msgs
        assert filter_for_provider(msgs, Provider.OPENAI, matrix={}) == msgs
        assert filter_for_provider(msgs, Provider.GEMINI, matrix={}) == []


class TestAssistantVisibilityMatrix:
    def test_empty_matrix_means_full_visibility(self):
        msgs = [
            _asst("claude reply", Provider.CLAUDE),
            _asst("gpt reply", Provider.OPENAI),
        ]
        out = filter_for_provider(msgs, Provider.GEMINI, matrix={})
        assert out == msgs

    def test_observer_missing_means_full_visibility(self):
        # Only openai has a restriction; gemini inherits full
        msgs = [_asst("claude reply", Provider.CLAUDE)]
        matrix = {"openai": []}
        assert filter_for_provider(msgs, Provider.GEMINI, matrix=matrix) == msgs

    def test_observer_sees_self_regardless_of_matrix(self):
        msgs = [_asst("claude reply", Provider.CLAUDE)]
        # Claude has an empty allowlist but must still see its own responses
        matrix = {"claude": []}
        assert filter_for_provider(msgs, Provider.CLAUDE, matrix=matrix) == msgs

    def test_observer_sees_only_allowed_sources(self):
        msgs = [
            _asst("claude", Provider.CLAUDE),
            _asst("gpt", Provider.OPENAI),
            _asst("gemini", Provider.GEMINI),
        ]
        # openai allowlist: only claude
        matrix = {"openai": ["claude"]}
        out = filter_for_provider(msgs, Provider.OPENAI, matrix=matrix)
        contents = [m.content for m in out]
        # openai sees claude (allowed) and gpt (itself); not gemini
        assert "claude" in contents
        assert "gpt" in contents
        assert "gemini" not in contents


class TestCombined:
    def test_user_addressing_and_matrix_both_apply(self):
        msgs = [
            _user("only-for-claude", "claude"),
            _asst("claude reply", Provider.CLAUDE),
            _asst("gpt reply", Provider.OPENAI),
        ]
        # gemini's allowlist excludes openai; user message was not addressed to gemini
        matrix = {"gemini": ["claude"]}
        out = filter_for_provider(msgs, Provider.GEMINI, matrix=matrix)
        # gemini sees: claude's reply (allowed), not gpt (excluded), not user msg
        assert len(out) == 1
        assert out[0].content == "claude reply"

    def test_system_messages_always_pass(self):
        msgs = [Message(role=Role.SYSTEM, content="sys")]
        matrix = {"claude": []}
        assert filter_for_provider(msgs, Provider.CLAUDE, matrix=matrix) == msgs
