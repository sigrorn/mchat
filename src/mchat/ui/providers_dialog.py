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
    QFileDialog,
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

        self._tab_indices: dict[str, int] = {}
        for pv, meta in PROVIDER_META.items():
            tab = self._build_provider_tab(pv, meta)
            idx = self._tabs.addTab(tab, meta["display"])
            self._tab_indices[pv] = idx

        self._update_all_tab_colors()

        # Footer: reset-colours and save/cancel
        footer = QHBoxLayout()

        import_btn = QPushButton("Import…")
        import_btn.clicked.connect(self._on_import_clicked)
        footer.addWidget(import_btn)

        export_btn = QPushButton("Export…")
        export_btn.clicked.connect(self._on_export_clicked)
        footer.addWidget(import_btn)

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
        key_edit.textChanged.connect(lambda _, p=pv: self._update_tab_color(p))
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
    # Tab colour indicators
    # ------------------------------------------------------------------

    def _update_tab_color(self, pv: str) -> None:
        """Set tab title to red if the API key is empty, default otherwise."""
        idx = self._tab_indices.get(pv)
        if idx is None:
            return
        key = self._api_key_edits[pv].text().strip()
        bar = self._tabs.tabBar()
        if not key:
            bar.setTabTextColor(idx, QColor("#cc0000"))
        else:
            bar.setTabTextColor(idx, QColor())  # reset to default

    def _update_all_tab_colors(self) -> None:
        for pv in self._tab_indices:
            self._update_tab_color(pv)

    # ------------------------------------------------------------------
    # Export / Import
    # ------------------------------------------------------------------

    def export_providers_md(self) -> str:
        """Serialize current provider settings to a readable .md string.
        Reads from the dialog widgets (which reflect current edits)."""
        lines: list[str] = ["# Provider Settings", ""]
        first = True
        for pv, meta in PROVIDER_META.items():
            if not first:
                lines.append("---")
                lines.append("")
            first = False
            lines.append(f"## {meta['display']}")
            lines.append(f"- API key: {self._api_key_edits[pv].text().strip()}")
            lines.append(f"- Model: {self._model_combos[pv].currentText()}")
            lines.append(f"- Color: {self._color_btns[pv].property('hex_color')}")
            lines.append("- System prompt:")
            lines.append("")
            prompt = self._system_prompt_edits[pv].toPlainText().strip()
            lines.append(prompt or "(none)")
            lines.append("")
        return "\n".join(lines)

    def import_providers_md(self, md: str) -> None:
        """Parse a .md string and write the provider settings to config."""
        import re
        # Build a reverse lookup: display name → provider value
        display_to_pv = {
            meta["display"]: pv for pv, meta in PROVIDER_META.items()
        }

        sections = re.split(r"\n---\n|\n(?=## )", md)
        for section in sections:
            section = section.strip()
            if not section:
                continue
            # Skip top-level header
            if section.startswith("# Provider Settings") and "## " not in section:
                continue
            if section.startswith("# Provider Settings"):
                idx = section.index("## ")
                section = section[idx:]

            name_match = re.match(r"^## (.+)$", section, re.MULTILINE)
            if not name_match:
                continue
            display = name_match.group(1).strip()
            pv = display_to_pv.get(display)
            if pv is None:
                continue
            meta = PROVIDER_META[pv]

            def _field(label: str) -> str | None:
                m = re.search(
                    rf"^- {label}:\s*(.*)$", section, re.MULTILINE,
                )
                if m:
                    val = m.group(1).strip()
                    return None if val == "(none)" else val
                return None

            api_key = _field("API key") or ""
            model = _field("Model") or ""
            color = _field("Color") or DEFAULTS.get(meta["color_key"], "")

            prompt_match = re.search(
                r"^- System prompt:\s*\n\n(.*)",
                section, re.MULTILINE | re.DOTALL,
            )
            prompt = ""
            if prompt_match:
                prompt = prompt_match.group(1).strip()
                if prompt == "(none)":
                    prompt = ""

            self._config.set(meta["api_key"], api_key)
            self._config.set(meta["model_key"], model)
            self._config.set(meta["color_key"], color)
            self._config.set(meta["system_prompt_key"], prompt)

        self._config.save()

    def _on_export_clicked(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Provider Settings", "providers.md",
            "Markdown Files (*.md);;All Files (*)",
        )
        if not path:
            return
        md = self.export_providers_md()
        with open(path, "w", encoding="utf-8") as f:
            f.write(md)

    def _on_import_clicked(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Import Provider Settings", "",
            "Markdown Files (*.md);;All Files (*)",
        )
        if not path:
            return
        with open(path, "r", encoding="utf-8") as f:
            md = f.read()
        self.import_providers_md(md)
        # Refresh the dialog widgets from the updated config
        for pv, meta in PROVIDER_META.items():
            self._api_key_edits[pv].setText(self._config.get(meta["api_key"]))
            color = self._config.get(meta["color_key"])
            self._color_btns[pv].setProperty("hex_color", color)
            self._apply_color_btn_style(self._color_btns[pv], color)
            self._system_prompt_edits[pv].setPlainText(
                self._config.get(meta["system_prompt_key"])
            )

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
