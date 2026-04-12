# ------------------------------------------------------------------
# Component: ProviderPanel
# Responsibility: Compose the toolbar bar between the chat view and
#                 the input area. Stage 4.5: one row per persona
#                 (model combo + include checkbox + spend label),
#                 keyed by persona_id. Owns styling (provider colours,
#                 waiting/retrying states) and model list population.
#                 #157: splits into two rows when >4 personas —
#                 persona widgets on top, action buttons on bottom.
# Collaborators: config, PySide6, pricing (format_cost)
# ------------------------------------------------------------------
from __future__ import annotations

import concurrent.futures
from typing import Callable

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from mchat.config import PROVIDER_META, Config
from mchat.models.message import Provider
from mchat.pricing import format_cost

# Each entry is (persona_id, display_label, provider).
PersonaEntry = tuple[str, str, Provider]

# Threshold: more than this many personas triggers two-row layout.
_TWO_ROW_THRESHOLD = 4


class ProviderPanel(QFrame):
    """Toolbar bar: one row per persona (combo + checkbox + spend).

    Emits signals keyed by persona_id for selection / model changes.
    The host drives the router, saves state, and reacts to changes.
    """

    # persona_id
    selection_changed = Signal(str)
    # persona_id
    combo_changed = Signal(str)
    # Emitted when the Personas... button in the empty state is clicked.
    personas_requested = Signal()

    def __init__(self, config: Config, font_size: int, parent=None) -> None:
        super().__init__(parent)
        self._config = config
        self._font_size = font_size
        self._personas: list[PersonaEntry] = []
        self._combos: dict[str, QComboBox] = {}
        self._checkboxes: dict[str, QCheckBox] = {}
        self._spend_labels: dict[str, QLabel] = {}
        self._persona_providers: dict[str, Provider] = {}
        self._row_widgets: list[QWidget] = []
        self._model_fetcher: QThread | None = None
        self._empty_hint: QLabel | None = None
        self._personas_btn: QPushButton | None = None
        self._two_row_mode: bool = False
        self._build_ui()

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self.setStyleSheet(
            "ProviderPanel { background-color: #f5f5f5; border-top: 1px solid #ddd; }"
        )
        # Top-level: vertical layout holding up to two horizontal rows.
        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(0, 0, 0, 0)
        self._outer.setSpacing(0)

        # Personas row — only visible in two-row mode.
        self._personas_row_widget = QWidget()
        self._personas_row = QHBoxLayout(self._personas_row_widget)
        self._personas_row.setContentsMargins(16, 8, 16, 2)
        self._personas_row.setSpacing(8)
        self._personas_row.addStretch()
        self._personas_row_widget.setVisible(False)
        self._outer.addWidget(self._personas_row_widget)

        # Buttons row — always visible. In single-row mode it also
        # holds the persona widgets; in two-row mode it holds only
        # the action buttons (right-aligned).
        self._buttons_row_widget = QWidget()
        self._buttons_row = QHBoxLayout(self._buttons_row_widget)
        self._buttons_row.setContentsMargins(16, 8, 16, 8)
        self._buttons_row.setSpacing(8)
        self._buttons_row.addStretch()
        self._outer.addWidget(self._buttons_row_widget)

        # Empty-state widgets (hidden by default) — live in buttons row.
        self._empty_hint = QLabel(
            'No personas yet \u2014 use <code>//addpersona &lt;provider&gt; as "&lt;name&gt;" ...'
            "</code> or click below"
        )
        self._empty_hint.setStyleSheet(
            f"color: #888; font-size: {self._font_size - 1}px; padding: 4px 8px;"
        )
        self._empty_hint.setVisible(False)
        self._buttons_row.insertWidget(0, self._empty_hint)

        self._personas_btn = QPushButton("Personas\u2026")
        self._personas_btn.setStyleSheet(
            "QPushButton { background: none; border: 1px solid #999; border-radius: 4px; "
            "padding: 4px 12px; color: #666; }"
            "QPushButton:hover { background-color: #eee; }"
        )
        self._personas_btn.setVisible(False)
        self._personas_btn.clicked.connect(self.personas_requested.emit)
        self._buttons_row.insertWidget(1, self._personas_btn)

    # ------------------------------------------------------------------
    # Persona rows
    # ------------------------------------------------------------------

    def set_personas(self, entries: list[PersonaEntry]) -> None:
        """(Re)build the bar with one row per persona entry.

        Each entry is ``(persona_id, display_label, provider)``.
        """
        # Clear existing persona widgets from whichever row they're in.
        for w in self._row_widgets:
            w.setParent(None)
            w.deleteLater()
        self._row_widgets.clear()
        self._combos.clear()
        self._checkboxes.clear()
        self._spend_labels.clear()
        self._persona_providers.clear()
        self._personas = list(entries)

        if not entries:
            self._two_row_mode = False
            self._personas_row_widget.setVisible(False)
            # In single-row mode, reduce top margin since no persona row.
            self._buttons_row.setContentsMargins(16, 8, 16, 8)
            self.show_empty_state()
            return

        # Hide empty state
        if self._empty_hint:
            self._empty_hint.setVisible(False)
        if self._personas_btn:
            self._personas_btn.setVisible(False)

        # Decide layout mode.
        self._two_row_mode = len(entries) > _TWO_ROW_THRESHOLD

        if self._two_row_mode:
            # Personas go in the top row, buttons stay in the bottom row.
            self._personas_row_widget.setVisible(True)
            self._buttons_row.setContentsMargins(16, 2, 16, 8)
            target_layout = self._personas_row
        else:
            # Single row: personas go in the buttons row (before the stretch).
            self._personas_row_widget.setVisible(False)
            self._buttons_row.setContentsMargins(16, 8, 16, 8)
            target_layout = self._buttons_row

        insert_pos = 0
        for i, (pid, label, provider) in enumerate(entries):
            self._persona_providers[pid] = provider

            if i > 0:
                spacer = QWidget()
                spacer.setFixedWidth(12)
                target_layout.insertWidget(insert_pos, spacer)
                self._row_widgets.append(spacer)
                insert_pos += 1

            # Persona label
            name_lbl = QLabel(label)
            name_lbl.setStyleSheet(f"color: #444; font-size: {self._font_size - 1}px; font-weight: bold;")
            target_layout.insertWidget(insert_pos, name_lbl)
            self._row_widgets.append(name_lbl)
            insert_pos += 1

            combo = QComboBox()
            combo.setMinimumWidth(140)
            combo.activated.connect(lambda _, c=combo: c.hidePopup())
            combo.currentTextChanged.connect(lambda _, p=pid: self.combo_changed.emit(p))
            target_layout.insertWidget(insert_pos, combo)
            self._row_widgets.append(combo)
            self._combos[pid] = combo
            insert_pos += 1

            cb = QCheckBox()
            cb.setToolTip(f"Include {label} in selection")
            cb.stateChanged.connect(lambda _, p=pid: self.selection_changed.emit(p))
            target_layout.insertWidget(insert_pos, cb)
            self._row_widgets.append(cb)
            self._checkboxes[pid] = cb
            insert_pos += 1

            spend_lbl = QLabel("$0.00000")
            self._apply_spend_label_style(spend_lbl)
            target_layout.insertWidget(insert_pos, spend_lbl)
            self._row_widgets.append(spend_lbl)
            self._spend_labels[pid] = spend_lbl
            insert_pos += 1

    def set_providers(self, configured: list[Provider] | set[Provider]) -> None:
        """Backwards-compat: builds persona entries from Provider list
        (synthetic defaults with persona_id == provider.value)."""
        entries: list[PersonaEntry] = [
            (p.value, PROVIDER_META[p.value]["display"], p)
            for p in Provider if p in set(configured)
        ]
        self.set_personas(entries)

    # ------------------------------------------------------------------
    # Empty state
    # ------------------------------------------------------------------

    def show_empty_state(self) -> None:
        for w in self._row_widgets:
            w.setVisible(False)
        if self._empty_hint:
            self._empty_hint.setVisible(True)
        if self._personas_btn:
            self._personas_btn.setVisible(True)

    def show_provider_rows(self) -> None:
        for w in self._row_widgets:
            w.setVisible(True)
        if self._empty_hint:
            self._empty_hint.setVisible(False)
        if self._personas_btn:
            self._personas_btn.setVisible(False)

    # ------------------------------------------------------------------
    # Public accessors
    # ------------------------------------------------------------------

    def combos(self) -> dict[str, QComboBox]:
        return self._combos

    def checkboxes(self) -> dict[str, QCheckBox]:
        return self._checkboxes

    def spend_labels(self) -> dict[str, QLabel]:
        return self._spend_labels

    def selected_model(self, persona_id: str) -> str:
        return self._combos[persona_id].currentText()

    def layout_ref(self) -> QHBoxLayout:
        """Return the buttons row layout. MainWindow adds action buttons
        (Cols, Personas, Providers, Settings) here."""
        return self._buttons_row

    # ------------------------------------------------------------------
    # Selection sync
    # ------------------------------------------------------------------

    def sync_checkboxes(self, selected_ids: set[str]) -> None:
        """Set checkbox state to match the given persona_id set."""
        for pid, cb in self._checkboxes.items():
            cb.blockSignals(True)
            cb.setChecked(pid in selected_ids)
            cb.blockSignals(False)

    def checked_persona_ids(self) -> list[str]:
        return [pid for pid, cb in self._checkboxes.items() if cb.isChecked()]

    # ------------------------------------------------------------------
    # Model combos
    # ------------------------------------------------------------------

    def set_persona_models(
        self,
        persona_id: str,
        models: list[str],
        current_override: str | None = None,
    ) -> None:
        """Fill a persona's model combo. First item is always
        'Use provider default'."""
        if persona_id not in self._combos:
            return
        combo = self._combos[persona_id]
        provider = self._persona_providers.get(persona_id)
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("Use provider default")
        if models:
            combo.addItems(models)
        if current_override:
            idx = combo.findText(current_override)
            if idx < 0:
                combo.addItem(current_override)
                idx = combo.findText(current_override)
            combo.setCurrentIndex(idx)
        else:
            combo.setCurrentIndex(0)
        combo.blockSignals(False)

    def populate_from_config(self, configured_providers: set[Provider]) -> None:
        """Fill combos with config defaults — no API calls."""
        for pid, _label, provider in self._personas:
            if provider in configured_providers:
                meta = PROVIDER_META[provider.value]
                current = self._config.get(meta["model_key"])
                self.set_persona_models(pid, [current] if current else [])

    def populate_async(
        self,
        providers: dict[Provider, object],
        on_done: Callable[[], None] | None = None,
    ) -> None:
        """Fetch model lists in background and update persona combos."""
        if not providers:
            return

        def fetch_all() -> dict[Provider, list[str]]:
            results: dict[Provider, list[str]] = {}
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                futures = {
                    pool.submit(prov.list_models): pid
                    for pid, prov in providers.items()
                }
                for future in concurrent.futures.as_completed(futures):
                    pid = futures[future]
                    try:
                        results[pid] = future.result()
                    except Exception:
                        results[pid] = []
            return results

        class _ModelFetcher(QThread):
            done = Signal(object)

            def run(self_inner):
                self_inner.done.emit(fetch_all())

        self._model_fetcher = _ModelFetcher()

        def _on_done(results: dict) -> None:
            for provider, models in results.items():
                if models:
                    # Update every persona that uses this provider
                    for pid, _label, prov in self._personas:
                        if prov == provider:
                            self.set_persona_models(pid, models)
            self._model_fetcher = None
            if on_done is not None:
                on_done()

        self._model_fetcher.done.connect(_on_done)
        self._model_fetcher.start()

    # ------------------------------------------------------------------
    # Styling
    # ------------------------------------------------------------------

    def _provider_color(self, provider: Provider) -> str:
        return self._config.get(PROVIDER_META[provider.value]["color_key"])

    def apply_combo_style(self, persona_id: str) -> None:
        provider = self._persona_providers.get(persona_id)
        if provider is None:
            return
        color = self._provider_color(provider)
        combo = self._combos[persona_id]
        combo.setStyleSheet(f"QComboBox {{ background-color: {color}; }}")

    def apply_all_combo_styles(self) -> None:
        for pid in self._combos:
            self.apply_combo_style(pid)

    def set_combo_waiting(self, persona_id: str, waiting: bool) -> None:
        if persona_id not in self._combos:
            return
        combo = self._combos[persona_id]
        if waiting:
            combo.setStyleSheet(
                "QComboBox { border: 2px solid #e8a020; background-color: #fff8e0; "
                "font-weight: bold; }"
            )
        else:
            self.apply_combo_style(persona_id)

    def set_combo_queued(self, persona_id: str) -> None:
        """Dark gray style for personas queued in sequential mode."""
        if persona_id not in self._combos:
            return
        combo = self._combos[persona_id]
        combo.setStyleSheet(
            "QComboBox { border: 1px solid #888; background-color: #d0d0d0; "
            "color: #666; font-style: italic; }"
        )

    def set_combo_retrying(self, persona_id: str) -> None:
        if persona_id not in self._combos:
            return
        combo = self._combos[persona_id]
        combo.setStyleSheet(
            "QComboBox { border: 2px solid #d04040; background-color: #ffe0e0; "
            "font-weight: bold; }"
        )

    def _apply_spend_label_style(self, label: QLabel) -> None:
        label.setStyleSheet(
            f"color: #666; font-size: {self._font_size - 1}px; padding: 0 4px;"
        )

    def update_font_size(self, size: int) -> None:
        self._font_size = size
        for label in self._spend_labels.values():
            self._apply_spend_label_style(label)

    # ------------------------------------------------------------------
    # Spend display
    # ------------------------------------------------------------------

    def update_spend(self, spend: dict[str, tuple[float, bool]]) -> None:
        for pid, label in self._spend_labels.items():
            entry = spend.get(pid)
            if entry:
                amount, estimated = entry
                text = format_cost(amount) if amount else "$0.00000"
                if estimated:
                    label.setText(f"<i>{text}</i>")
                else:
                    label.setText(text)
            else:
                label.setText("$0.00000")
