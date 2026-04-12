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


class TestTwoRowToolbar:
    """#157 — toolbar splits into two rows when >4 personas."""

    def _make_entries(self, n):
        from mchat.models.message import Provider
        providers = list(Provider)
        return [
            (f"p_{i}", f"Persona{i}", providers[i % len(providers)])
            for i in range(n)
        ]

    def test_single_row_with_4_personas(self, qtbot, config):
        """With exactly 4 personas, the panel uses a single row."""
        from mchat.ui.provider_panel import ProviderPanel
        panel = ProviderPanel(config, font_size=14)
        qtbot.addWidget(panel)
        panel.set_personas(self._make_entries(4))
        # Single-row mode: _personas_row and _buttons_row are the same layout
        assert not panel._two_row_mode

    def test_two_rows_with_5_personas(self, qtbot, config):
        """With 5 personas, the panel switches to two-row mode."""
        from mchat.ui.provider_panel import ProviderPanel
        panel = ProviderPanel(config, font_size=14)
        qtbot.addWidget(panel)
        panel.set_personas(self._make_entries(5))
        assert panel._two_row_mode

    def test_two_rows_with_6_personas(self, qtbot, config):
        """With 6 personas, the panel is in two-row mode."""
        from mchat.ui.provider_panel import ProviderPanel
        panel = ProviderPanel(config, font_size=14)
        qtbot.addWidget(panel)
        panel.set_personas(self._make_entries(6))
        assert panel._two_row_mode
        # All personas are still present
        assert len(panel._checkboxes) == 6
        assert len(panel._combos) == 6

    def test_back_to_single_row_when_personas_removed(self, qtbot, config):
        """Switching from >4 to <=4 personas reverts to single row."""
        from mchat.ui.provider_panel import ProviderPanel
        panel = ProviderPanel(config, font_size=14)
        qtbot.addWidget(panel)
        panel.set_personas(self._make_entries(6))
        assert panel._two_row_mode
        panel.set_personas(self._make_entries(3))
        assert not panel._two_row_mode

    def test_action_buttons_in_bottom_row(self, qtbot, config):
        """In two-row mode, action buttons added via layout_ref() go
        into the bottom row, not the personas row."""
        from mchat.ui.provider_panel import ProviderPanel
        from PySide6.QtWidgets import QPushButton
        panel = ProviderPanel(config, font_size=14)
        qtbot.addWidget(panel)
        # Add a button to layout_ref() BEFORE set_personas (like main_window does)
        btn = QPushButton("Test")
        panel.layout_ref().addWidget(btn)
        panel.set_personas(self._make_entries(6))
        # Button should still be visible and in the buttons row
        assert btn.isVisible()
        # The buttons row layout should contain our button
        assert panel._buttons_row.indexOf(btn) >= 0

    def test_personas_row_exists_in_two_row_mode(self, qtbot, config):
        """The personas row contains the persona widgets."""
        from mchat.ui.provider_panel import ProviderPanel
        panel = ProviderPanel(config, font_size=14)
        qtbot.addWidget(panel)
        panel.set_personas(self._make_entries(5))
        # Personas row should have persona widgets
        assert panel._personas_row.count() > 0
