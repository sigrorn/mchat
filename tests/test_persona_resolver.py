# ------------------------------------------------------------------
# Component: test_persona_resolver
# Responsibility: Tests for PersonaResolver — the conversation-scoped
#                 layer that maps user input to PersonaTargets, sitting
#                 downstream of Router. Covers every D1 rule.
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
        targets, cleaned = resolver.resolve("partner, Ciao!", conv.id, db)
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
            "partner, evaluator, hello", conv.id, db,
        )
        ids = {t.persona_id for t in targets}
        assert ids == {partner.id, evaluator.id}
        assert cleaned == "hello"

    def test_persona_name_is_case_insensitive(self, resolver, db):
        conv = db.create_conversation()
        p = db.create_persona(_make_persona(conv.id, "Partner", "partner"))
        targets, cleaned = resolver.resolve("PARTNER, Hi", conv.id, db)
        assert len(targets) == 1
        assert targets[0].persona_id == p.id
        assert cleaned == "Hi"

    def test_only_active_personas_match(self, resolver, db):
        """Tombstoned personas must NOT be matchable by prefix — the
        user removed them deliberately."""
        conv = db.create_conversation()
        p = db.create_persona(_make_persona(conv.id, "Gone", "gone"))
        db.tombstone_persona(conv.id, p.id)
        # "gone," should no longer resolve — it's an unknown token now.
        with pytest.raises(ResolveError):
            resolver.resolve("gone, whatever", conv.id, db)


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

        targets, cleaned = resolver.resolve("claude, hi", conv.id, db)
        assert len(targets) == 1
        assert targets[0] == synthetic_default(Provider.CLAUDE)
        assert cleaned == "hi"

    def test_legacy_conversation_with_no_personas_uses_synthetic(
        self, resolver, db,
    ):
        """Chats that never used //addpersona still work exactly as
        today — provider shorthands resolve to synthetic defaults."""
        conv = db.create_conversation()
        targets, cleaned = resolver.resolve("gpt, hello", conv.id, db)
        assert targets == [synthetic_default(Provider.OPENAI)]
        assert cleaned == "hello"

    def test_pplx_alias_resolves_to_perplexity_synthetic(self, resolver, db):
        conv = db.create_conversation()
        targets, cleaned = resolver.resolve("pplx, hi", conv.id, db)
        assert targets == [synthetic_default(Provider.PERPLEXITY)]
        assert cleaned == "hi"


class TestAllAndFlipped:
    def test_all_with_no_personas_falls_back_to_synthetic_defaults(
        self, resolver, db,
    ):
        """#107: `all,` with no explicit personas should fall back to
        synthetic defaults for all configured providers (legacy compat)."""
        conv = db.create_conversation()
        targets, cleaned = resolver.resolve("all, hi everyone", conv.id, db)
        # Should get one synthetic default per configured provider
        assert len(targets) == len(list(Provider))
        provider_set = {t.provider for t in targets}
        assert provider_set == set(Provider)
        assert cleaned == "hi everyone"

    def test_all_returns_only_explicit_personas(self, resolver, db):
        """Stage 4.4: `all,` includes only the conversation's active
        personas — no synthetic defaults for uncovered providers."""
        conv = db.create_conversation()
        partner = db.create_persona(
            _make_persona(conv.id, "Partner", "partner")
        )
        targets, cleaned = resolver.resolve("all, hi", conv.id, db)
        assert len(targets) == 1
        assert targets[0].persona_id == partner.id

    def test_flipped_complements_over_explicit_personas(self, resolver, db):
        """Stage 4.4: `flipped,` returns the complement of the current
        selection over explicit personas only."""
        conv = db.create_conversation()
        p1 = db.create_persona(
            _make_persona(conv.id, "Partner", "partner", Provider.CLAUDE)
        )
        p2 = db.create_persona(
            _make_persona(conv.id, "Checker", "checker", Provider.OPENAI)
        )
        t1 = PersonaTarget(persona_id=p1.id, provider=Provider.CLAUDE)
        resolver._router._selection_state.set([t1])

        flipped, _ = resolver.resolve("flipped, y", conv.id, db)
        assert len(flipped) == 1
        assert flipped[0].persona_id == p2.id


class TestAllFlippedNoSyntheticDefaults:
    """#94 — all/flipped must only include explicit personas from the
    conversation, not synthetic defaults for unconfigured providers."""

    def test_all_with_three_personas_returns_only_those_three(
        self, resolver, db,
    ):
        """all, with Partner(claude), Checker(openai), Translator(mistral)
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
        targets, _ = resolver.resolve("all, hello", conv.id, db)
        assert len(targets) == 3
        persona_ids = {t.persona_id for t in targets}
        assert persona_ids == {p1.id, p2.id, p3.id}

    def test_flipped_with_personas_no_synthetic_defaults(
        self, resolver, db,
    ):
        """flipped, with 1 of 3 personas selected should return the
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
        flipped, _ = resolver.resolve("flipped, go", conv.id, db)
        assert len(flipped) == 2
        flipped_ids = {t.persona_id for t in flipped}
        assert flipped_ids == {p2.id, p3.id}

    def test_all_with_zero_personas_falls_back_to_synthetic(self, resolver, db):
        """#107: all, with no personas falls back to synthetic defaults."""
        conv = db.create_conversation()
        targets, _ = resolver.resolve("all, hello", conv.id, db)
        # Falls back to synthetic defaults for all configured providers
        assert len(targets) == len(list(Provider))


class TestFlippedPersonaLevel:
    """#84 — flipped must compare by persona_id, not provider.
    Two Claude personas selected: flipping should flip at persona
    level, not exclude all Claude personas."""

    def test_flipped_persona_level_not_provider_level(self, resolver, db):
        """With one of two Claude personas selected, flipped should
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
        flipped, _ = resolver.resolve("flipped, go", conv.id, db)
        flipped_ids = {t.persona_id for t in flipped}
        # Evaluator should be in the flipped set (same provider, different persona)
        assert evaluator.id in flipped_ids
        # Partner should NOT be in the flipped set
        assert partner.id not in flipped_ids

    def test_write_selection_preserves_persona_targets(self, resolver, db):
        """_write_selection should write full PersonaTargets to the
        selection state, not collapse to providers."""
        conv = db.create_conversation()
        partner = db.create_persona(
            _make_persona(conv.id, "Partner", "partner")
        )
        partner_target = PersonaTarget(persona_id=partner.id, provider=Provider.CLAUDE)
        resolver.resolve("partner, hi", conv.id, db)
        # The selection state should hold the actual PersonaTarget
        selection = resolver._router._selection_state.selection
        assert partner_target in selection


class TestUnknownName:
    def test_unknown_token_raises(self, resolver, db):
        conv = db.create_conversation()
        with pytest.raises(ResolveError) as exc:
            resolver.resolve("nobody, hi", conv.id, db)
        assert "nobody" in str(exc.value)

    def test_partial_unknown_in_multi_prefix_raises(self, resolver, db):
        """If a multi-prefix line has one unknown token, fail the
        whole resolve — don't silently drop the unknown."""
        conv = db.create_conversation()
        db.create_persona(_make_persona(conv.id, "Partner", "partner"))
        with pytest.raises(ResolveError):
            resolver.resolve("partner, nobody, hi", conv.id, db)


class TestMixedPrefixes:
    def test_persona_name_then_provider_shorthand(self, resolver, db):
        """`partner, claude, text` — partner is the explicit persona,
        claude is the synthetic default. Both end up as targets."""
        conv = db.create_conversation()
        partner = db.create_persona(
            _make_persona(conv.id, "Partner", "partner")
        )
        targets, cleaned = resolver.resolve(
            "partner, gpt, compare your takes", conv.id, db,
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
        """'claude, hello' with explicit Claude personas should still
        resolve to the synthetic default (personas addressed by name)."""
        conv = db.create_conversation()
        db.create_persona(_make_persona(conv.id, "Partner", "partner"))
        targets, cleaned = resolver.resolve("claude, hello", conv.id, db)
        assert targets == [synthetic_default(Provider.CLAUDE)]
        assert cleaned == "hello"

    def test_provider_shorthand_without_explicit_personas(self, resolver, db):
        conv = db.create_conversation()
        targets, cleaned = resolver.resolve("gpt, hello", conv.id, db)
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
        resolver.resolve("partner, hi", conv.id, db)
        # The underlying selection state should now hold whatever
        # providers are referenced by the targets.
        assert resolver._router.selection == [Provider.CLAUDE]
