# ------------------------------------------------------------------
# Component: ProviderPanel
# Responsibility: Compose the per-provider bar that sits between the
#                 chat view and the input area: model combos, include
#                 checkboxes, and spend labels. Owns all their styling
#                 (provider colours, waiting/retrying states) and model
#                 list fetching (fast local, async background refresh).
# Collaborators: config, router, PySide6, pricing (format_cost)
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
    QWidget,
)

from mchat.config import PROVIDER_META, Config
from mchat.models.message import Provider
from mchat.pricing import format_cost

_PROVIDER_DISPLAY = {p: PROVIDER_META[p.value]["display"] for p in Provider}


class ProviderPanel(QFrame):
    """The per-provider bar (combos + checkboxes + spend labels).

    Emits signals for selection / model-selection changes. The host
    is responsible for driving the router, saving state, and reacting
    to combo changes (e.g. restyling the input background).
    """

    # pid
    selection_changed = Signal(Provider)
    # pid
    combo_changed = Signal(Provider)

    # Emitted when the Personas... button in the empty state is clicked.
    personas_requested = Signal()

    def __init__(self, config: Config, font_size: int, parent=None) -> None:
        super().__init__(parent)
        self._config = config
        self._font_size = font_size
        self._combos: dict[Provider, QComboBox] = {}
        self._checkboxes: dict[Provider, QCheckBox] = {}
        self._spend_labels: dict[Provider, QLabel] = {}
        self._model_fetcher: QThread | None = None
        self._empty_hint: QLabel | None = None
        self._personas_btn: QPushButton | None = None
        self._build_ui()

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self.setStyleSheet(
            "ProviderPanel { background-color: #f5f5f5; border-top: 1px solid #ddd; }"
        )
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(16, 8, 16, 8)
        self._layout.setSpacing(8)

        for i, p in enumerate(Provider):
            if i > 0:
                self._layout.addSpacing(12)

            combo = QComboBox()
            combo.setMinimumWidth(160)
            combo.activated.connect(lambda _, c=combo: c.hidePopup())
            combo.currentTextChanged.connect(lambda _, pid=p: self.combo_changed.emit(pid))
            self._layout.addWidget(combo)
            self._combos[p] = combo

            cb = QCheckBox()
            cb.setToolTip(f"Include {_PROVIDER_DISPLAY[p]} in selection")
            cb.stateChanged.connect(lambda _, pid=p: self.selection_changed.emit(pid))
            self._layout.addWidget(cb)
            self._checkboxes[p] = cb

            label = QLabel("$0.00000")
            self._apply_spend_label_style(label)
            self._layout.addWidget(label)
            self._spend_labels[p] = label

        self._layout.addStretch()

        # Empty-state widgets (hidden by default — shown via show_empty_state)
        self._empty_hint = QLabel(
            'No personas yet \u2014 use <code>//addpersona &lt;provider&gt; as "&lt;name&gt;" ...'
            "</code> or click below"
        )
        self._empty_hint.setStyleSheet(
            f"color: #888; font-size: {self._font_size - 1}px; padding: 4px 8px;"
        )
        self._empty_hint.setVisible(False)
        self._layout.insertWidget(0, self._empty_hint)

        self._personas_btn = QPushButton("Personas\u2026")
        self._personas_btn.setStyleSheet(
            "QPushButton { background: none; border: 1px solid #999; border-radius: 4px; "
            "padding: 4px 12px; color: #666; }"
            "QPushButton:hover { background-color: #eee; }"
        )
        self._personas_btn.setVisible(False)
        self._personas_btn.clicked.connect(self.personas_requested.emit)
        self._layout.insertWidget(1, self._personas_btn)

    # ------------------------------------------------------------------
    # Empty state / provider rows toggle (Stage 3A.4)
    # ------------------------------------------------------------------

    def show_empty_state(self) -> None:
        """Hide provider rows and show the empty-state hint + Personas button."""
        for combo in self._combos.values():
            combo.setVisible(False)
        for cb in self._checkboxes.values():
            cb.setVisible(False)
        for label in self._spend_labels.values():
            label.setVisible(False)
        if self._empty_hint:
            self._empty_hint.setVisible(True)
        if self._personas_btn:
            self._personas_btn.setVisible(True)

    def show_provider_rows(self) -> None:
        """Show provider rows and hide the empty-state hint."""
        for combo in self._combos.values():
            combo.setVisible(True)
        for cb in self._checkboxes.values():
            cb.setVisible(True)
        for label in self._spend_labels.values():
            label.setVisible(True)
        if self._empty_hint:
            self._empty_hint.setVisible(False)
        if self._personas_btn:
            self._personas_btn.setVisible(False)

    # ------------------------------------------------------------------
    # Public accessors (used by MainWindow)
    # ------------------------------------------------------------------

    def combos(self) -> dict[Provider, QComboBox]:
        return self._combos

    def checkboxes(self) -> dict[Provider, QCheckBox]:
        return self._checkboxes

    def spend_labels(self) -> dict[Provider, QLabel]:
        return self._spend_labels

    def selected_model(self, p: Provider) -> str:
        return self._combos[p].currentText()

    def layout_ref(self) -> QHBoxLayout:
        """Expose the HBox so MainWindow can append trailing widgets
        (column button, settings button) after the panel is built."""
        return self._layout

    # ------------------------------------------------------------------
    # Selection sync
    # ------------------------------------------------------------------

    def sync_checkboxes(self, selected: set[Provider]) -> None:
        """Set checkbox state to match the given selection without
        re-emitting stateChanged signals."""
        for p, cb in self._checkboxes.items():
            cb.blockSignals(True)
            cb.setChecked(p in selected)
            cb.blockSignals(False)

    def checked_providers(self) -> list[Provider]:
        return [p for p, cb in self._checkboxes.items() if cb.isChecked()]

    # ------------------------------------------------------------------
    # Model combos
    # ------------------------------------------------------------------

    def set_models(
        self,
        p: Provider,
        models: list[str],
        configured_providers: set[Provider],
    ) -> None:
        """Fill a combo's model list, preserve the current selection,
        and disable combo + checkbox when the provider has no API key."""
        combo = self._combos[p]
        meta = PROVIDER_META[p.value]
        current = combo.currentText() or self._config.get(meta["model_key"])
        combo.blockSignals(True)
        combo.clear()
        if models:
            combo.addItems(models)
        if current and combo.findText(current) < 0:
            combo.insertItem(0, current)
        if not combo.count() and current:
            combo.addItem(current)
        idx = combo.findText(current)
        if idx >= 0:
            combo.setCurrentIndex(idx)
        combo.setEnabled(p in configured_providers)
        combo.blockSignals(False)
        self._checkboxes[p].setEnabled(p in configured_providers)

    def populate_from_config(self, configured_providers: set[Provider]) -> None:
        """Fill combos with config defaults only — no API calls."""
        for p in Provider:
            meta = PROVIDER_META[p.value]
            current = self._config.get(meta["model_key"])
            self.set_models(p, [current] if current else [], configured_providers)

    def populate_from_providers(
        self,
        providers: dict[Provider, object],
    ) -> None:
        """Full synchronous populate — calls provider.list_models() directly."""
        configured = set(providers.keys())
        for p in Provider:
            provider = providers.get(p)
            models: list[str] = []
            if provider:
                try:
                    models = provider.list_models()
                except Exception:
                    models = []
            self.set_models(p, models, configured)

    def populate_async(
        self,
        providers: dict[Provider, object],
        on_done: Callable[[], None] | None = None,
    ) -> None:
        """Fetch model lists in a background QThread and update combos
        on the main thread when done. The optional callback runs after
        combos have been updated."""
        if not providers:
            return
        configured = set(providers.keys())

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
            for p, models in results.items():
                if models:
                    self.set_models(p, models, configured)
            self._model_fetcher = None
            if on_done is not None:
                on_done()

        self._model_fetcher.done.connect(_on_done)
        self._model_fetcher.start()

    # ------------------------------------------------------------------
    # Styling
    # ------------------------------------------------------------------

    def _provider_color(self, p: Provider) -> str:
        return self._config.get(PROVIDER_META[p.value]["color_key"])

    def apply_combo_provider_style(self, p: Provider) -> None:
        color = self._provider_color(p)
        combo = self._combos[p]
        if combo.isEnabled():
            combo.setStyleSheet(f"QComboBox {{ background-color: {color}; }}")
        else:
            combo.setStyleSheet(
                "QComboBox { background-color: #e0e0e0; color: #999; }"
            )

    def apply_all_combo_styles(self) -> None:
        for p in Provider:
            self.apply_combo_provider_style(p)

    def set_combo_waiting(self, p: Provider, waiting: bool) -> None:
        combo = self._combos[p]
        if waiting:
            combo.setStyleSheet(
                "QComboBox { border: 2px solid #e8a020; background-color: #fff8e0; "
                "font-weight: bold; }"
            )
        else:
            self.apply_combo_provider_style(p)

    def set_combo_retrying(self, p: Provider) -> None:
        combo = self._combos[p]
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
        for p in Provider:
            label = self._spend_labels[p]
            entry = spend.get(p.value)
            if entry:
                amount, estimated = entry
                text = format_cost(amount) if amount else "$0.00000"
                if estimated:
                    label.setText(f"<i>{text}</i>")
                else:
                    label.setText(text)
            else:
                label.setText("$0.00000")
