# ------------------------------------------------------------------
# Component: ProvidersDialog
# Responsibility: Tabbed dialog for per-provider configuration — one
#                 tab per provider with API key, model combo,
#                 provider colour, and provider system prompt. Split
#                 out of the monolithic SettingsDialog so general
#                 preferences and provider credentials each live in
#                 their own dialog.
# Collaborators: PySide6, config, providers
# ------------------------------------------------------------------
from __future__ import annotations

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from mchat.config import Config, DEFAULTS, PROVIDER_META
from mchat.models.message import Provider
from mchat.providers.base import BaseProvider


class ProvidersDialog(QDialog):
    """Tabbed provider-configuration editor. One tab per provider;
    each tab owns the provider's API key, model combo, colour, and
    provider-specific system prompt baseline."""

    def __init__(
        self,
        config: Config,
        providers: dict | None = None,
        models_cache: dict | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._config = config
        self._providers = providers or {}
        # Pre-fetched model lists keyed by Provider enum — when
        # supplied we use them and never call provider.list_models()
        # during build, so opening the dialog doesn't block on a
        # network call. Populated by MainWindow's ModelCatalog.
        self._models_cache = models_cache or {}
        self.setWindowTitle("Providers")
        self.setMinimumWidth(560)
        self.setMinimumHeight(520)

        # Widgets keyed by provider value string
        self._api_key_edits: dict[str, QLineEdit] = {}
        self._model_combos: dict[str, QComboBox] = {}
        self._color_btns: dict[str, QPushButton] = {}
        self._system_prompt_edits: dict[str, QPlainTextEdit] = {}

        self._build_ui()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)

        self._tabs = QTabWidget()
        outer.addWidget(self._tabs, stretch=1)

        for pv, meta in PROVIDER_META.items():
            tab = self._build_provider_tab(pv, meta)
            self._tabs.addTab(tab, meta["display"])

        # Footer: reset-colours and save/cancel
        footer = QHBoxLayout()

        reset_btn = QPushButton("Reset colours to defaults")
        reset_btn.setStyleSheet(
            "QPushButton { background: none; border: 1px solid #999; border-radius: 4px; "
            "padding: 4px 12px; color: #666; }"
            "QPushButton:hover { background-color: #eee; }"
        )
        reset_btn.clicked.connect(self._reset_colors)
        footer.addWidget(reset_btn)
        footer.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        footer.addWidget(cancel_btn)

        save_btn = QPushButton("Save")
        save_btn.setDefault(True)
        save_btn.setStyleSheet(
            "QPushButton { background-color: #6b5ce7; color: white; border: none; "
            "border-radius: 6px; padding: 8px 24px; font-weight: bold; }"
            "QPushButton:hover { background-color: #5a4bd6; }"
        )
        save_btn.clicked.connect(self._save)
        footer.addWidget(save_btn)

        outer.addSpacing(8)
        outer.addLayout(footer)

    def _build_provider_tab(self, pv: str, meta: dict) -> QWidget:
        tab = QWidget()
        form = QFormLayout(tab)
        form.setSpacing(12)
        form.setContentsMargins(16, 16, 16, 16)

        display = meta["display"]

        # API key (password-style, so it's masked)
        key_edit = QLineEdit(self._config.get(meta["api_key"]))
        key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        key_edit.setPlaceholderText(f"{display} API key")
        form.addRow("API key:", key_edit)
        self._api_key_edits[pv] = key_edit

        # Model combo — populated from cache or provider.list_models()
        model_combo = QComboBox()
        self._populate_model_combo(
            model_combo, pv, self._config.get(meta["model_key"])
        )
        form.addRow("Model:", model_combo)
        self._model_combos[pv] = model_combo

        # Provider colour
        color_btn = self._make_color_btn(self._config.get(meta["color_key"]))
        form.addRow("Colour:", color_btn)
        self._color_btns[pv] = color_btn

        # Provider system prompt (baseline — every persona for this
        # provider inherits this unless its system_prompt_override is set).
        sp_edit = QPlainTextEdit(self._config.get(meta["system_prompt_key"]))
        sp_edit.setPlaceholderText(
            f"Baseline system prompt for every {display} persona "
            f"(unless the persona overrides it)..."
        )
        form.addRow("System prompt:", sp_edit)
        self._system_prompt_edits[pv] = sp_edit

        return tab

    # ------------------------------------------------------------------
    # Model combo population
    # ------------------------------------------------------------------

    def _populate_model_combo(
        self, combo: QComboBox, provider_key: str, current_model: str
    ) -> None:
        try:
            provider_enum = Provider(provider_key)
        except ValueError:
            if current_model:
                combo.addItem(current_model)
            return

        models: list[str] = list(self._models_cache.get(provider_enum, []))
        if not models:
            provider: BaseProvider | None = self._providers.get(provider_enum)
            if provider:
                try:
                    models = provider.list_models()
                except Exception:
                    models = []
        if not models and current_model:
            models = [current_model]
        combo.addItems(models)

        if current_model and combo.findText(current_model) < 0:
            combo.insertItem(0, current_model)
        idx = combo.findText(current_model)
        if idx >= 0:
            combo.setCurrentIndex(idx)

    # ------------------------------------------------------------------
    # Colour helpers
    # ------------------------------------------------------------------

    def _make_color_btn(self, hex_color: str) -> QPushButton:
        btn = QPushButton()
        btn.setFixedSize(80, 28)
        btn.setProperty("hex_color", hex_color)
        self._apply_color_btn_style(btn, hex_color)
        btn.clicked.connect(lambda _, b=btn: self._pick_color(b))
        return btn

    @staticmethod
    def _apply_color_btn_style(btn: QPushButton, hex_color: str) -> None:
        btn.setStyleSheet(
            f"QPushButton {{ background-color: {hex_color}; border: 1px solid #999; "
            f"border-radius: 4px; }}"
        )
        btn.setText(hex_color)

    def _reset_colors(self) -> None:
        for pv, meta in PROVIDER_META.items():
            btn = self._color_btns[pv]
            default = DEFAULTS[meta["color_key"]]
            btn.setProperty("hex_color", default)
            self._apply_color_btn_style(btn, default)

    def _pick_color(self, btn: QPushButton) -> None:
        current = QColor(btn.property("hex_color"))
        color = QColorDialog.getColor(current, self, "Pick a colour")
        if color.isValid():
            hex_color = color.name()
            btn.setProperty("hex_color", hex_color)
            self._apply_color_btn_style(btn, hex_color)

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------

    def _save(self) -> None:
        """Persist only the provider-specific fields — general config
        keys (font, shading, global prompt, user colour) are left
        alone so SettingsDialog can own them safely."""
        for pv, meta in PROVIDER_META.items():
            self._config.set(
                meta["api_key"],
                self._api_key_edits[pv].text().strip(),
            )
            self._config.set(
                meta["model_key"],
                self._model_combos[pv].currentText(),
            )
            self._config.set(
                meta["color_key"],
                self._color_btns[pv].property("hex_color"),
            )
            self._config.set(
                meta["system_prompt_key"],
                self._system_prompt_edits[pv].toPlainText().strip(),
            )
        self._config.save()
        self.accept()
