# ------------------------------------------------------------------
# Component: test_provider_panel
# Responsibility: Tests for ProviderPanel empty-state rendering
#                 (Stage 3A.4 — zero providers on new chat).
# Collaborators: ui.provider_panel, config
# ------------------------------------------------------------------
from __future__ import annotations

import pytest

from mchat.config import Config


@pytest.fixture
def config(tmp_path):
    cfg = Config(config_path=tmp_path / "cfg.json")
    cfg.save()
    return cfg


class TestProviderPanelPersonaRows:
    """#95 — toolbar shows one row per persona, not per provider."""

    def test_persona_rows_built(self, qtbot, config):
        """set_personas should build one row per persona entry."""
        from mchat.ui.provider_panel import ProviderPanel
        from mchat.models.message import Provider
        panel = ProviderPanel(config, font_size=14)
        qtbot.addWidget(panel)
        panel.set_personas([
            ("p_partner", "Partner", Provider.CLAUDE),
            ("p_checker", "Checker", Provider.OPENAI),
        ])
        # Should have checkboxes and combos keyed by persona_id
        assert "p_partner" in panel._checkboxes
        assert "p_checker" in panel._checkboxes
        assert "p_partner" in panel._combos
        assert "p_checker" in panel._combos

    def test_checkbox_keyed_by_persona_id(self, qtbot, config):
        """Checkboxes should be keyed by persona_id string, not Provider."""
        from mchat.ui.provider_panel import ProviderPanel
        from mchat.models.message import Provider
        panel = ProviderPanel(config, font_size=14)
        qtbot.addWidget(panel)
        panel.set_personas([
            ("p_partner", "Partner", Provider.CLAUDE),
        ])
        # Should NOT have Provider-keyed checkboxes
        assert Provider.CLAUDE not in panel._checkboxes
        assert "p_partner" in panel._checkboxes

    def test_spend_label_keyed_by_persona_id(self, qtbot, config):
        from mchat.ui.provider_panel import ProviderPanel
        from mchat.models.message import Provider
        panel = ProviderPanel(config, font_size=14)
        qtbot.addWidget(panel)
        panel.set_personas([
            ("p_partner", "Partner", Provider.CLAUDE),
        ])
        assert "p_partner" in panel._spend_labels


class TestProviderPanelEmptyState:
    """Stage 3A.4 — when the selection is empty, the panel should
    show an empty-state hint and a Personas... button instead of
    provider rows."""

    def test_empty_state_shows_hint_label(self, qtbot, config):
        """An empty-state hint must be visible when no personas are
        selected."""
        from mchat.ui.provider_panel import ProviderPanel
        panel = ProviderPanel(config, font_size=14)
        qtbot.addWidget(panel)
        panel.show_empty_state()
        assert panel._empty_hint is not None
        assert not panel._empty_hint.isHidden()

    def test_empty_state_shows_personas_button(self, qtbot, config):
        """A 'Personas...' button must be visible in the empty state."""
        from mchat.ui.provider_panel import ProviderPanel
        panel = ProviderPanel(config, font_size=14)
        qtbot.addWidget(panel)
        panel.show_empty_state()
        assert panel._personas_btn is not None
        assert not panel._personas_btn.isHidden()

    def test_empty_state_hides_provider_rows(self, qtbot, config):
        """Provider combos/checkboxes/spend labels must be hidden in
        the empty state."""
        from mchat.ui.provider_panel import ProviderPanel
        panel = ProviderPanel(config, font_size=14)
        qtbot.addWidget(panel)
        panel.show_empty_state()
        for combo in panel._combos.values():
            assert combo.isHidden()
        for cb in panel._checkboxes.values():
            assert cb.isHidden()
        for label in panel._spend_labels.values():
            assert label.isHidden()

    def test_show_provider_rows_hides_empty_state(self, qtbot, config):
        """After calling show_provider_rows(), the provider widgets
        must be visible and the empty-state hint hidden."""
        from mchat.ui.provider_panel import ProviderPanel
        panel = ProviderPanel(config, font_size=14)
        qtbot.addWidget(panel)
        panel.show_empty_state()
        panel.show_provider_rows()
        assert panel._empty_hint.isHidden()
        assert panel._personas_btn.isHidden()
        for combo in panel._combos.values():
            assert not combo.isHidden()
