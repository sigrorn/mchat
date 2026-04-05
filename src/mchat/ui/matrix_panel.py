# ------------------------------------------------------------------
# Component: MatrixPanel
# Responsibility: Compact N×N checkbox grid that lets the user control
#                 which source provider's responses each observer
#                 provider sees when context is built for a request.
# Collaborators: PySide6, models.message, config
# ------------------------------------------------------------------
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QGridLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from mchat.config import PROVIDER_META
from mchat.models.message import Provider


class MatrixPanel(QWidget):
    """Row-observer / column-source visibility matrix.

    Emits ``matrix_changed(dict)`` whenever the user toggles a cell.
    The emitted dict uses provider enum *values* as keys and lists of
    provider enum values as allowlists (observer's own value is always
    implicit and never stored).
    """

    matrix_changed = Signal(dict)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._providers = list(Provider)
        self._checkboxes: dict[tuple[Provider, Provider], QCheckBox] = {}
        self._loading = False  # suppress signals while programmatically updating
        self._build_ui()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 4, 8, 4)
        outer.setSpacing(2)

        title = QLabel("Visibility (row sees column)")
        title.setStyleSheet("color: #666; font-size: 10px;")
        outer.addWidget(title)

        grid = QGridLayout()
        grid.setHorizontalSpacing(4)
        grid.setVerticalSpacing(2)
        outer.addLayout(grid)

        # Header row: source provider labels
        for j, src in enumerate(self._providers):
            lbl = QLabel(_short(src))
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet("color: #666; font-size: 10px;")
            grid.addWidget(lbl, 0, j + 1)

        # Rows: observer provider label + N checkboxes
        for i, obs in enumerate(self._providers):
            lbl = QLabel(_short(obs))
            lbl.setStyleSheet("color: #666; font-size: 10px;")
            grid.addWidget(lbl, i + 1, 0)
            for j, src in enumerate(self._providers):
                cb = QCheckBox()
                cb.setChecked(True)
                if obs == src:
                    # Diagonal: always on, cannot be changed
                    cb.setEnabled(False)
                else:
                    cb.toggled.connect(self._on_toggle)
                self._checkboxes[(obs, src)] = cb
                grid.addWidget(cb, i + 1, j + 1, alignment=Qt.AlignmentFlag.AlignCenter)

    def set_configured(self, configured: set[Provider]) -> None:
        """Grey out rows/columns for providers that have no API key."""
        for (obs, src), cb in self._checkboxes.items():
            if obs == src:
                continue  # diagonal stays disabled
            cb.setEnabled(obs in configured and src in configured)

    def load_matrix(self, matrix: dict[str, list[str]]) -> None:
        """Populate checkboxes from a stored matrix dict."""
        self._loading = True
        try:
            for (obs, src), cb in self._checkboxes.items():
                if obs == src:
                    continue
                allowed = matrix.get(obs.value)
                if allowed is None:
                    cb.setChecked(True)  # missing observer = full visibility
                else:
                    cb.setChecked(src.value in allowed)
        finally:
            self._loading = False

    def to_matrix(self) -> dict[str, list[str]]:
        """Serialize current checkbox state to the canonical dict form.

        Observers with full visibility (all off-diagonal cells ticked) are
        omitted from the dict so the default is cheap to represent.
        """
        result: dict[str, list[str]] = {}
        for obs in self._providers:
            allowed: list[str] = []
            full = True
            for src in self._providers:
                if obs == src:
                    continue
                if self._checkboxes[(obs, src)].isChecked():
                    allowed.append(src.value)
                else:
                    full = False
            if not full:
                result[obs.value] = allowed
        return result

    def _on_toggle(self, _checked: bool) -> None:
        if self._loading:
            return
        self.matrix_changed.emit(self.to_matrix())


def _short(p: Provider) -> str:
    """Return a short label for the header cells."""
    display = PROVIDER_META[p.value]["display"]
    return display[:3] if len(display) > 3 else display
