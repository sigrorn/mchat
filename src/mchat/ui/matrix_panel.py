# ------------------------------------------------------------------
# Component: MatrixPanel
# Responsibility: Compact checkbox grid that lets the user control
#                 which source provider's responses each observer
#                 provider sees when context is built for a request.
#                 Only configured providers are shown — unconfigured
#                 providers are hidden entirely and reappear as soon as
#                 their API key is added.
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
    implicit and never stored). Observers with full visibility are
    omitted from the dict, so the default is cheap.
    """

    matrix_changed = Signal(dict)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        # Persistent per-pair state — survives rebuilds of the grid.
        # Key = (observer, source); value = visible? (True by default).
        self._state: dict[tuple[Provider, Provider], bool] = {}
        self._providers: list[Provider] = []  # currently displayed
        self._checkboxes: dict[tuple[Provider, Provider], QCheckBox] = {}
        self._loading = False

        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(8, 4, 8, 4)
        self._outer.setSpacing(2)

        self._title = QLabel("Visibility (row sees column)")
        self._title.setStyleSheet("color: #666; font-size: 10px;")
        self._outer.addWidget(self._title)

        # Grid lives inside a container widget so we can drop+recreate it
        # cleanly when the set of configured providers changes.
        self._grid_container: QWidget | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_providers(self, configured: list[Provider] | set[Provider]) -> None:
        """(Re)build the grid to show only the given configured providers.

        State for providers that remain visible is preserved across the
        rebuild; providers that were removed keep their state cached in
        case they come back later.
        """
        # Preserve insertion order from Provider enum for a stable layout
        ordered = [p for p in Provider if p in set(configured)]
        if ordered == self._providers and self._grid_container is not None:
            return  # nothing to do
        self._providers = ordered
        self._rebuild_grid()

    def load_matrix(self, matrix: dict[str, list[str]]) -> None:
        """Populate state from a stored matrix dict (observer -> allowlist)."""
        # Reset to full visibility, then apply restrictions from the dict.
        self._loading = True
        try:
            for obs in Provider:
                allowed = matrix.get(obs.value)
                for src in Provider:
                    if obs == src:
                        continue
                    visible = True if allowed is None else (src.value in allowed)
                    self._state[(obs, src)] = visible
            # Push into any currently-visible checkboxes
            for (obs, src), cb in self._checkboxes.items():
                if obs == src:
                    continue
                cb.setChecked(self._state.get((obs, src), True))
        finally:
            self._loading = False

    def to_matrix(self) -> dict[str, list[str]]:
        """Serialize full state (including hidden providers) to the dict form."""
        result: dict[str, list[str]] = {}
        for obs in Provider:
            allowed: list[str] = []
            full = True
            for src in Provider:
                if obs == src:
                    continue
                if self._state.get((obs, src), True):
                    allowed.append(src.value)
                else:
                    full = False
            if not full:
                result[obs.value] = allowed
        return result

    # ------------------------------------------------------------------
    # Grid construction
    # ------------------------------------------------------------------

    def _rebuild_grid(self) -> None:
        if self._grid_container is not None:
            self._outer.removeWidget(self._grid_container)
            self._grid_container.deleteLater()
            self._grid_container = None
        self._checkboxes.clear()

        if len(self._providers) < 2:
            # Nothing meaningful to show with 0 or 1 providers.
            self.setVisible(False)
            return
        self.setVisible(True)

        container = QWidget()
        grid = QGridLayout(container)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(4)
        grid.setVerticalSpacing(2)

        # Header row: source labels
        for j, src in enumerate(self._providers):
            lbl = QLabel(_short(src))
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet("color: #666; font-size: 10px;")
            grid.addWidget(lbl, 0, j + 1)

        # Rows
        for i, obs in enumerate(self._providers):
            lbl = QLabel(_short(obs))
            lbl.setStyleSheet("color: #666; font-size: 10px;")
            grid.addWidget(lbl, i + 1, 0)
            for j, src in enumerate(self._providers):
                cb = QCheckBox()
                if obs == src:
                    cb.setChecked(True)
                    cb.setEnabled(False)  # diagonal always on
                else:
                    cb.setChecked(self._state.get((obs, src), True))
                    cb.toggled.connect(
                        lambda checked, o=obs, s=src: self._on_toggle(o, s, checked)
                    )
                self._checkboxes[(obs, src)] = cb
                grid.addWidget(cb, i + 1, j + 1, alignment=Qt.AlignmentFlag.AlignCenter)

        self._outer.addWidget(container)
        self._grid_container = container

    def _on_toggle(self, obs: Provider, src: Provider, checked: bool) -> None:
        self._state[(obs, src)] = checked
        if self._loading:
            return
        self.matrix_changed.emit(self.to_matrix())


def _short(p: Provider) -> str:
    """Return a short label for the header cells."""
    display = PROVIDER_META[p.value]["display"]
    return display[:3] if len(display) > 3 else display
