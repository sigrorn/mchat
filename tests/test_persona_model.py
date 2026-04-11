# ------------------------------------------------------------------
# Component: test_persona_model
# Responsibility: Tests for the Persona dataclass, generate_persona_id
#                 helper, and slugify_persona_name helper. Pure data
#                 layer, no DB, no Qt.
# Collaborators: models.persona
# ------------------------------------------------------------------
from __future__ import annotations

import re

import pytest

from mchat.models.message import Provider
from mchat.models.persona import (
    Persona,
    generate_persona_id,
    slugify_persona_name,
)


class TestPersonaDataclass:
    def test_required_fields(self):
        p = Persona(
            conversation_id=1,
            id="p_abc12345",
            provider=Provider.CLAUDE,
            name="Evaluator",
            name_slug="evaluator",
        )
        assert p.conversation_id == 1
        assert p.id == "p_abc12345"
        assert p.provider == Provider.CLAUDE
        assert p.name == "Evaluator"
        assert p.name_slug == "evaluator"

    def test_override_fields_default_to_none(self):
        """Every override field uses null-means-inherit per D6."""
        p = Persona(
            conversation_id=1,
            id="p_abc12345",
            provider=Provider.CLAUDE,
            name="Evaluator",
            name_slug="evaluator",
        )
        assert p.system_prompt_override is None
        assert p.model_override is None
        assert p.color_override is None

    def test_history_scope_defaults_to_full_history(self):
        p = Persona(
            conversation_id=1,
            id="p_abc12345",
            provider=Provider.CLAUDE,
            name="Evaluator",
            name_slug="evaluator",
        )
        assert p.created_at_message_index is None

    def test_sort_order_defaults_to_zero(self):
        p = Persona(
            conversation_id=1,
            id="p_abc12345",
            provider=Provider.CLAUDE,
            name="x",
            name_slug="x",
        )
        assert p.sort_order == 0

    def test_deleted_at_defaults_to_none_active(self):
        """A freshly-created persona is active (not tombstoned)."""
        p = Persona(
            conversation_id=1,
            id="p_abc12345",
            provider=Provider.CLAUDE,
            name="x",
            name_slug="x",
        )
        assert p.deleted_at is None


class TestGeneratePersonaId:
    def test_format(self):
        pid = generate_persona_id()
        assert re.fullmatch(r"p_[0-9a-z]{8}", pid), f"unexpected format: {pid!r}"

    def test_ids_are_unique_in_practice(self):
        """Generate a batch and check they're all distinct. Not a
        cryptographic guarantee — 36**8 ≈ 2.8 trillion combinations
        means collisions within 1000 samples are astronomically
        unlikely (birthday bound ~1 in 1.4e6)."""
        ids = {generate_persona_id() for _ in range(1000)}
        assert len(ids) == 1000


class TestSlugifyPersonaName:
    def test_lowercases(self):
        assert slugify_persona_name("Evaluator") == "evaluator"

    def test_strips_whitespace(self):
        assert slugify_persona_name("  Partner  ") == "partner"

    def test_collapses_runs_of_non_alnum_to_underscore(self):
        assert slugify_persona_name("Italian tutor") == "italian_tutor"
        assert slugify_persona_name("Italian  tutor") == "italian_tutor"
        assert slugify_persona_name("role-reviewer") == "role_reviewer"

    def test_strips_leading_and_trailing_separators(self):
        assert slugify_persona_name("--eval--") == "eval"
        assert slugify_persona_name("  eval  ") == "eval"

    def test_preserves_digits(self):
        assert slugify_persona_name("Reviewer 2") == "reviewer_2"

    def test_empty_name_raises(self):
        with pytest.raises(ValueError):
            slugify_persona_name("")
        with pytest.raises(ValueError):
            slugify_persona_name("   ")
        with pytest.raises(ValueError):
            slugify_persona_name("---")


class TestValidatePersonaName:
    """#140 — validate_persona_name rejects names that contain
    whitespace, punctuation other than - and _, the @ sigil, or
    collide with reserved tokens. Applied on NEW write paths
    (create_persona, //addpersona, //editpersona with name change,
    persona import). Grandfathered personas don't go through this
    path — slugify_persona_name still accepts historical names for
    back-compat at read-time."""

    def test_accepts_letters_digits_hyphen_underscore(self):
        from mchat.models.persona import validate_persona_name
        # Must not raise
        validate_persona_name("partner")
        validate_persona_name("Partner")
        validate_persona_name("italian-tutor")
        validate_persona_name("italian_tutor")
        validate_persona_name("claude-bot_42")
        validate_persona_name("X")  # single char is OK

    def test_rejects_empty_string(self):
        from mchat.models.persona import validate_persona_name
        with pytest.raises(ValueError):
            validate_persona_name("")

    def test_rejects_whitespace(self):
        from mchat.models.persona import validate_persona_name
        with pytest.raises(ValueError, match=r"whitespace"):
            validate_persona_name("Claude Bot")
        with pytest.raises(ValueError, match=r"whitespace"):
            validate_persona_name("  Partner")
        with pytest.raises(ValueError, match=r"whitespace"):
            validate_persona_name("Partner\t")

    def test_rejects_at_sigil(self):
        from mchat.models.persona import validate_persona_name
        with pytest.raises(ValueError):
            validate_persona_name("@partner")

    def test_rejects_comma_colon_slash(self):
        from mchat.models.persona import validate_persona_name
        with pytest.raises(ValueError):
            validate_persona_name("part,ner")
        with pytest.raises(ValueError):
            validate_persona_name("part:ner")
        with pytest.raises(ValueError):
            validate_persona_name("part/ner")

    def test_rejects_other_punctuation(self):
        from mchat.models.persona import validate_persona_name
        for bad in ("part.ner", "part!ner", "part(ner)", "part#ner", "part$ner"):
            with pytest.raises(ValueError):
                validate_persona_name(bad)

    def test_rejects_reserved_provider_shorthand(self):
        from mchat.models.persona import validate_persona_name
        for reserved in ("claude", "gpt", "gemini", "perplexity", "pplx", "mistral"):
            with pytest.raises(ValueError, match=r"reserved"):
                validate_persona_name(reserved)

    def test_rejects_reserved_all_and_others(self):
        from mchat.models.persona import validate_persona_name
        with pytest.raises(ValueError, match=r"reserved"):
            validate_persona_name("all")
        with pytest.raises(ValueError, match=r"reserved"):
            validate_persona_name("others")

    def test_reserved_check_is_case_insensitive(self):
        from mchat.models.persona import validate_persona_name
        for bad in ("Claude", "CLAUDE", "GPT", "All", "Others", "OTHERS"):
            with pytest.raises(ValueError, match=r"reserved"):
                validate_persona_name(bad)

    def test_slugify_does_not_call_validator(self):
        """slugify_persona_name is still the back-compat path for
        grandfathered data; it must NOT reject whitespace-containing
        names. Only write-path validators call validate_persona_name."""
        from mchat.models.persona import slugify_persona_name
        # Historical data with a space should still slugify cleanly.
        assert slugify_persona_name("Italian tutor") == "italian_tutor"
