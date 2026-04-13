# ------------------------------------------------------------------
# Component: PersonaDialog
# Responsibility: Modal editor for the persona list of a single
#                 conversation. Exposes service-level methods
#                 (create_persona, update_persona, remove_persona,
#                 list_items) that the dialog's widgets call, and
#                 effective-value helpers that display the currently
#                 resolved prompt/model/colour alongside each override
#                 input. See docs/plans/personas.md § Stage 3A.1.
# Collaborators: db, config, models.persona, services.persona_service, ui.persona_resolution  (external: PySide6)
# ------------------------------------------------------------------
from __future__ import annotations

import sqlite3

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from mchat.config import PROVIDER_META, Config
from mchat.db import Database
from mchat.models.message import Provider
from mchat.models.persona import (
    Persona,
    slugify_persona_name,
    validate_persona_name,
)
from mchat.services.persona_service import (
    PersonaImportError,
    PersonaService,
)
from mchat.ui.persona_resolution import (
    resolve_persona_color,
    resolve_persona_model,
    resolve_persona_prompt,
)


class PersonaDialog(QDialog):
    """Modal persona editor for one conversation.

    The dialog has two halves: a list of active personas on the left
    with Add/Remove/Move buttons, and an edit form on the right for
    the currently selected persona. Every widget action calls one of
    the public service methods below; those in turn write through to
    the DB. Tests exercise the service methods directly rather than
    simulating clicks, which keeps them fast and stable.
    """

    _MODEL_DEFAULT_LABEL = "Use provider default"

    def __init__(
        self,
        db: Database,
        config: Config,
        conversation_id: int,
        parent: QWidget | None = None,
        models_cache: dict[Provider, list[str]] | None = None,
    ) -> None:
        super().__init__(parent)
        self._db = db
        self._config = config
        self._conv_id = conversation_id
        self._service = PersonaService(db, config, conversation_id)
        self._models_cache: dict[Provider, list[str]] = models_cache or {}
        self.setWindowTitle("Personas")
        self.setMinimumSize(700, 450)
        self._build_ui()
        self._refresh_list()

    # ------------------------------------------------------------------
    # Service-level methods — delegated to PersonaService (#160)
    # ------------------------------------------------------------------

    def list_items(self) -> list[Persona]:
        return self._service.list_items()

    def create_persona(self, **kwargs) -> Persona:
        return self._service.create_persona(**kwargs)

    def update_persona(self, persona_id: str, **kwargs) -> None:
        self._service.update_persona(persona_id, **kwargs)

    def remove_persona(self, persona_id: str) -> None:
        self._service.remove_persona(persona_id)

    def move_persona_up(self, persona_id: str) -> None:
        self._service.move_persona_up(persona_id)

    def move_persona_down(self, persona_id: str) -> None:
        self._service.move_persona_down(persona_id)

    def export_personas_md(self) -> str:
        return self._service.export_personas_md()

    def import_personas_md(self, md: str) -> None:
        self._service.import_personas_md(md)
        self._refresh_list()

    def effective_prompt(self, persona: Persona) -> str:
        return self._service.effective_prompt(persona)

    def effective_model(self, persona: Persona) -> str:
        return self._service.effective_model(persona)

    def effective_color(self, persona: Persona) -> str:
        return self._service.effective_color(persona)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        outer = QHBoxLayout(self)

        # Left column: list of personas + action buttons
        left = QVBoxLayout()
        self._list = QListWidget()
        self._list.currentItemChanged.connect(self._on_selection_changed)
        left.addWidget(self._list, stretch=1)

        list_btns = QHBoxLayout()
        add_btn = QPushButton("Add")
        add_btn.clicked.connect(self._on_add_clicked)
        list_btns.addWidget(add_btn)
        self._remove_btn = QPushButton("Remove")
        self._remove_btn.clicked.connect(self._on_remove_clicked)
        self._remove_btn.setEnabled(False)
        list_btns.addWidget(self._remove_btn)
        self._up_btn = QPushButton("▲")
        self._up_btn.setFixedWidth(30)
        self._up_btn.setToolTip("Move up")
        self._up_btn.clicked.connect(self._on_move_up_clicked)
        self._up_btn.setEnabled(False)
        list_btns.addWidget(self._up_btn)
        self._down_btn = QPushButton("▼")
        self._down_btn.setFixedWidth(30)
        self._down_btn.setToolTip("Move down")
        self._down_btn.clicked.connect(self._on_move_down_clicked)
        self._down_btn.setEnabled(False)
        list_btns.addWidget(self._down_btn)
        left.addLayout(list_btns)

        outer.addLayout(left, stretch=1)

        # Right column: edit form
        right = QVBoxLayout()
        self._form_widget = QWidget()
        form = QFormLayout(self._form_widget)
        form.setContentsMargins(8, 8, 8, 8)

        self._name_edit = QLineEdit()
        form.addRow("Name:", self._name_edit)

        self._provider_combo = QComboBox()
        for p in Provider:
            self._provider_combo.addItem(
                PROVIDER_META[p.value]["display"], p,
            )
        self._provider_combo.currentIndexChanged.connect(
            self._on_provider_changed
        )
        form.addRow("Provider:", self._provider_combo)

        # System prompt override + effective-value label
        self._prompt_edit = QPlainTextEdit()
        self._prompt_edit.setMaximumHeight(100)
        self._prompt_edit.setPlaceholderText(
            "(leave blank to inherit the global provider prompt)"
        )
        form.addRow("System prompt:", self._prompt_edit)
        self._prompt_effective = QLabel()
        self._prompt_effective.setStyleSheet("color: #888; font-style: italic;")
        self._prompt_effective.setWordWrap(True)
        form.addRow("", self._prompt_effective)

        # Model override combo + effective-value label
        self._model_combo = QComboBox()
        self._model_combo.setEditable(False)
        form.addRow("Model:", self._model_combo)
        self._model_effective = QLabel()
        self._model_effective.setStyleSheet("color: #888; font-style: italic;")
        form.addRow("", self._model_effective)

        # Color override + swatch
        color_row = QHBoxLayout()
        self._color_edit = QLineEdit()
        self._color_edit.setPlaceholderText(
            "(leave blank to inherit the provider colour)"
        )
        self._color_edit.setMaximumWidth(120)
        color_row.addWidget(self._color_edit)
        pick_color_btn = QPushButton("Pick…")
        pick_color_btn.clicked.connect(self._on_pick_color)
        color_row.addWidget(pick_color_btn)
        color_row.addStretch()
        form.addRow("Colour override:", color_row)
        self._color_effective = QLabel()
        self._color_effective.setStyleSheet("color: #888; font-style: italic;")
        form.addRow("", self._color_effective)

        # Save button
        save_row = QHBoxLayout()
        save_row.addStretch()
        self._save_btn = QPushButton("Save persona")
        self._save_btn.clicked.connect(self._on_save_clicked)
        self._save_btn.setEnabled(False)
        save_row.addWidget(self._save_btn)
        form.addRow("", save_row)

        right.addWidget(self._form_widget)

        # Export / Import / Close buttons at the bottom
        bottom_row = QHBoxLayout()
        import_btn = QPushButton("Import…")
        import_btn.clicked.connect(self._on_import_clicked)
        bottom_row.addWidget(import_btn)
        export_btn = QPushButton("Export…")
        export_btn.clicked.connect(self._on_export_clicked)
        bottom_row.addWidget(export_btn)
        bottom_row.addStretch()
        self._warning_label = QLabel()
        self._warning_label.setStyleSheet("color: #cc0000; font-size: 11px;")
        self._warning_label.setVisible(False)
        bottom_row.addWidget(self._warning_label)
        self._close_btn = QPushButton("Close")
        self._close_btn.clicked.connect(self.accept)
        bottom_row.addWidget(self._close_btn)
        right.addLayout(bottom_row)

        outer.addLayout(right, stretch=2)

        self._set_form_enabled(False)

    def _configured_providers(self) -> set[Provider]:
        """Return the set of providers that have a non-empty API key."""
        configured: set[Provider] = set()
        for pv, meta in PROVIDER_META.items():
            key = self._config.get(meta["api_key"])
            if key:
                try:
                    configured.add(Provider(pv))
                except ValueError:
                    pass
        return configured

    def _refresh_list(self) -> None:
        """Reload the persona list from the DB, preserving the
        currently selected persona id where possible. Highlights
        personas with unconfigured providers or invalid names in red
        and blocks the Close button until resolved."""
        current_id = self._selected_persona_id()
        self._list.clear()
        configured = self._configured_providers()
        unconfigured_names: list[str] = []
        invalid_names: list[str] = []
        for p in self.list_items():
            item = QListWidgetItem(f"{p.name}  ({p.provider.value})")
            item.setData(Qt.ItemDataRole.UserRole, p.id)
            # Check for invalid persona name (#155)
            has_bad_name = False
            try:
                validate_persona_name(p.name)
            except ValueError:
                has_bad_name = True
                invalid_names.append(p.name)
            if p.provider not in configured:
                unconfigured_names.append(
                    f"{p.name} ({p.provider.value})"
                )
            if has_bad_name or p.provider not in configured:
                item.setForeground(QColor("#cc0000"))
            self._list.addItem(item)

        # Block close if any persona has issues
        warnings: list[str] = []
        if invalid_names:
            warnings.append(
                f"Invalid name{'s' if len(invalid_names) > 1 else ''}: "
                f"{', '.join(invalid_names)} "
                f"— rename before closing"
            )
        if unconfigured_names:
            warnings.append(
                f"Unconfigured: {', '.join(unconfigured_names)} "
                f"— configure API key in Providers or change/remove the persona"
            )
        if warnings:
            self._warning_label.setText(" | ".join(warnings))
            self._warning_label.setVisible(True)
            self._close_btn.setEnabled(False)
        else:
            self._warning_label.setVisible(False)
            self._close_btn.setEnabled(True)

        # Restore selection
        if current_id:
            for i in range(self._list.count()):
                if self._list.item(i).data(Qt.ItemDataRole.UserRole) == current_id:
                    self._list.setCurrentRow(i)
                    return
        # No valid previous selection — clear the form
        self._set_form_enabled(False)

    def _selected_persona_id(self) -> str | None:
        item = self._list.currentItem()
        if item is None:
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    def _selected_persona(self) -> Persona | None:
        pid = self._selected_persona_id()
        if pid is None:
            return None
        for p in self.list_items():
            if p.id == pid:
                return p
        return None

    def _set_form_enabled(self, enabled: bool) -> None:
        self._form_widget.setEnabled(enabled)
        self._remove_btn.setEnabled(enabled)
        self._save_btn.setEnabled(enabled)
        self._up_btn.setEnabled(enabled)
        self._down_btn.setEnabled(enabled)
        if not enabled:
            self._name_edit.clear()
            self._prompt_edit.clear()
            self._model_combo.clear()
            self._color_edit.clear()
            self._prompt_effective.clear()
            self._model_effective.clear()
            self._color_effective.clear()

    def _populate_model_combo(
        self, provider: Provider, current_override: str | None,
    ) -> None:
        """Fill the model combo for the given provider. First item is
        always 'Use provider default'; remaining items come from
        models_cache. If current_override is set and not in the list,
        it is inserted so the combo shows the current value."""
        self._model_combo.blockSignals(True)
        self._model_combo.clear()
        self._model_combo.addItem(self._MODEL_DEFAULT_LABEL)
        models = self._models_cache.get(provider, [])
        for m in models:
            self._model_combo.addItem(m)
        if current_override:
            idx = self._model_combo.findText(current_override)
            if idx < 0:
                self._model_combo.addItem(current_override)
                idx = self._model_combo.findText(current_override)
            self._model_combo.setCurrentIndex(idx)
        else:
            self._model_combo.setCurrentIndex(0)
        self._model_combo.blockSignals(False)

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _on_provider_changed(self, _index: int) -> None:
        """Repopulate the model combo when the provider combo changes.

        Stage 3A.6: switching a persona's backing provider updates the
        model list and resets the selection to 'Use provider default'.
        """
        provider = self._provider_combo.currentData()
        if provider is not None:
            self._populate_model_combo(provider, None)

    def _on_selection_changed(
        self, current: QListWidgetItem | None, _previous,
    ) -> None:
        if current is None:
            self._set_form_enabled(False)
            return
        persona = self._selected_persona()
        if persona is None:
            self._set_form_enabled(False)
            return

        self._set_form_enabled(True)
        self._name_edit.setText(persona.name)
        self._provider_combo.blockSignals(True)
        idx = self._provider_combo.findData(persona.provider)
        if idx >= 0:
            self._provider_combo.setCurrentIndex(idx)
        self._provider_combo.blockSignals(False)
        self._prompt_edit.setPlainText(persona.system_prompt_override or "")
        self._populate_model_combo(persona.provider, persona.model_override)
        self._color_edit.setText(persona.color_override or "")
        self._refresh_effective_labels(persona)

    def _refresh_effective_labels(self, persona: Persona) -> None:
        prompt = self.effective_prompt(persona)
        prompt_preview = prompt if len(prompt) < 200 else prompt[:197] + "…"
        self._prompt_effective.setText(
            f"Currently effective: {prompt_preview or '(empty)'}"
        )
        self._model_effective.setText(
            f"Currently effective: {self.effective_model(persona)}"
        )
        self._color_effective.setText(
            f"Currently effective: {self.effective_color(persona)}"
        )

    def _on_add_clicked(self) -> None:
        # Create a persona with a unique default name.
        # #140: the default must pass validate_persona_name so the
        # click-to-add flow keeps working — use 'new_persona' with
        # an underscore, not 'New persona' with whitespace.
        base = "new_persona"
        existing_slugs = {p.name_slug for p in self.list_items()}
        name = base
        suffix = 2
        while slugify_persona_name(name) in existing_slugs:
            name = f"{base}_{suffix}"
            suffix += 1

        provider = self._provider_combo.currentData() or Provider.CLAUDE
        try:
            self.create_persona(provider=provider, name=name)
        except sqlite3.IntegrityError:
            QMessageBox.warning(
                self, "Duplicate",
                f"A persona named {name!r} already exists.",
            )
            return
        except ValueError as e:
            # Shouldn't happen with the hardcoded default name, but
            # be defensive so the button never silently no-ops.
            QMessageBox.warning(self, "Invalid name", str(e))
            return
        self._refresh_list()
        # Select the new persona
        for i in range(self._list.count()):
            item = self._list.item(i)
            persona = next(
                (p for p in self.list_items()
                 if p.id == item.data(Qt.ItemDataRole.UserRole)),
                None,
            )
            if persona and persona.name == name:
                self._list.setCurrentRow(i)
                return

    def _on_remove_clicked(self) -> None:
        persona = self._selected_persona()
        if persona is None:
            return
        reply = QMessageBox.question(
            self, "Remove persona",
            f"Remove persona {persona.name!r}? This tombstones the row — "
            f"historical messages will still show the name, but the "
            f"persona won't participate in new sends.",
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self.remove_persona(persona.id)
        self._refresh_list()

    def _on_move_up_clicked(self) -> None:
        pid = self._selected_persona_id()
        if pid:
            self.move_persona_up(pid)
            self._refresh_list()

    def _on_move_down_clicked(self) -> None:
        pid = self._selected_persona_id()
        if pid:
            self.move_persona_down(pid)
            self._refresh_list()

    def _on_save_clicked(self) -> None:
        persona = self._selected_persona()
        if persona is None:
            return

        new_name = self._name_edit.text().strip()
        if not new_name:
            QMessageBox.warning(self, "Invalid", "Name cannot be empty.")
            return
        # #140: validate only when the name actually changed, so old
        # grandfathered personas can still be edited (prompt, model,
        # colour) without being forced to rename. Renaming to a
        # fresh name must obey the new rules.
        if new_name != persona.name:
            try:
                validate_persona_name(new_name)
            except ValueError as e:
                QMessageBox.warning(self, "Invalid name", str(e))
                return
        try:
            new_slug = slugify_persona_name(new_name)
        except ValueError:
            QMessageBox.warning(
                self, "Invalid", f"Name {new_name!r} produces an empty slug.",
            )
            return

        # Renaming check: new slug must not collide with another active persona
        for other in self.list_items():
            if other.id != persona.id and other.name_slug == new_slug:
                QMessageBox.warning(
                    self, "Duplicate",
                    f"A persona named {new_name!r} already exists.",
                )
                return

        # Read override inputs — empty string = None (inherit)
        prompt_text = self._prompt_edit.toPlainText().strip()
        prompt_override: str | None = prompt_text if prompt_text else None
        model_text = self._model_combo.currentText()
        model_override: str | None = (
            None if model_text == self._MODEL_DEFAULT_LABEL else model_text
        )
        color_text = self._color_edit.text().strip()
        color_override: str | None = color_text if color_text else None

        # Direct mutation — the service-level update_persona only
        # touches override fields, but we need to handle rename + provider
        # change here too.
        persona.name = new_name
        persona.name_slug = new_slug
        provider = self._provider_combo.currentData()
        if provider is not None:
            persona.provider = provider
        persona.system_prompt_override = prompt_override
        persona.model_override = model_override
        persona.color_override = color_override
        try:
            self._db.update_persona(persona)
        except sqlite3.IntegrityError as e:
            QMessageBox.warning(self, "Save failed", str(e))
            return

        self._refresh_list()
        self._refresh_effective_labels(persona)

    def _on_export_clicked(self) -> None:
        import os
        default_path = os.path.join(self._config.work_dir(), "personas.md")
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Personas", default_path,
            "Markdown Files (*.md);;All Files (*)",
        )
        if not path:
            return
        md = self.export_personas_md()
        with open(path, "w", encoding="utf-8") as f:
            f.write(md)

    def _on_import_clicked(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Import Personas", self._config.work_dir(),
            "Markdown Files (*.md);;All Files (*)",
        )
        if not path:
            return
        reply = QMessageBox.question(
            self, "Import Personas",
            "This will replace all existing personas in this chat. Continue?",
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        with open(path, "r", encoding="utf-8") as f:
            md = f.read()
        try:
            self.import_personas_md(md)
        except PersonaImportError as e:
            # #140: pre-flight validation failed — show every issue
            # at once and leave the DB untouched.
            QMessageBox.warning(self, "Import failed", str(e))

    def _on_pick_color(self) -> None:
        current = self._color_edit.text().strip() or "#ffffff"
        color = QColorDialog.getColor(QColor(current), self, "Pick persona colour")
        if color.isValid():
            self._color_edit.setText(color.name())
