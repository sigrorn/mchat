# ------------------------------------------------------------------
# Component: test_persona_target
# Responsibility: Tests for PersonaTarget frozen dataclass and the
#                 synthetic_default helper (D1).
# Collaborators: ui.persona_target, models.message
# ------------------------------------------------------------------
from __future__ import annotations

import pytest

from mchat.models.message import Provider
from mchat.ui.persona_target import PersonaTarget, synthetic_default


class TestPersonaTarget:
    def test_fields(self):
        t = PersonaTarget(persona_id="p_abc12345", provider=Provider.CLAUDE)
        assert t.persona_id == "p_abc12345"
        assert t.provider == Provider.CLAUDE

    def test_equality(self):
        a = PersonaTarget(persona_id="x", provider=Provider.CLAUDE)
        b = PersonaTarget(persona_id="x", provider=Provider.CLAUDE)
        c = PersonaTarget(persona_id="y", provider=Provider.CLAUDE)
        d = PersonaTarget(persona_id="x", provider=Provider.OPENAI)
        assert a == b
        assert a != c
        assert a != d

    def test_frozen_cannot_be_mutated(self):
        from dataclasses import FrozenInstanceError
        t = PersonaTarget(persona_id="x", provider=Provider.CLAUDE)
        with pytest.raises(FrozenInstanceError):
            t.persona_id = "y"

    def test_hashable(self):
        """Frozen dataclasses must be hashable so they can live in sets
        and dict keys — used by SelectionState and the resolver."""
        t = PersonaTarget(persona_id="x", provider=Provider.CLAUDE)
        {t}  # should not raise
        {t: 1}  # should not raise


class TestSyntheticDefault:
    @pytest.mark.parametrize("provider", list(Provider))
    def test_returns_persona_id_equal_to_provider_value(self, provider):
        """D1: the synthetic default persona has id = provider.value
        as a deliberate exception to opaque-id convention, so legacy
        messages (persona_id=None → provider.value at resolution time)
        still reach the same downstream code path."""
        t = synthetic_default(provider)
        assert t.persona_id == provider.value
        assert t.provider == provider

    def test_returns_frozen_persona_target(self):
        t = synthetic_default(Provider.CLAUDE)
        assert isinstance(t, PersonaTarget)
