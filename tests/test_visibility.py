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


class TestPersonaAwareFilter:
    """Stage 2.7 — filter accepts PersonaTarget and keys the visibility
    matrix by persona_id. Legacy provider-value keys naturally apply
    to the synthetic default persona via D1 (persona_id = provider.value).
    """

    def _persona_msg(self, text, provider, persona_id):
        return Message(
            role=Role.ASSISTANT,
            content=text,
            provider=provider,
            persona_id=persona_id,
        )

    def _user_msg(self, text, addressed):
        return Message(
            role=Role.USER, content=text, addressed_to=addressed,
        )

    def test_filter_accepts_persona_target(self):
        """filter_for_provider can be called with a PersonaTarget, not
        just a bare Provider. The result for a synthetic default
        PersonaTarget must match the Provider call exactly."""
        from mchat.ui.persona_target import synthetic_default
        msgs = [
            _asst("claude", Provider.CLAUDE),
            _asst("gpt", Provider.OPENAI),
        ]
        out_target = filter_for_provider(
            msgs, synthetic_default(Provider.GEMINI), matrix={}
        )
        out_provider = filter_for_provider(msgs, Provider.GEMINI, matrix={})
        assert out_target == out_provider

    def test_matrix_keyed_by_persona_id(self):
        """An explicit persona's matrix entry is keyed by its opaque id.
        Messages from its 'source allowlist' (by persona_id) pass."""
        from mchat.ui.persona_target import PersonaTarget
        partner = PersonaTarget(persona_id="p_partner", provider=Provider.CLAUDE)
        evaluator = PersonaTarget(persona_id="p_evaluator", provider=Provider.CLAUDE)

        msgs = [
            self._persona_msg("partner-says", Provider.CLAUDE, "p_partner"),
            self._persona_msg("evaluator-says", Provider.CLAUDE, "p_evaluator"),
        ]
        # Evaluator's allowlist: only partner
        matrix = {"p_evaluator": ["p_partner"]}
        out = filter_for_provider(msgs, evaluator, matrix=matrix)
        # Evaluator sees partner (allowed) and itself
        contents = [m.content for m in out]
        assert "partner-says" in contents
        assert "evaluator-says" in contents

    def test_legacy_matrix_applies_to_synthetic_default_only(self):
        """D5: a matrix keyed by 'claude' (provider-value string)
        naturally applies to the synthetic default Claude persona,
        because synthetic_default(CLAUDE).persona_id == 'claude'.
        Explicit Claude personas are not affected (their matrix
        lookup uses their opaque id, which isn't in the legacy matrix).
        """
        from mchat.ui.persona_target import PersonaTarget, synthetic_default

        msgs = [
            _asst("from-openai", Provider.OPENAI),
            _asst("from-gemini", Provider.GEMINI),
        ]
        matrix = {"claude": ["openai"]}

        # Synthetic default Claude: matrix applies, allowlist is ["openai"]
        synthetic = synthetic_default(Provider.CLAUDE)
        out = filter_for_provider(msgs, synthetic, matrix=matrix)
        contents = [m.content for m in out]
        assert "from-openai" in contents
        assert "from-gemini" not in contents  # gemini excluded

        # Explicit Claude persona "p_partner" — matrix has no "p_partner" key,
        # so it falls through to full visibility (D5 rule).
        explicit = PersonaTarget(persona_id="p_partner", provider=Provider.CLAUDE)
        out = filter_for_provider(msgs, explicit, matrix=matrix)
        contents = [m.content for m in out]
        assert "from-openai" in contents
        assert "from-gemini" in contents  # full visibility

    def test_user_addressed_to_persona_id(self):
        """A user message addressed_to a specific persona_id is only
        visible to that persona, not to other personas on the same
        provider."""
        from mchat.ui.persona_target import PersonaTarget

        partner = PersonaTarget(persona_id="p_partner", provider=Provider.CLAUDE)
        evaluator = PersonaTarget(persona_id="p_evaluator", provider=Provider.CLAUDE)

        msgs = [self._user_msg("only for partner", "p_partner")]
        assert filter_for_provider(msgs, partner, matrix={}) == msgs
        assert filter_for_provider(msgs, evaluator, matrix={}) == []

    def test_user_addressed_to_provider_value_still_matches_synthetic(self):
        """Legacy addressed_to values like 'claude' still reach the
        synthetic default Claude persona (persona_id == 'claude' matches)."""
        from mchat.ui.persona_target import synthetic_default, PersonaTarget

        synthetic = synthetic_default(Provider.CLAUDE)
        msgs = [self._user_msg("legacy claude-addressed", "claude")]
        assert filter_for_provider(msgs, synthetic, matrix={}) == msgs

        # But an explicit Claude persona does NOT see it — the
        # legacy "claude" token only matches the synthetic default.
        explicit = PersonaTarget(persona_id="p_partner", provider=Provider.CLAUDE)
        assert filter_for_provider(msgs, explicit, matrix={}) == []

    def test_persona_sees_its_own_messages_regardless_of_matrix(self):
        """Even with a restrictive allowlist, the persona sees its
        own messages (keyed by persona_id)."""
        from mchat.ui.persona_target import PersonaTarget

        partner = PersonaTarget(persona_id="p_partner", provider=Provider.CLAUDE)
        msgs = [
            self._persona_msg("my own reply", Provider.CLAUDE, "p_partner"),
            _asst("someone else", Provider.OPENAI),
        ]
        # Empty allowlist
        matrix = {"p_partner": []}
        out = filter_for_provider(msgs, partner, matrix=matrix)
        contents = [m.content for m in out]
        assert "my own reply" in contents
        assert "someone else" not in contents
