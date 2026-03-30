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
    QSpinBox,
    QVBoxLayout,
)

from mchat.config import Config, MAX_FONT_SIZE, MIN_FONT_SIZE
from mchat.providers.base import BaseProvider


class SettingsDialog(QDialog):
    def __init__(
        self,
        config: Config,
        providers: dict | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._config = config
        self._providers = providers or {}
        self.setWindowTitle("Settings")
        self.setMinimumWidth(450)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        form = QFormLayout()
        form.setSpacing(12)

        # Anthropic API key
        self._anthropic_key = QLineEdit(self._config.get("anthropic_api_key"))
        self._anthropic_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._anthropic_key.setPlaceholderText("sk-ant-...")
        form.addRow("Anthropic API Key:", self._anthropic_key)

        # OpenAI API key
        self._openai_key = QLineEdit(self._config.get("openai_api_key"))
        self._openai_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._openai_key.setPlaceholderText("sk-...")
        form.addRow("OpenAI API Key:", self._openai_key)

        # Default provider
        self._default_provider = QComboBox()
        self._default_provider.addItems(["claude", "openai"])
        current = self._config.get("default_provider")
        idx = self._default_provider.findText(current)
        if idx >= 0:
            self._default_provider.setCurrentIndex(idx)
        form.addRow("Default Provider:", self._default_provider)

        # Claude model
        self._claude_model = QComboBox()
        self._populate_model_combo(self._claude_model, "claude", self._config.get("claude_model"))
        form.addRow("Claude Model:", self._claude_model)

        # OpenAI model
        self._openai_model = QComboBox()
        self._populate_model_combo(self._openai_model, "openai", self._config.get("openai_model"))
        form.addRow("OpenAI Model:", self._openai_model)

        # System prompt
        self._system_prompt = QPlainTextEdit(self._config.get("system_prompt"))
        self._system_prompt.setMaximumHeight(100)
        self._system_prompt.setPlaceholderText("System prompt sent at the start of new chats...")
        form.addRow("System Prompt:", self._system_prompt)

        # Background colours
        self._color_user_btn = self._make_color_btn(self._config.get("color_user"))
        form.addRow("User colour:", self._color_user_btn)

        self._color_claude_btn = self._make_color_btn(self._config.get("color_claude"))
        form.addRow("Claude colour:", self._color_claude_btn)

        self._color_openai_btn = self._make_color_btn(self._config.get("color_openai"))
        form.addRow("GPT colour:", self._color_openai_btn)

        # Font size
        self._font_size = QSpinBox()
        self._font_size.setRange(MIN_FONT_SIZE, MAX_FONT_SIZE)
        self._font_size.setSuffix(" px")
        self._font_size.setValue(int(self._config.get("font_size") or 14))
        form.addRow("Font Size:", self._font_size)

        layout.addLayout(form)
        layout.addSpacing(16)

        # Buttons
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

        layout.addLayout(btn_layout)

    def _make_color_btn(self, hex_color: str) -> QPushButton:
        """Create a button that shows and lets the user pick a colour."""
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
        """Fill a model combo from the live provider, falling back to config."""
        from mchat.models.message import Provider

        provider_enum = Provider(provider_key)
        provider: BaseProvider | None = self._providers.get(provider_enum)

        models: list[str] = []
        if provider:
            models = provider.list_models()

        if not models:
            # Bare-minimum fallback so the combo is never empty
            if current_model:
                models = [current_model]

        combo.addItems(models)

        # Ensure the currently-configured model is present and selected
        if current_model and combo.findText(current_model) < 0:
            combo.insertItem(0, current_model)
        idx = combo.findText(current_model)
        if idx >= 0:
            combo.setCurrentIndex(idx)

    def _save(self) -> None:
        self._config.set("anthropic_api_key", self._anthropic_key.text().strip())
        self._config.set("openai_api_key", self._openai_key.text().strip())
        self._config.set("default_provider", self._default_provider.currentText())
        self._config.set("claude_model", self._claude_model.currentText())
        self._config.set("openai_model", self._openai_model.currentText())
        self._config.set("system_prompt", self._system_prompt.toPlainText().strip())
        self._config.set("color_user", self._color_user_btn.property("hex_color"))
        self._config.set("color_claude", self._color_claude_btn.property("hex_color"))
        self._config.set("color_openai", self._color_openai_btn.property("hex_color"))
        self._config.set("font_size", self._font_size.value())
        self._config.save()
        self.accept()
