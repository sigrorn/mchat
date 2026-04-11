# ------------------------------------------------------------------
# Component: test_persona_resolver
# Responsibility: Tests for PersonaResolver — the conversation-scoped
#                 layer that maps user input to PersonaTargets, sitting
#                 downstream of Router. Covers every D1 rule under the
#                 new @-prefix grammar (replacing the old <word>,
#                 syntax in issue #140).
# Collaborators: ui.persona_resolver, ui.persona_target, db, models
# ------------------------------------------------------------------
from __future__ import annotations

import pytest

from mchat.db import Database
from mchat.models.message import Provider
from mchat.models.persona import Persona, generate_persona_id
from mchat.router import Router
from mchat.ui.persona_resolver import PersonaResolver, ResolveError
from mchat.ui.persona_target import PersonaTarget, synthetic_default
from mchat.ui.state import SelectionState


class _FakeProvider:
    """Minimal stand-in — Router only needs .provider_id and .list_models
    for its constructor and parse path."""
    def __init__(self, pid):
        self._pid = pid

    @property
    def provider_id(self):
        return self._pid


@pytest.fixture
def db(tmp_path):
    d = Database(db_path=tmp_path / "res.db")
    yield d
    d.close()


@pytest.fixture
def all_providers_router():
    providers = {p: _FakeProvider(p) for p in Provider}
    selection_state = SelectionState([synthetic_default(Provider.CLAUDE)])
    return Router(
        providers, default=Provider.CLAUDE, selection_state=selection_state,
    )


@pytest.fixture
def resolver(all_providers_router):
    return PersonaResolver(all_providers_router)


def _make_persona(conv_id, name, slug, provider=Provider.CLAUDE):
    return Persona(
        conversation_id=conv_id,
        id=generate_persona_id(),
        provider=provider,
        name=name,
        name_slug=slug,
    )


class TestExplicitPersonaName:
    def test_single_persona_prefix(self, resolver, db):
        conv = db.create_conversation()
        partner = db.create_persona(
            _make_persona(conv.id, "Partner", "partner")
        )
        targets, cleaned = resolver.resolve("@partner Ciao!", conv.id, db)
        assert len(targets) == 1
        assert targets[0].persona_id == partner.id
        assert targets[0].provider == Provider.CLAUDE
        assert cleaned == "Ciao!"

    def test_multi_persona_prefix(self, resolver, db):
        conv = db.create_conversation()
        partner = db.create_persona(
            _make_persona(conv.id, "Partner", "partner")
        )
        evaluator = db.create_persona(
            _make_persona(conv.id, "Evaluator", "evaluator")
        )
        targets, cleaned = resolver.resolve(
            "@partner @evaluator hello", conv.id, db,
        )
        ids = {t.persona_id for t in targets}
        assert ids == {partner.id, evaluator.id}
        assert cleaned == "hello"

    def test_persona_name_is_case_insensitive(self, resolver, db):
        conv = db.create_conversation()
        p = db.create_persona(_make_persona(conv.id, "Partner", "partner"))
        targets, cleaned = resolver.resolve("@PARTNER Hi", conv.id, db)
        assert len(targets) == 1
        assert targets[0].persona_id == p.id
        assert cleaned == "Hi"

    def test_only_active_personas_match(self, resolver, db):
        """Tombstoned personas must NOT be matchable by prefix — the
        user removed them deliberately."""
        conv = db.create_conversation()
        p = db.create_persona(_make_persona(conv.id, "Gone", "gone"))
        db.tombstone_persona(conv.id, p.id)
        # "@gone" should no longer resolve — it's an unknown token now.
        with pytest.raises(ResolveError):
            resolver.resolve("@gone whatever", conv.id, db)


class TestProviderShorthandSyntheticDefault:
    def test_claude_shorthand_always_resolves_to_synthetic_default(
        self, resolver, db,
    ):
        """Provider shorthand always resolves to the synthetic default,
        even when explicit Claude personas exist. Personas are addressed
        by name, not provider shorthand."""
        conv = db.create_conversation()
        db.create_persona(_make_persona(conv.id, "Partner", "partner"))
        db.create_persona(_make_persona(conv.id, "Evaluator", "evaluator"))

        targets, cleaned = resolver.resolve("@claude hi", conv.id, db)
        assert len(targets) == 1
        assert targets[0] == synthetic_default(Provider.CLAUDE)
        assert cleaned == "hi"

    def test_legacy_conversation_with_no_personas_uses_synthetic(
        self, resolver, db,
    ):
        """Chats that never used //addpersona still work exactly as
        today — provider shorthands resolve to synthetic defaults."""
        conv = db.create_conversation()
        targets, cleaned = resolver.resolve("@gpt hello", conv.id, db)
        assert targets == [synthetic_default(Provider.OPENAI)]
        assert cleaned == "hello"

    def test_pplx_alias_resolves_to_perplexity_synthetic(self, resolver, db):
        conv = db.create_conversation()
        targets, cleaned = resolver.resolve("@pplx hi", conv.id, db)
        assert targets == [synthetic_default(Provider.PERPLEXITY)]
        assert cleaned == "hi"


class TestAllAndOthers:
    def test_all_with_no_personas_falls_back_to_synthetic_defaults(
        self, resolver, db,
    ):
        """#107: `@all` with no explicit personas should fall back to
        synthetic defaults for all configured providers (legacy compat)."""
        conv = db.create_conversation()
        targets, cleaned = resolver.resolve("@all hi everyone", conv.id, db)
        # Should get one synthetic default per configured provider
        assert len(targets) == len(list(Provider))
        provider_set = {t.provider for t in targets}
        assert provider_set == set(Provider)
        assert cleaned == "hi everyone"

    def test_all_returns_only_explicit_personas(self, resolver, db):
        """Stage 4.4: `@all` includes only the conversation's active
        personas — no synthetic defaults for uncovered providers."""
        conv = db.create_conversation()
        partner = db.create_persona(
            _make_persona(conv.id, "Partner", "partner")
        )
        targets, cleaned = resolver.resolve("@all hi", conv.id, db)
        assert len(targets) == 1
        assert targets[0].persona_id == partner.id

    def test_others_complements_over_explicit_personas(self, resolver, db):
        """Stage 4.4: `@others` (was `flipped,`) returns the complement
        of the current selection over explicit personas only."""
        conv = db.create_conversation()
        p1 = db.create_persona(
            _make_persona(conv.id, "Partner", "partner", Provider.CLAUDE)
        )
        p2 = db.create_persona(
            _make_persona(conv.id, "Checker", "checker", Provider.OPENAI)
        )
        t1 = PersonaTarget(persona_id=p1.id, provider=Provider.CLAUDE)
        resolver._router._selection_state.set([t1])

        others, _ = resolver.resolve("@others y", conv.id, db)
        assert len(others) == 1
        assert others[0].persona_id == p2.id


class TestAllOthersNoSyntheticDefaults:
    """#94 — @all/@others must only include explicit personas from the
    conversation, not synthetic defaults for unconfigured providers."""

    def test_all_with_three_personas_returns_only_those_three(
        self, resolver, db,
    ):
        """@all with Partner(claude), Checker(openai), Translator(mistral)
        should return exactly 3 targets — not 5 (no Gemini/Perplexity)."""
        conv = db.create_conversation()
        p1 = db.create_persona(
            _make_persona(conv.id, "Partner", "partner", Provider.CLAUDE)
        )
        p2 = db.create_persona(
            _make_persona(conv.id, "Checker", "checker", Provider.OPENAI)
        )
        p3 = db.create_persona(
            _make_persona(conv.id, "Translator", "translator", Provider.MISTRAL)
        )
        targets, _ = resolver.resolve("@all hello", conv.id, db)
        assert len(targets) == 3
        persona_ids = {t.persona_id for t in targets}
        assert persona_ids == {p1.id, p2.id, p3.id}

    def test_others_with_personas_no_synthetic_defaults(
        self, resolver, db,
    ):
        """@others with 1 of 3 personas selected should return the
        other 2 — not 2 + synthetic defaults for uncovered providers."""
        conv = db.create_conversation()
        p1 = db.create_persona(
            _make_persona(conv.id, "Partner", "partner", Provider.CLAUDE)
        )
        p2 = db.create_persona(
            _make_persona(conv.id, "Checker", "checker", Provider.OPENAI)
        )
        p3 = db.create_persona(
            _make_persona(conv.id, "Translator", "translator", Provider.MISTRAL)
        )
        target1 = PersonaTarget(persona_id=p1.id, provider=Provider.CLAUDE)
        resolver._router._selection_state.set([target1])
        others, _ = resolver.resolve("@others go", conv.id, db)
        assert len(others) == 2
        others_ids = {t.persona_id for t in others}
        assert others_ids == {p2.id, p3.id}

    def test_all_with_zero_personas_falls_back_to_synthetic(self, resolver, db):
        """#107: @all with no personas falls back to synthetic defaults."""
        conv = db.create_conversation()
        targets, _ = resolver.resolve("@all hello", conv.id, db)
        # Falls back to synthetic defaults for all configured providers
        assert len(targets) == len(list(Provider))


class TestOthersPersonaLevel:
    """#84 — @others must compare by persona_id, not provider.
    Two Claude personas selected: 'othering' should work at persona
    level, not exclude all Claude personas."""

    def test_others_persona_level_not_provider_level(self, resolver, db):
        """With one of two Claude personas selected, @others should
        include the other Claude persona (not exclude all Claude)."""
        conv = db.create_conversation()
        partner = db.create_persona(
            _make_persona(conv.id, "Partner", "partner")
        )
        evaluator = db.create_persona(
            _make_persona(conv.id, "Evaluator", "evaluator")
        )
        partner_target = PersonaTarget(persona_id=partner.id, provider=Provider.CLAUDE)
        # Select only Partner
        resolver._router._selection_state.set([partner_target])
        others, _ = resolver.resolve("@others go", conv.id, db)
        others_ids = {t.persona_id for t in others}
        # Evaluator should be in the others set (same provider, different persona)
        assert evaluator.id in others_ids
        # Partner should NOT be in the others set
        assert partner.id not in others_ids

    def test_write_selection_preserves_persona_targets(self, resolver, db):
        """_write_selection should write full PersonaTargets to the
        selection state, not collapse to providers."""
        conv = db.create_conversation()
        partner = db.create_persona(
            _make_persona(conv.id, "Partner", "partner")
        )
        partner_target = PersonaTarget(persona_id=partner.id, provider=Provider.CLAUDE)
        resolver.resolve("@partner hi", conv.id, db)
        # The selection state should hold the actual PersonaTarget
        selection = resolver._router._selection_state.selection
        assert partner_target in selection


class TestUnknownName:
    def test_unknown_token_raises(self, resolver, db):
        conv = db.create_conversation()
        with pytest.raises(ResolveError) as exc:
            resolver.resolve("@nobody hi", conv.id, db)
        assert "nobody" in str(exc.value)

    def test_partial_unknown_in_multi_prefix_raises(self, resolver, db):
        """If a multi-prefix line has one unknown token, fail the
        whole resolve — don't silently drop the unknown."""
        conv = db.create_conversation()
        db.create_persona(_make_persona(conv.id, "Partner", "partner"))
        with pytest.raises(ResolveError):
            resolver.resolve("@partner @nobody hi", conv.id, db)


class TestMixedPrefixes:
    def test_persona_name_then_provider_shorthand(self, resolver, db):
        """`@partner @gpt text` — partner is the explicit persona,
        gpt is the synthetic default. Both end up as targets."""
        conv = db.create_conversation()
        partner = db.create_persona(
            _make_persona(conv.id, "Partner", "partner")
        )
        targets, cleaned = resolver.resolve(
            "@partner @gpt compare your takes", conv.id, db,
        )
        persona_ids = {t.persona_id for t in targets}
        assert partner.id in persona_ids
        assert "openai" in persona_ids  # synthetic default for gpt
        assert cleaned == "compare your takes"

    def test_no_prefix_uses_current_selection(self, resolver, db):
        """An input with no prefix at all uses the router's current
        selection, mapped through synthetic defaults."""
        conv = db.create_conversation()
        resolver._router._selection_state.set([synthetic_default(Provider.OPENAI)])
        targets, cleaned = resolver.resolve("plain text", conv.id, db)
        assert targets == [synthetic_default(Provider.OPENAI)]
        assert cleaned == "plain text"

    def test_empty_input(self, resolver, db):
        conv = db.create_conversation()
        targets, cleaned = resolver.resolve("", conv.id, db)
        # Should return current selection (Claude default) and empty text
        assert targets == [synthetic_default(Provider.CLAUDE)]
        assert cleaned == ""


class TestProviderShorthandAlwaysSynthetic:
    """#113 — provider shorthands always resolve to synthetic default,
    never expand to explicit personas."""

    def test_provider_shorthand_with_explicit_personas_still_synthetic(
        self, resolver, db,
    ):
        """'@claude hello' with explicit Claude personas should still
        resolve to the synthetic default (personas addressed by name)."""
        conv = db.create_conversation()
        db.create_persona(_make_persona(conv.id, "Partner", "partner"))
        targets, cleaned = resolver.resolve("@claude hello", conv.id, db)
        assert targets == [synthetic_default(Provider.CLAUDE)]
        assert cleaned == "hello"

    def test_provider_shorthand_without_explicit_personas(self, resolver, db):
        conv = db.create_conversation()
        targets, cleaned = resolver.resolve("@gpt hello", conv.id, db)
        assert targets == [synthetic_default(Provider.OPENAI)]
        assert cleaned == "hello"


class TestSelectionStateUpdate:
    def test_resolve_updates_current_selection(self, resolver, db):
        """After resolve, the router's selection state should reflect
        the new targets — same behaviour as Router.parse used to have.
        """
        conv = db.create_conversation()
        partner = db.create_persona(
            _make_persona(conv.id, "Partner", "partner")
        )
        resolver.resolve("@partner hi", conv.id, db)
        # The underlying selection state should now hold whatever
        # providers are referenced by the targets.
        assert resolver._router.selection == [Provider.CLAUDE]


# ==================================================================
# #140 — new @-prefix grammar: regression & edge-case coverage
# ==================================================================


class TestAtGrammarEdgeCases:
    """#140 — the core motivation for the grammar change: natural
    English with a leading comma-terminated word no longer triggers
    false-positive targeting."""

    def test_ok_comma_no_longer_raises_resolve_error(self, resolver, db):
        """'ok, but what about X?' used to raise ResolveError because
        the old `<word>,` parser treated "ok" as an unknown persona.
        Under @-grammar this is plain text and falls through to the
        current selection."""
        conv = db.create_conversation()
        targets, cleaned = resolver.resolve(
            "ok, but what about X?", conv.id, db,
        )
        # Falls through to current selection (Claude default from fixture)
        assert targets == [synthetic_default(Provider.CLAUDE)]
        assert cleaned == "ok, but what about X?"

    def test_well_comma_no_longer_raises(self, resolver, db):
        conv = db.create_conversation()
        targets, cleaned = resolver.resolve(
            "well, if you say so", conv.id, db,
        )
        assert cleaned == "well, if you say so"

    def test_at_terminates_on_first_non_at_token(self, resolver, db):
        """'@partner ok, but what about X?' — partner is the only
        target; the comma after 'ok' is just data."""
        conv = db.create_conversation()
        partner = db.create_persona(
            _make_persona(conv.id, "Partner", "partner")
        )
        targets, cleaned = resolver.resolve(
            "@partner ok, but what about X?", conv.id, db,
        )
        assert len(targets) == 1
        assert targets[0].persona_id == partner.id
        assert cleaned == "ok, but what about X?"

    def test_at_in_middle_of_text_is_data(self, resolver, db):
        """'hello @partner' — first token is 'hello', not '@...', so
        no targeting happens; '@partner' is part of the prompt text."""
        conv = db.create_conversation()
        db.create_persona(_make_persona(conv.id, "Partner", "partner"))
        targets, cleaned = resolver.resolve(
            "hello @partner", conv.id, db,
        )
        # Falls through to current selection; text is unchanged.
        assert targets == [synthetic_default(Provider.CLAUDE)]
        assert cleaned == "hello @partner"

    def test_at_prefix_only_changes_selection(self, resolver, db):
        """'@partner' alone (no prompt) should resolve to partner and
        leave an empty cleaned text — same behaviour as the old
        'partner,' alone. The caller interprets empty cleaned text as
        a prefix-only selection change."""
        conv = db.create_conversation()
        partner = db.create_persona(
            _make_persona(conv.id, "Partner", "partner")
        )
        targets, cleaned = resolver.resolve("@partner", conv.id, db)
        assert len(targets) == 1
        assert targets[0].persona_id == partner.id
        assert cleaned == ""

    def test_multiple_at_prefix_only(self, resolver, db):
        """'@partner @evaluator' (no prompt) — both targets selected,
        cleaned text is empty."""
        conv = db.create_conversation()
        partner = db.create_persona(
            _make_persona(conv.id, "Partner", "partner")
        )
        evaluator = db.create_persona(
            _make_persona(conv.id, "Evaluator", "evaluator")
        )
        targets, cleaned = resolver.resolve(
            "@partner @evaluator", conv.id, db,
        )
        ids = {t.persona_id for t in targets}
        assert ids == {partner.id, evaluator.id}
        assert cleaned == ""

    def test_leading_whitespace_before_at(self, resolver, db):
        """Leading whitespace is stripped before tokenisation."""
        conv = db.create_conversation()
        partner = db.create_persona(
            _make_persona(conv.id, "Partner", "partner")
        )
        targets, cleaned = resolver.resolve("   @partner hello", conv.id, db)
        assert len(targets) == 1
        assert targets[0].persona_id == partner.id
        assert cleaned == "hello"

    def test_all_combined_with_persona_breaks_parse(self, resolver, db):
        """'@partner @all foo' — @all is special and not combinable.
        When we see @all after collecting other targets, the parser
        stops treating it as a target; from there it's message text.
        So targets=[partner], cleaned='@all foo'."""
        conv = db.create_conversation()
        partner = db.create_persona(
            _make_persona(conv.id, "Partner", "partner")
        )
        targets, cleaned = resolver.resolve(
            "@partner @all foo", conv.id, db,
        )
        assert len(targets) == 1
        assert targets[0].persona_id == partner.id
        assert cleaned == "@all foo"

    def test_others_combined_with_persona_breaks_parse(self, resolver, db):
        """Same rule applies to @others."""
        conv = db.create_conversation()
        partner = db.create_persona(
            _make_persona(conv.id, "Partner", "partner")
        )
        targets, cleaned = resolver.resolve(
            "@partner @others foo", conv.id, db,
        )
        assert len(targets) == 1
        assert targets[0].persona_id == partner.id
        assert cleaned == "@others foo"

    def test_unknown_at_token_error_message_lists_options(self, resolver, db):
        """ResolveError should tell the user what valid @ tokens exist
        so they can correct the typo."""
        conv = db.create_conversation()
        db.create_persona(_make_persona(conv.id, "Partner", "partner"))
        with pytest.raises(ResolveError) as exc:
            resolver.resolve("@nobody hello", conv.id, db)
        msg = str(exc.value)
        assert "nobody" in msg
        # The error should mention at least one valid option category
        assert ("@claude" in msg or "@all" in msg or "partner" in msg)


class TestGrandfatheredReservedNamePersona:
    """#140 — old chats may contain personas named after a provider or
    keyword (pre-validator). The resolver must still find them by name,
    preserving existing semantics. The validator blocks new ones from
    being created; existing ones keep working unchanged.
    """

    def test_reserved_name_persona_matches_before_synthetic_default(
        self, resolver, db,
    ):
        """A chat with an explicit persona named 'claude' (slug 'claude')
        should resolve '@claude' to THAT persona, not to the Claude
        synthetic default. Achieved by persona-slug lookup preceding
        the provider-shorthand fallback in the resolver."""
        conv = db.create_conversation()
        # Bypass the validator by creating the persona directly.
        # (The validator will block this path for new personas once
        # Phase 2 lands, but persona rows already in the DB are kept.)
        grandfathered = db.create_persona(
            _make_persona(conv.id, "claude", "claude", Provider.OPENAI),
        )
        targets, cleaned = resolver.resolve("@claude hello", conv.id, db)
        assert len(targets) == 1
        assert targets[0].persona_id == grandfathered.id
        # The persona is backed by OpenAI, not Claude — another argument
        # against allowing new ones like this.
        assert targets[0].provider == Provider.OPENAI
        assert cleaned == "hello"

    def test_no_grandfathered_persona_falls_back_to_synthetic(
        self, resolver, db,
    ):
        """In a chat without a 'claude' persona, '@claude' is the
        synthetic Claude default — the usual case."""
        conv = db.create_conversation()
        targets, cleaned = resolver.resolve("@claude hello", conv.id, db)
        assert targets == [synthetic_default(Provider.CLAUDE)]
        assert cleaned == "hello"
