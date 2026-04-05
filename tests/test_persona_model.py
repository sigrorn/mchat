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
