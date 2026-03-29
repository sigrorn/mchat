# ------------------------------------------------------------------
# Component: SettingsDialog
# Responsibility: UI for managing API keys and settings
# Collaborators: PySide6, config
# ------------------------------------------------------------------
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from mchat.config import Config


class SettingsDialog(QDialog):
    def __init__(self, config: Config, parent=None) -> None:
        super().__init__(parent)
        self._config = config
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
        self._claude_model.addItems([
            "claude-opus-4-20250514",
            "claude-sonnet-4-20250514",
            "claude-haiku-4-20250414",
        ])
        current_model = self._config.get("claude_model")
        idx = self._claude_model.findText(current_model)
        if idx >= 0:
            self._claude_model.setCurrentIndex(idx)
        form.addRow("Claude Model:", self._claude_model)

        # OpenAI model
        self._openai_model = QComboBox()
        self._openai_model.addItems(["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-3.5-turbo"])
        current_model = self._config.get("openai_model")
        idx = self._openai_model.findText(current_model)
        if idx >= 0:
            self._openai_model.setCurrentIndex(idx)
        form.addRow("OpenAI Model:", self._openai_model)

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

    def _save(self) -> None:
        self._config.set("anthropic_api_key", self._anthropic_key.text().strip())
        self._config.set("openai_api_key", self._openai_key.text().strip())
        self._config.set("default_provider", self._default_provider.currentText())
        self._config.set("claude_model", self._claude_model.currentText())
        self._config.set("openai_model", self._openai_model.currentText())
        self._config.save()
        self.accept()
