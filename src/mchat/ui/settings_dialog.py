# ------------------------------------------------------------------
# Component: SettingsDialog
# Responsibility: UI for general (non-provider) settings — font size,
#                 user colour, exclude shading, global system prompt,
#                 default provider. Provider-specific fields (API
#                 keys, models, provider colours, per-provider system
#                 prompts) live in ProvidersDialog.
# Collaborators: config  (external: PySide6)
# ------------------------------------------------------------------
from __future__ import annotations

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from mchat.config import Config, DEFAULTS, MAX_FONT_SIZE, MIN_FONT_SIZE


class SettingsDialog(QDialog):
    """General-settings dialog (font, user colour, shading, global
    system prompt, default provider). Provider-specific configuration
    has moved to ProvidersDialog."""

    def __init__(self, config: Config, parent=None) -> None:
        super().__init__(parent)
        self._config = config
        self.setWindowTitle("Settings")
        self.setMinimumWidth(480)
        self._build_ui()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)

        form = QFormLayout()
        form.setSpacing(12)
        outer.addLayout(form)

        # Font size
        self._font_size = QSpinBox()
        self._font_size.setRange(MIN_FONT_SIZE, MAX_FONT_SIZE)
        self._font_size.setSuffix(" px")
        self._font_size.setValue(int(self._config.get("font_size") or 14))
        form.addRow("Font Size:", self._font_size)

        # User colour + reset button
        self._color_user_btn = self._make_color_btn(
            self._config.get("color_user")
        )
        form.addRow("User colour:", self._color_user_btn)

        reset_user_btn = QPushButton("Reset user colour to default")
        reset_user_btn.setStyleSheet(
            "QPushButton { background: none; border: 1px solid #999; border-radius: 4px; "
            "padding: 4px 12px; color: #666; }"
            "QPushButton:hover { background-color: #eee; }"
        )
        reset_user_btn.clicked.connect(self._reset_user_color)
        form.addRow("", reset_user_btn)

        # Exclude shading (messages outside //limit)
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
        self._exclude_shade_amount.setValue(
            int(self._config.get("exclude_shade_amount") or 20)
        )
        form.addRow("Shading amount:", self._exclude_shade_amount)

        # Stage 3A.4: default_provider UI control removed. The config
        # key still exists as a fallback for all,/flipped, prefix
        # parsing but is no longer user-facing.

        # Work directory (#154)
        work_dir_layout = QHBoxLayout()
        self._work_directory = QLineEdit(self._config.get("work_directory"))
        self._work_directory.setPlaceholderText(
            "Default directory for import/export (empty = current directory)"
        )
        work_dir_layout.addWidget(self._work_directory)
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self._browse_work_dir)
        work_dir_layout.addWidget(browse_btn)
        form.addRow("Work directory:", work_dir_layout)

        # Diagram format preference (#151)
        self._diagram_format = QComboBox()
        self._diagram_format.addItems(["auto", "mermaid", "graphviz", "none"])
        current_df = str(self._config.get("diagram_format") or "auto")
        df_idx = self._diagram_format.findText(current_df)
        if df_idx >= 0:
            self._diagram_format.setCurrentIndex(df_idx)
        form.addRow("Diagram format:", self._diagram_format)

        # Global system prompt
        self._system_prompt = QPlainTextEdit(self._config.get("system_prompt"))
        self._system_prompt.setMinimumHeight(120)
        self._system_prompt.setPlaceholderText(
            "System prompt sent at the start of new chats..."
        )
        form.addRow("System prompt:", self._system_prompt)

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

    def _browse_work_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(
            self, "Select Work Directory", self._work_directory.text()
        )
        if d:
            self._work_directory.setText(d)

    def _reset_user_color(self) -> None:
        default = DEFAULTS["color_user"]
        self._color_user_btn.setProperty("hex_color", default)
        self._apply_color_btn_style(self._color_user_btn, default)

    def _pick_color(self, btn: QPushButton) -> None:
        current = QColor(btn.property("hex_color"))
        color = QColorDialog.getColor(current, self, "Pick a colour")
        if color.isValid():
            hex_color = color.name()
            btn.setProperty("hex_color", hex_color)
            self._apply_color_btn_style(btn, hex_color)

    def _save(self) -> None:
        """Persist only the general fields — provider-specific config
        keys are untouched so ProvidersDialog can own them safely."""
        self._config.set("color_user", self._color_user_btn.property("hex_color"))
        self._config.set("system_prompt", self._system_prompt.toPlainText().strip())
        self._config.set("font_size", self._font_size.value())
        self._config.set("exclude_shade_mode", self._exclude_shade_mode.currentText())
        self._config.set("exclude_shade_amount", self._exclude_shade_amount.value())
        self._config.set("diagram_format", self._diagram_format.currentText())
        self._config.set("work_directory", self._work_directory.text().strip())
        self._config.save()
        self.accept()
