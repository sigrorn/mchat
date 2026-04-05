# ------------------------------------------------------------------
# Component: test_persona_resolution
# Responsibility: Tests for the shared resolution helpers that
#                 implement D6b — null-means-inherit for persona
#                 prompt / model / colour. Pure functions, no Qt.
# Collaborators: ui.persona_resolution, models.persona, config
# ------------------------------------------------------------------
from __future__ import annotations

import pytest

from mchat.config import Config
from mchat.models.message import Provider
from mchat.models.persona import Persona, generate_persona_id
from mchat.ui.persona_resolution import (
    resolve_persona_color,
    resolve_persona_model,
    resolve_persona_prompt,
)


@pytest.fixture
def config(tmp_path):
    cfg = Config(config_path=tmp_path / "cfg.json")
    cfg.set("system_prompt_claude", "Global Claude prompt")
    cfg.set("claude_model", "claude-sonnet-4-global")
    cfg.set("color_claude", "#111111")
    cfg.save()
    return cfg


def _persona(**overrides):
    fields = dict(
        conversation_id=1,
        id=generate_persona_id(),
        provider=Provider.CLAUDE,
        name="Evaluator",
        name_slug="evaluator",
    )
    fields.update(overrides)
    return Persona(**fields)


class TestResolvePersonaPrompt:
    def test_override_set_returns_override(self, config):
        p = _persona(system_prompt_override="Be ruthless and direct")
        assert resolve_persona_prompt(p, config) == "Be ruthless and direct"

    def test_override_none_returns_global(self, config):
        p = _persona(system_prompt_override=None)
        assert resolve_persona_prompt(p, config) == "Global Claude prompt"

    def test_synthetic_default_persona_falls_through_to_global(self, config):
        """D1: the synthetic default persona has every override as None,
        so every resolver should return the global value for it."""
        synthetic = _persona(
            id="claude",
            name="Claude",
            name_slug="claude",
            system_prompt_override=None,
        )
        assert resolve_persona_prompt(synthetic, config) == "Global Claude prompt"


class TestResolvePersonaModel:
    def test_override_set_returns_override(self, config):
        p = _persona(model_override="claude-opus-4")
        assert resolve_persona_model(p, config) == "claude-opus-4"

    def test_override_none_returns_global(self, config):
        p = _persona(model_override=None)
        assert resolve_persona_model(p, config) == "claude-sonnet-4-global"

    def test_synthetic_default_persona_falls_through_to_global(self, config):
        synthetic = _persona(
            id="claude",
            name="Claude",
            name_slug="claude",
            model_override=None,
        )
        assert resolve_persona_model(synthetic, config) == "claude-sonnet-4-global"


class TestResolvePersonaColor:
    def test_override_set_returns_override(self, config):
        p = _persona(color_override="#ff00ff")
        assert resolve_persona_color(p, config) == "#ff00ff"

    def test_override_none_returns_global(self, config):
        p = _persona(color_override=None)
        assert resolve_persona_color(p, config) == "#111111"

    def test_synthetic_default_persona_falls_through_to_global(self, config):
        synthetic = _persona(
            id="claude",
            name="Claude",
            name_slug="claude",
            color_override=None,
        )
        assert resolve_persona_color(synthetic, config) == "#111111"
