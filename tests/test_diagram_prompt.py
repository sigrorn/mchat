# ------------------------------------------------------------------
# Component: test_diagram_prompt
# Responsibility: Tests for the diagram_instruction() helper that
#                 decides what diagramming instruction to inject into
#                 the system prompt based on tool availability and
#                 user preference.
# Collaborators: mchat.diagram_prompt, mchat.dot_renderer,
#                mchat.mermaid_renderer, mchat.config
# ------------------------------------------------------------------
from __future__ import annotations

import pytest

from mchat.config import Config
from mchat.diagram_prompt import diagram_instruction


@pytest.fixture
def config(tmp_path):
    return Config(config_path=tmp_path / "cfg.json")


def _patch_tools(monkeypatch, *, graphviz: bool, mmdc: bool):
    """Monkeypatch tool availability checks."""
    from mchat import dot_renderer, mermaid_renderer

    dot_renderer.is_graphviz_available.cache_clear()
    mermaid_renderer.is_mmdc_available.cache_clear()
    monkeypatch.setattr(
        dot_renderer, "is_graphviz_available",
        lambda: graphviz,
    )
    monkeypatch.setattr(
        mermaid_renderer, "is_mmdc_available",
        lambda: mmdc,
    )


class TestAutoMode:
    """diagram_format='auto' (default) — detect what's installed."""

    def test_neither_installed_returns_none(self, config, monkeypatch):
        _patch_tools(monkeypatch, graphviz=False, mmdc=False)
        assert diagram_instruction(config) is None

    def test_graphviz_only_recommends_dot(self, config, monkeypatch):
        _patch_tools(monkeypatch, graphviz=True, mmdc=False)
        result = diagram_instruction(config)
        assert result is not None
        assert "dot" in result.lower()
        assert "mermaid" in result.lower()  # tells model NOT to use mermaid

    def test_mmdc_only_recommends_mermaid(self, config, monkeypatch):
        _patch_tools(monkeypatch, graphviz=False, mmdc=True)
        result = diagram_instruction(config)
        assert result is not None
        assert "mermaid" in result.lower()

    def test_both_installed_prefers_mermaid(self, config, monkeypatch):
        _patch_tools(monkeypatch, graphviz=True, mmdc=True)
        result = diagram_instruction(config)
        assert result is not None
        assert "mermaid" in result.lower()
        # Should also mention dot as an alternative
        assert "dot" in result.lower()


class TestExplicitPreference:
    """User overrides the auto-detection via diagram_format setting."""

    def test_mermaid_preference_regardless_of_tools(self, config, monkeypatch):
        _patch_tools(monkeypatch, graphviz=True, mmdc=False)
        config.set("diagram_format", "mermaid")
        result = diagram_instruction(config)
        assert result is not None
        assert "mermaid" in result.lower()

    def test_graphviz_preference_regardless_of_tools(self, config, monkeypatch):
        _patch_tools(monkeypatch, graphviz=False, mmdc=True)
        config.set("diagram_format", "graphviz")
        result = diagram_instruction(config)
        assert result is not None
        assert "dot" in result.lower()

    def test_none_preference_returns_none(self, config, monkeypatch):
        _patch_tools(monkeypatch, graphviz=True, mmdc=True)
        config.set("diagram_format", "none")
        assert diagram_instruction(config) is None


class TestDefaultConfigValue:
    """The default config value for diagram_format is 'auto'."""

    def test_default_is_auto(self, config):
        assert config.get("diagram_format") == "auto"
