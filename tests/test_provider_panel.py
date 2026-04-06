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
        assert panel._empty_hint.isVisible()

    def test_empty_state_shows_personas_button(self, qtbot, config):
        """A 'Personas...' button must be visible in the empty state."""
        from mchat.ui.provider_panel import ProviderPanel
        panel = ProviderPanel(config, font_size=14)
        qtbot.addWidget(panel)
        panel.show_empty_state()
        assert panel._personas_btn is not None
        assert panel._personas_btn.isVisible()

    def test_empty_state_hides_provider_rows(self, qtbot, config):
        """Provider combos/checkboxes/spend labels must be hidden in
        the empty state."""
        from mchat.ui.provider_panel import ProviderPanel
        panel = ProviderPanel(config, font_size=14)
        qtbot.addWidget(panel)
        panel.show_empty_state()
        for combo in panel._combos.values():
            assert not combo.isVisible()
        for cb in panel._checkboxes.values():
            assert not cb.isVisible()
        for label in panel._spend_labels.values():
            assert not label.isVisible()

    def test_show_provider_rows_hides_empty_state(self, qtbot, config):
        """After calling show_provider_rows(), the provider widgets
        must be visible and the empty-state hint hidden."""
        from mchat.ui.provider_panel import ProviderPanel
        panel = ProviderPanel(config, font_size=14)
        qtbot.addWidget(panel)
        panel.show_empty_state()
        panel.show_provider_rows()
        assert not panel._empty_hint.isVisible()
        assert not panel._personas_btn.isVisible()
        for combo in panel._combos.values():
            assert combo.isVisible()
