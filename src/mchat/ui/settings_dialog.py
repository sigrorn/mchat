# ------------------------------------------------------------------
# Component: SettingsDialog
# Responsibility: UI for managing API keys and settings
# Collaborators: PySide6, config, providers
# ------------------------------------------------------------------
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from mchat.config import Config, DEFAULTS, MAX_FONT_SIZE, MIN_FONT_SIZE, PROVIDER_META
from mchat.models.message import Provider
from mchat.providers.base import BaseProvider


class SettingsDialog(QDialog):
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
        # Optional pre-fetched model lists keyed by Provider enum — when
        # supplied, the dialog uses them instead of calling list_models()
        # (which may hit the network) during _build_ui.
        self._models_cache = models_cache or {}
        self.setWindowTitle("Settings")
        self.setMinimumWidth(500)
        self.setMinimumHeight(500)

        # Dynamic widgets keyed by provider value string
        self._api_key_edits: dict[str, QLineEdit] = {}
        self._model_combos: dict[str, QComboBox] = {}
        self._color_btns: dict[str, QPushButton] = {}
        self._system_prompt_edits: dict[str, QPlainTextEdit] = {}

        self._build_ui()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)

        # Scrollable form
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        form_widget = QWidget()
        form = QFormLayout(form_widget)
        form.setSpacing(12)
        scroll.setWidget(form_widget)
        outer.addWidget(scroll, stretch=1)

        # --- Per-provider settings ---
        for pv, meta in PROVIDER_META.items():
            display = meta["display"]

            # API key
            key_edit = QLineEdit(self._config.get(meta["api_key"]))
            key_edit.setEchoMode(QLineEdit.EchoMode.Password)
            form.addRow(f"{display} API Key:", key_edit)
            self._api_key_edits[pv] = key_edit

            # Model
            model_combo = QComboBox()
            self._populate_model_combo(model_combo, pv, self._config.get(meta["model_key"]))
            form.addRow(f"{display} Model:", model_combo)
            self._model_combos[pv] = model_combo

            # Colour
            color_btn = self._make_color_btn(self._config.get(meta["color_key"]))
            form.addRow(f"{display} colour:", color_btn)
            self._color_btns[pv] = color_btn

            # Provider-specific system prompt
            sp_edit = QPlainTextEdit(self._config.get(meta["system_prompt_key"]))
            sp_edit.setMaximumHeight(60)
            sp_edit.setPlaceholderText(f"Additional instructions for {display} (optional)...")
            form.addRow(f"{display} prompt:", sp_edit)
            self._system_prompt_edits[pv] = sp_edit

        # User colour
        self._color_user_btn = self._make_color_btn(self._config.get("color_user"))
        form.addRow("User colour:", self._color_user_btn)

        reset_colors_btn = QPushButton("Reset colours to defaults")
        reset_colors_btn.setStyleSheet(
            "QPushButton { background: none; border: 1px solid #999; border-radius: 4px; "
            "padding: 4px 12px; color: #666; }"
            "QPushButton:hover { background-color: #eee; }"
        )
        reset_colors_btn.clicked.connect(self._reset_colors)
        form.addRow("", reset_colors_btn)

        # Exclude shading (for messages outside //limit)
        self._exclude_shade_mode = QComboBox()
        self._exclude_shade_mode.addItems(["darken", "lighten"])
        current_mode = str(self._config.get("exclude_shade_mode") or "darken")
        idx = self._exclude_shade_mode.findText(current_mode)
        if idx >= 0:
            self._exclude_shade_mode.setCurrentIndex(idx)
        form.addRow("Excluded shading:", self._exclude_shade_mode)

        self._exclude_shade_amount = QSpinBox()
        self._exclude_shade_amount.setRange(0, 100)
        self._exclude_shade_amount.setSuffix(" %")
        self._exclude_shade_amount.setValue(int(self._config.get("exclude_shade_amount") or 20))
        form.addRow("Shading amount:", self._exclude_shade_amount)

        # --- General settings ---
        # Default provider
        self._default_provider = QComboBox()
        self._default_provider.addItems([p.value for p in Provider])
        current = self._config.get("default_provider")
        idx = self._default_provider.findText(current)
        if idx >= 0:
            self._default_provider.setCurrentIndex(idx)
        form.addRow("Default Provider:", self._default_provider)

        # System prompt
        self._system_prompt = QPlainTextEdit(self._config.get("system_prompt"))
        self._system_prompt.setMaximumHeight(100)
        self._system_prompt.setPlaceholderText("System prompt sent at the start of new chats...")
        form.addRow("System Prompt:", self._system_prompt)

        # Font size
        self._font_size = QSpinBox()
        self._font_size.setRange(MIN_FONT_SIZE, MAX_FONT_SIZE)
        self._font_size.setSuffix(" px")
        self._font_size.setValue(int(self._config.get("font_size") or 14))
        form.addRow("Font Size:", self._font_size)

        # --- Buttons ---
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        save_btn = QPushButton("Save")
        save_btn.setDefault(True)
        save_btn.setStyleSheet(
            "QPushButton { background-color: #6b5ce7; color: white; border: none; "
            "border-radius: 6px; padding: 8px 24px; font-weight: bold; }"
            "QPushButton:hover { background-color: #5a4bd6; }"
        )
        save_btn.clicked.connect(self._save)
        btn_layout.addWidget(save_btn)

        outer.addSpacing(8)
        outer.addLayout(btn_layout)

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
        user_default = DEFAULTS["color_user"]
        self._color_user_btn.setProperty("hex_color", user_default)
        self._apply_color_btn_style(self._color_user_btn, user_default)

    def _pick_color(self, btn: QPushButton) -> None:
        current = QColor(btn.property("hex_color"))
        color = QColorDialog.getColor(current, self, "Pick a colour")
        if color.isValid():
            hex_color = color.name()
            btn.setProperty("hex_color", hex_color)
            self._apply_color_btn_style(btn, hex_color)

    def _populate_model_combo(
        self, combo: QComboBox, provider_key: str, current_model: str
    ) -> None:
        try:
            provider_enum = Provider(provider_key)
        except ValueError:
            if current_model:
                combo.addItem(current_model)
            return

        # Prefer the pre-fetched cache (populated by MainWindow's background
        # model fetch) so opening Settings does not block on provider APIs.
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

    def _save(self) -> None:
        # Per-provider settings
        for pv, meta in PROVIDER_META.items():
            self._config.set(meta["api_key"], self._api_key_edits[pv].text().strip())
            self._config.set(meta["model_key"], self._model_combos[pv].currentText())
            self._config.set(meta["color_key"], self._color_btns[pv].property("hex_color"))
            self._config.set(meta["system_prompt_key"], self._system_prompt_edits[pv].toPlainText().strip())

        # General settings
        self._config.set("color_user", self._color_user_btn.property("hex_color"))
        self._config.set("default_provider", self._default_provider.currentText())
        self._config.set("system_prompt", self._system_prompt.toPlainText().strip())
        self._config.set("font_size", self._font_size.value())
        self._config.set("exclude_shade_mode", self._exclude_shade_mode.currentText())
        self._config.set("exclude_shade_amount", self._exclude_shade_amount.value())
        self._config.save()
        self.accept()
