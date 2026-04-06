# ------------------------------------------------------------------
# Component: test_matrix_panel
# Responsibility: Tests for persona-keyed visibility matrix panel
#                 (Stage 4.1). The panel should show one row/column
#                 per persona (explicit or synthetic default), not
#                 per Provider enum member.
# Collaborators: ui.matrix_panel, models.message
# ------------------------------------------------------------------
from __future__ import annotations

import pytest

from mchat.models.message import Provider


class TestMatrixPanelPersonaKeying:
    """#83 — matrix panel keyed by persona_id, not Provider."""

    def test_set_personas_builds_grid(self, qtbot):
        from mchat.ui.matrix_panel import MatrixPanel
        panel = MatrixPanel()
        qtbot.addWidget(panel)
        # Two personas: a Claude persona and a GPT persona
        panel.set_personas([
            ("p_partner", "Partner", Provider.CLAUDE),
            ("p_evaluator", "Evaluator", Provider.OPENAI),
        ])
        # Grid should be visible with 2 entries
        assert panel.isVisible()
        assert len(panel._checkboxes) == 4  # 2x2 grid

    def test_headers_show_persona_labels(self, qtbot):
        from mchat.ui.matrix_panel import MatrixPanel
        panel = MatrixPanel()
        qtbot.addWidget(panel)
        panel.set_personas([
            ("p_partner", "Partner", Provider.CLAUDE),
            ("p_evaluator", "Evaluator", Provider.OPENAI),
        ])
        # The grid should use persona labels, not provider abbreviations
        # Check that _personas stores the labels
        assert panel._personas[0] == ("p_partner", "Partner", Provider.CLAUDE)

    def test_to_matrix_uses_persona_id_keys(self, qtbot):
        from mchat.ui.matrix_panel import MatrixPanel
        panel = MatrixPanel()
        qtbot.addWidget(panel)
        panel.set_personas([
            ("p_partner", "Partner", Provider.CLAUDE),
            ("p_evaluator", "Evaluator", Provider.OPENAI),
        ])
        # Toggle off: evaluator cannot see partner
        cb = panel._checkboxes[("p_evaluator", "p_partner")]
        cb.setChecked(False)
        matrix = panel.to_matrix()
        # Key should be persona_id, not provider.value
        assert "p_evaluator" in matrix
        assert "p_partner" not in matrix["p_evaluator"]

    def test_load_matrix_uses_persona_id_keys(self, qtbot):
        from mchat.ui.matrix_panel import MatrixPanel
        panel = MatrixPanel()
        qtbot.addWidget(panel)
        panel.set_personas([
            ("p_partner", "Partner", Provider.CLAUDE),
            ("p_evaluator", "Evaluator", Provider.OPENAI),
        ])
        panel.load_matrix({"p_evaluator": ["p_partner"]})
        # Evaluator sees partner but not itself in the allowlist
        # (self is always implicit)
        matrix = panel.to_matrix()
        assert "p_evaluator" not in matrix  # full visibility (partner allowed, self implicit)

    def test_synthetic_defaults_work_for_legacy(self, qtbot):
        """Legacy conversations with no explicit personas use synthetic
        defaults (persona_id == provider.value). The matrix should
        render identically to the old provider-keyed panel."""
        from mchat.ui.matrix_panel import MatrixPanel
        panel = MatrixPanel()
        qtbot.addWidget(panel)
        panel.set_personas([
            ("claude", "Claude", Provider.CLAUDE),
            ("openai", "GPT", Provider.OPENAI),
        ])
        panel.load_matrix({"openai": ["claude"]})
        matrix = panel.to_matrix()
        assert "openai" in matrix
        assert matrix["openai"] == ["claude"]

    def test_single_persona_hides_panel(self, qtbot):
        from mchat.ui.matrix_panel import MatrixPanel
        panel = MatrixPanel()
        qtbot.addWidget(panel)
        panel.set_personas([("p_partner", "Partner", Provider.CLAUDE)])
        assert not panel.isVisible()
