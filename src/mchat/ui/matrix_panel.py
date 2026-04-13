# ------------------------------------------------------------------
# Component: MatrixPanel
# Responsibility: Compact checkbox grid that lets the user control
#                 which source provider's responses each observer
#                 provider sees when context is built for a request.
#                 Only configured providers are shown — unconfigured
#                 providers are hidden entirely and reappear as soon as
#                 their API key is added.
# Collaborators: models.message, config  (external: PySide6)
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
    The emitted dict uses persona_id strings as keys and lists of
    persona_id strings as allowlists (observer's own id is always
    implicit and never stored). Observers with full visibility are
    omitted from the dict, so the default is cheap.

    Stage 4.1: keyed by persona (persona_id, label, provider) instead
    of Provider enum members. Legacy conversations use synthetic
    defaults where persona_id == provider.value.
    """

    matrix_changed = Signal(dict)

    # Each entry is (persona_id, display_label, provider).
    PersonaEntry = tuple[str, str, Provider]

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        # Persistent per-pair state — survives rebuilds of the grid.
        # Key = (observer_persona_id, source_persona_id); value = visible?
        self._state: dict[tuple[str, str], bool] = {}
        self._personas: list[MatrixPanel.PersonaEntry] = []
        self._checkboxes: dict[tuple[str, str], QCheckBox] = {}
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

    def set_personas(self, entries: list[PersonaEntry]) -> None:
        """(Re)build the grid to show one row/column per persona entry.

        Each entry is ``(persona_id, display_label, provider)``.
        State for personas that remain visible is preserved across the
        rebuild; personas that were removed keep their state cached.
        """
        if entries == self._personas and self._grid_container is not None:
            return
        self._personas = list(entries)
        self._rebuild_grid()

    def set_providers(self, configured: list[Provider] | set[Provider]) -> None:
        """Backwards-compat wrapper: builds synthetic-default persona
        entries from a list of Provider enum members."""
        entries: list[MatrixPanel.PersonaEntry] = [
            (p.value, PROVIDER_META[p.value]["display"], p)
            for p in Provider if p in set(configured)
        ]
        self.set_personas(entries)

    def load_matrix(self, matrix: dict[str, list[str]]) -> None:
        """Populate state from a stored matrix dict (observer_id -> allowlist)."""
        self._loading = True
        try:
            ids = [pid for pid, _label, _prov in self._personas]
            for obs_id in ids:
                allowed = matrix.get(obs_id)
                for src_id in ids:
                    if obs_id == src_id:
                        continue
                    visible = True if allowed is None else (src_id in allowed)
                    self._state[(obs_id, src_id)] = visible
            for (obs_id, src_id), cb in self._checkboxes.items():
                if obs_id == src_id:
                    continue
                cb.setChecked(self._state.get((obs_id, src_id), True))
        finally:
            self._loading = False

    def to_matrix(self) -> dict[str, list[str]]:
        """Serialize full state to dict form (persona_id keys)."""
        ids = [pid for pid, _label, _prov in self._personas]
        result: dict[str, list[str]] = {}
        for obs_id in ids:
            allowed: list[str] = []
            full = True
            for src_id in ids:
                if obs_id == src_id:
                    continue
                if self._state.get((obs_id, src_id), True):
                    allowed.append(src_id)
                else:
                    full = False
            if not full:
                result[obs_id] = allowed
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

        if len(self._personas) < 2:
            self.setVisible(False)
            return
        self.setVisible(True)

        container = QWidget()
        grid = QGridLayout(container)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(4)
        grid.setVerticalSpacing(2)

        # Header row: persona labels
        for j, (_sid, label, _prov) in enumerate(self._personas):
            lbl = QLabel(_short_label(label))
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet("color: #666; font-size: 10px;")
            grid.addWidget(lbl, 0, j + 1)

        # Rows
        for i, (obs_id, obs_label, _obs_prov) in enumerate(self._personas):
            lbl = QLabel(_short_label(obs_label))
            lbl.setStyleSheet("color: #666; font-size: 10px;")
            grid.addWidget(lbl, i + 1, 0)
            for j, (src_id, _src_label, _src_prov) in enumerate(self._personas):
                cb = QCheckBox()
                if obs_id == src_id:
                    cb.setChecked(True)
                    cb.setEnabled(False)
                else:
                    cb.setChecked(self._state.get((obs_id, src_id), True))
                    cb.toggled.connect(
                        lambda checked, o=obs_id, s=src_id: self._on_toggle(o, s, checked)
                    )
                self._checkboxes[(obs_id, src_id)] = cb
                grid.addWidget(cb, i + 1, j + 1, alignment=Qt.AlignmentFlag.AlignCenter)

        self._outer.addWidget(container)
        self._grid_container = container

    def _on_toggle(self, obs_id: str, src_id: str, checked: bool) -> None:
        self._state[(obs_id, src_id)] = checked
        if self._loading:
            return
        self.matrix_changed.emit(self.to_matrix())


def _short_label(label: str) -> str:
    """Return a short label for the header cells."""
    return label[:3] if len(label) > 3 else label
