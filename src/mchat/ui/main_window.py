# ------------------------------------------------------------------
# Component: MainWindow
# Responsibility: Top-level window — wires sidebar, chat, input, providers
# Collaborators: PySide6, all ui components, router, db, config, workers
# ------------------------------------------------------------------
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QKeySequence, QShortcut, QTextBlockFormat, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from mchat.config import Config, MAX_FONT_SIZE, MIN_FONT_SIZE, PROVIDER_META
from mchat.db import Database
from mchat.models.conversation import Conversation
from mchat.models.message import Message, Provider, Role
from mchat.pricing import estimate_cost, format_cost
from mchat.providers.base import BaseProvider
from mchat.providers.claude import ClaudeProvider
from mchat.providers.gemini_provider import GeminiProvider
from mchat.providers.openai_provider import OpenAIProvider
from mchat.providers.perplexity_provider import PerplexityProvider
from mchat.router import Router
from mchat.ui.chat_widget import ChatWidget, FindBar
from mchat.ui.context_builder import build_context, compute_excluded_indices
from mchat.ui.matrix_panel import MatrixPanel
from mchat.ui.message_renderer import (
    MessageRenderer,
    PROVIDER_DISPLAY as _PROVIDER_DISPLAY_FROM_RENDERER,
    PROVIDER_ORDER as _PROVIDER_ORDER_FROM_RENDERER,
    strip_echoed_heading as _strip_echoed_heading,
)
from mchat.ui.send_controller import SendController
from mchat.ui.input_widget import InputWidget
from mchat.ui.settings_dialog import SettingsDialog
from mchat.ui.sidebar import Sidebar
from mchat.workers.stream_worker import StreamWorker

def _get_version() -> str:
    """Get version from last git commit timestamp (vYYYYMMDDHHMMSS)."""
    import subprocess
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%cd", "--date=format:%Y%m%d%H%M%S"],
            capture_output=True, text=True, timeout=3,
            cwd=str(Path(__file__).parent),
        )
        if result.returncode == 0 and result.stdout.strip():
            return f"v{result.stdout.strip()}"
    except Exception:
        pass
    try:
        from importlib.metadata import version as pkg_version
        return f"v{pkg_version('mchat')}"
    except Exception:
        return "vdev"


# Re-exported from message_renderer for the rest of main_window.
_PROVIDER_DISPLAY = _PROVIDER_DISPLAY_FROM_RENDERER
_PROVIDER_ORDER = _PROVIDER_ORDER_FROM_RENDERER


class MainWindow(QMainWindow):
    def __init__(self, config: Config, db: Database) -> None:
        super().__init__()
        self._config = config
        self._db = db
        self._current_conv: Conversation | None = None
        self._router: Router | None = None
        self._font_size = int(self._config.get("font_size") or 14)

        # Per-provider UI widgets (built dynamically)
        self._checkboxes: dict[Provider, QCheckBox] = {}
        self._combos: dict[Provider, QComboBox] = {}
        self._spend_labels: dict[Provider, QLabel] = {}

        self._init_providers()
        self._build_ui()
        self._renderer = MessageRenderer(self._chat, self._config, self._db)
        self._send = SendController(self)
        self._populate_model_combos_fast()  # config defaults only, no API calls
        self._apply_all_combo_styles()
        self._sync_checkboxes_from_selection()
        self._sync_matrix_panel()
        self._setup_shortcuts()
        self._load_conversations()
        self._update_input_placeholder()
        self._update_input_color()
        self._input._text_edit.setFocus()

        # Fetch live model lists in background after window is shown
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, self._populate_model_combos_async)

    def _init_providers(self) -> None:
        providers: dict[Provider, BaseProvider] = {}

        anthropic_key = self._config.get("anthropic_api_key")
        if anthropic_key:
            providers[Provider.CLAUDE] = ClaudeProvider(
                api_key=anthropic_key,
                default_model=self._config.get("claude_model"),
            )

        openai_key = self._config.get("openai_api_key")
        if openai_key:
            providers[Provider.OPENAI] = OpenAIProvider(
                api_key=openai_key,
                default_model=self._config.get("openai_model"),
            )

        gemini_key = self._config.get("gemini_api_key")
        if gemini_key:
            providers[Provider.GEMINI] = GeminiProvider(
                api_key=gemini_key,
                default_model=self._config.get("gemini_model"),
            )

        perplexity_key = self._config.get("perplexity_api_key")
        if perplexity_key:
            providers[Provider.PERPLEXITY] = PerplexityProvider(
                api_key=perplexity_key,
                default_model=self._config.get("perplexity_model"),
            )

        try:
            default = Provider(self._config.get("default_provider"))
        except ValueError:
            default = Provider.CLAUDE
        # Fall back to first configured provider if default is unconfigured
        if default not in providers and providers:
            default = next(iter(providers))
        self._router = Router(providers, default) if providers else None

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self.setWindowTitle(f"mchat {_get_version()}")
        self.setMinimumSize(900, 600)
        self._restore_geometry()

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Sidebar
        self._sidebar = Sidebar(font_size=self._font_size)
        self._sidebar.conversation_selected.connect(self._on_conversation_selected)
        self._sidebar.new_chat_requested.connect(self._on_new_chat)
        self._sidebar.rename_requested.connect(self._on_rename_conversation)
        self._sidebar.save_requested.connect(self._on_save_conversation)
        self._sidebar.delete_requested.connect(self._on_delete_conversation)
        main_layout.addWidget(self._sidebar)

        # Right panel (chat + bar + input)
        right = QFrame()
        right.setStyleSheet("background-color: #f5f5f5;")
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        # Chat area
        self._chat = ChatWidget(
            font_size=self._font_size,
            color_user=self._config.get("color_user"),
            color_claude=self._config.get("color_claude"),
            color_openai=self._config.get("color_openai"),
            color_gemini=self._config.get("color_gemini"),
            color_perplexity=self._config.get("color_perplexity"),
            exclude_shade_mode=str(self._config.get("exclude_shade_mode") or "darken"),
            exclude_shade_amount=int(self._config.get("exclude_shade_amount") or 20),
        )
        # Delegate ChatWidget rebuilds through _display_messages so
        # multi-provider column groups are preserved
        self._chat._rebuild_callback = lambda: self._display_messages(
            self._current_conv.messages if self._current_conv else []
        )
        self._find_bar = FindBar(self._chat)
        right_layout.addWidget(self._find_bar)
        right_layout.addWidget(self._chat, stretch=1)

        # ---- Provider bar (between chat and input) ----
        bar = QFrame()
        bar.setStyleSheet("background-color: #f5f5f5; border-top: 1px solid #ddd;")
        self._bar_layout = QHBoxLayout(bar)
        self._bar_layout.setContentsMargins(16, 8, 16, 8)
        self._bar_layout.setSpacing(8)

        # Build combo + checkbox + spend label for each provider
        providers_list = list(Provider)
        for i, p in enumerate(providers_list):
            if i > 0:
                self._bar_layout.addSpacing(12)

            combo = QComboBox()
            combo.setMinimumWidth(160)
            combo.activated.connect(lambda _, c=combo: c.hidePopup())
            self._bar_layout.addWidget(combo)
            self._combos[p] = combo

            cb = QCheckBox()
            cb.setToolTip(f"Include {_PROVIDER_DISPLAY[p]} in selection")
            cb.stateChanged.connect(lambda _, pid=p: self._on_checkbox_changed(pid))
            self._bar_layout.addWidget(cb)
            self._checkboxes[p] = cb

            label = QLabel("$0.00000")
            self._apply_spend_label_style(label)
            self._bar_layout.addWidget(label)
            self._spend_labels[p] = label

        self._bar_layout.addStretch()

        # Column/list mode toggle (restore from config)
        self._column_mode = bool(self._config.get("column_mode"))
        self._column_btn = QPushButton("⫐ Cols" if self._column_mode else "⫏ List")
        self._column_btn.setToolTip("Toggle between list and column layout for multi-provider responses")
        self._column_btn.setFixedWidth(70)
        self._column_btn.setStyleSheet(
            "QPushButton { background: none; border: 1px solid #ccc; border-radius: 6px; "
            "padding: 4px 8px; color: #666; font-size: 12px; }"
            "QPushButton:hover { background-color: #eee; }"
        )
        self._column_btn.clicked.connect(self._toggle_column_mode)
        self._bar_layout.addWidget(self._column_btn)

        # Settings button (right-aligned)
        self._settings_btn = QPushButton("⚙ Settings")
        self._apply_settings_btn_style()
        self._settings_btn.clicked.connect(self._open_settings)
        self._bar_layout.addWidget(self._settings_btn)

        right_layout.addWidget(bar)

        # Input area + visibility matrix to its right
        input_row = QHBoxLayout()
        input_row.setContentsMargins(0, 0, 0, 0)
        input_row.setSpacing(4)
        self._input = InputWidget(font_size=self._font_size)
        self._input.message_submitted.connect(self._on_message_submitted)
        input_row.addWidget(self._input, stretch=1)

        self._matrix_panel = MatrixPanel()
        self._matrix_panel.matrix_changed.connect(self._on_visibility_changed)
        input_row.addWidget(self._matrix_panel, stretch=0)

        right_layout.addLayout(input_row)

        main_layout.addWidget(right, stretch=1)

    def _set_combo_models(self, p: Provider, models: list[str]) -> None:
        """Set a combo's model list, preserving the current selection."""
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
        providers = self._router._providers if self._router else {}
        combo.setEnabled(p in providers)
        combo.blockSignals(False)

        cb = self._checkboxes[p]
        cb.setEnabled(p in providers)

    def _populate_model_combos_fast(self) -> None:
        """Fill combos with config defaults only — no API calls."""
        for p in Provider:
            meta = PROVIDER_META[p.value]
            current = self._config.get(meta["model_key"])
            self._set_combo_models(p, [current] if current else [])

    def _populate_model_combos_async(self) -> None:
        """Fetch live model lists in a background thread, update combos when done."""
        import concurrent.futures

        providers = self._router._providers if self._router else {}
        if not providers:
            return

        def fetch_all() -> dict[Provider, list[str]]:
            results = {}
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

        from PySide6.QtCore import QThread, Signal

        class _ModelFetcher(QThread):
            done = Signal(object)

            def run(self_inner):
                self_inner.done.emit(fetch_all())

        self._model_fetcher = _ModelFetcher()
        self._model_fetcher.done.connect(self._on_models_fetched)
        self._model_fetcher.start()

    def _on_models_fetched(self, results: dict) -> None:
        """Called on main thread when background model fetch completes."""
        for p, models in results.items():
            if models:
                self._set_combo_models(p, models)
        self._model_fetcher = None

    def _populate_model_combos(self) -> None:
        """Full synchronous populate — used when opening Settings."""
        providers = self._router._providers if self._router else {}
        for p in Provider:
            provider = providers.get(p)
            models = provider.list_models() if provider else []
            self._set_combo_models(p, models)

    def _sync_checkboxes_from_selection(self) -> None:
        """Update checkboxes to reflect the router's current selection."""
        if not self._router:
            return
        sel = set(self._router.selection)
        for p, cb in self._checkboxes.items():
            cb.blockSignals(True)
            cb.setChecked(p in sel)
            cb.blockSignals(False)

    def _on_checkbox_changed(self, provider_id: Provider) -> None:
        """Handle a checkbox toggle — update the router selection."""
        if not self._router:
            return
        selected = [p for p, cb in self._checkboxes.items() if cb.isChecked()]
        if not selected:
            # Don't allow empty selection — revert
            self._sync_checkboxes_from_selection()
            self._chat.add_note("Error: at least one provider must be selected")
            return
        self._router.set_selection(selected)
        self._save_selection()
        self._update_input_placeholder()
        self._update_input_color()

    def _apply_spend_label_style(self, label: QLabel) -> None:
        label.setStyleSheet(
            f"color: #666; font-size: {self._font_size - 1}px; padding: 0 4px;"
        )

    def _apply_settings_btn_style(self) -> None:
        self._settings_btn.setStyleSheet(
            f"QPushButton {{ background: none; border: 1px solid #ccc; border-radius: 6px; "
            f"padding: 4px 12px; color: #666; font-size: {self._font_size - 1}px; }}"
            f"QPushButton:hover {{ background-color: #eee; }}"
        )

    def _provider_color(self, p: Provider) -> str:
        return self._config.get(PROVIDER_META[p.value]["color_key"])

    def _apply_combo_provider_style(self, p: Provider) -> None:
        color = self._provider_color(p)
        combo = self._combos[p]
        if combo.isEnabled():
            combo.setStyleSheet(f"QComboBox {{ background-color: {color}; }}")
        else:
            combo.setStyleSheet(
                "QComboBox { background-color: #e0e0e0; color: #999; }"
            )

    def _apply_all_combo_styles(self) -> None:
        for p in Provider:
            self._apply_combo_provider_style(p)

    def _set_combo_waiting(self, p: Provider, waiting: bool) -> None:
        combo = self._combos[p]
        if waiting:
            combo.setStyleSheet(
                "QComboBox { border: 2px solid #e8a020; background-color: #fff8e0; "
                "font-weight: bold; }"
            )
        else:
            self._apply_combo_provider_style(p)

    def _set_combo_retrying(self, p: Provider) -> None:
        combo = self._combos[p]
        combo.setStyleSheet(
            "QComboBox { border: 2px solid #d04040; background-color: #ffe0e0; "
            "font-weight: bold; }"
        )

    def _update_input_color(self) -> None:
        if not self._router:
            return
        sel = self._router.selection
        if len(sel) == 1:
            color = self._provider_color(sel[0])
        else:
            color = self._config.get("color_user")
        self._input.set_background(color)

    def _update_spend_labels(self) -> None:
        if self._current_conv:
            spend = self._db.get_conversation_spend(self._current_conv.id)
        else:
            spend = {}
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

    # ------------------------------------------------------------------
    # Shortcuts
    # ------------------------------------------------------------------

    def _setup_shortcuts(self) -> None:
        QShortcut(QKeySequence("Ctrl+="), self).activated.connect(self._zoom_in)
        QShortcut(QKeySequence("Ctrl++"), self).activated.connect(self._zoom_in)
        QShortcut(QKeySequence("Ctrl+-"), self).activated.connect(self._zoom_out)
        QShortcut(QKeySequence("Ctrl+0"), self).activated.connect(self._zoom_reset)
        QShortcut(QKeySequence("Ctrl+S"), self).activated.connect(self._export_chat)
        QShortcut(QKeySequence("Ctrl+F"), self).activated.connect(self._find_bar.open_bar)

    def _export_chat(self) -> None:
        if not self._current_conv or not self._current_conv.messages:
            return
        title = self._current_conv.title.replace(" ", "_")[:40]
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Chat", f"{title}.html", "HTML Files (*.html)"
        )
        if path:
            html = self._chat.export_html()
            with open(path, "w", encoding="utf-8") as f:
                f.write(html)

    def _zoom_in(self) -> None:
        self._set_font_size(self._font_size + 1)

    def _zoom_out(self) -> None:
        self._set_font_size(self._font_size - 1)

    def _zoom_reset(self) -> None:
        self._set_font_size(14)

    def _set_font_size(self, size: int) -> None:
        size = max(MIN_FONT_SIZE, min(MAX_FONT_SIZE, size))
        if size == self._font_size:
            return
        self._font_size = size
        self._config.set("font_size", size)
        self._config.save()
        self._apply_font_size()

    def _apply_font_size(self) -> None:
        self._chat.update_font_size(self._font_size)
        self._input.update_font_size(self._font_size)
        self._sidebar.update_font_size(self._font_size)
        self._apply_settings_btn_style()
        for label in self._spend_labels.values():
            self._apply_spend_label_style(label)

    # ------------------------------------------------------------------
    # Conversations
    # ------------------------------------------------------------------

    def _load_conversations(self) -> None:
        conversations = self._db.list_conversations()
        self._sidebar.set_conversations(conversations)
        if conversations:
            self._sidebar.select_conversation(conversations[0].id)

    def _on_conversation_selected(self, conv_id: int) -> None:
        conv = self._db.get_conversation(conv_id)
        if not conv:
            return
        messages = self._db.get_messages(conv_id)
        self._current_conv = conv
        self._current_conv.messages = messages

        # Restore selection from last_provider (comma-separated)
        if conv.last_provider and self._router:
            try:
                providers = [Provider(v.strip()) for v in conv.last_provider.split(",") if v.strip()]
                if providers:
                    self._router.set_selection(providers)
            except ValueError:
                pass
        self._sync_checkboxes_from_selection()
        self._update_input_placeholder()
        self._update_input_color()
        self._update_spend_labels()
        self._sync_matrix_panel()

        self._display_messages(messages)

    def _sync_matrix_panel(self) -> None:
        """Rebuild the matrix panel for the currently configured providers
        and populate it from the current conversation's visibility matrix."""
        configured = list(self._router._providers.keys()) if self._router else []
        self._matrix_panel.set_providers(configured)
        if self._current_conv:
            self._matrix_panel.load_matrix(self._current_conv.visibility_matrix or {})

    def _on_visibility_changed(self, matrix: dict) -> None:
        if not self._current_conv:
            return
        self._current_conv.visibility_matrix = matrix
        self._db.set_visibility_matrix(self._current_conv.id, matrix)

    def _on_new_chat(self) -> None:
        system_prompt = self._config.get("system_prompt")
        conv = self._db.create_conversation(system_prompt=system_prompt)
        self._current_conv = conv
        self._chat.clear_messages()
        self._update_spend_labels()
        self._sync_matrix_panel()
        self._load_conversations()
        self._sidebar.select_conversation(conv.id)

    def _on_rename_conversation(self, conv_id: int, new_title: str) -> None:
        self._db.update_conversation_title(conv_id, new_title)
        if self._current_conv and self._current_conv.id == conv_id:
            self._current_conv.title = new_title
        # Update the sidebar item in place instead of reloading every
        # conversation and triggering a full chat re-render.
        self._sidebar.update_conversation_title(conv_id, new_title)

    def _on_save_conversation(self, conv_id: int) -> None:
        messages = self._db.get_messages(conv_id)
        if not messages:
            return
        convs = self._db.list_conversations()
        conv = next((c for c in convs if c.id == conv_id), None)
        title = (conv.title if conv else "chat").replace(" ", "_")[:40]

        from mchat.ui.chat_widget import ChatWidget
        tmp = ChatWidget(font_size=self._font_size)
        for msg in messages:
            tmp._messages.append(msg)
            tmp._insert_rendered(msg)
        html = tmp.export_html()
        tmp.deleteLater()

        path, _ = QFileDialog.getSaveFileName(
            self, "Export Chat", f"{title}.html", "HTML Files (*.html)"
        )
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(html)

    def _on_delete_conversation(self, conv_id: int) -> None:
        reply = QMessageBox.question(
            self, "Delete Chat",
            "Delete this conversation? This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        was_current = self._current_conv and self._current_conv.id == conv_id
        self._db.delete_conversation(conv_id)
        if was_current:
            self._current_conv = None
            self._chat.clear_messages()
        self._load_conversations()
        if was_current:
            self._on_new_chat()

    # ------------------------------------------------------------------
    # Messaging
    # ------------------------------------------------------------------

    def _update_input_placeholder(self) -> None:
        if not self._router:
            self._input.set_placeholder("Configure an API key in Settings to start chatting")
            return
        sel = self._router.selection
        if len(sel) == 1:
            name = _PROVIDER_DISPLAY[sel[0]]
            others = [_PROVIDER_DISPLAY[p] for p in Provider if p != sel[0]]
            alt = ", ".join(others[:2])
            self._input.set_placeholder(f"Message {name} — prefix another provider or use //select")
        else:
            names = ", ".join(_PROVIDER_DISPLAY[p] for p in sel)
            self._input.set_placeholder(f"Message {names} — use //select to change")

    def _selected_model(self, p: Provider) -> str:
        return self._combos[p].currentText()

    def _build_context(self, provider_id: Provider) -> list[Message]:
        """Delegate to ui.context_builder — MainWindow is just the host."""
        return build_context(
            self._current_conv, provider_id, self._db, self._config
        )

    # ------------------------------------------------------------------
    # // commands (delegated to ui.commands module)
    # ------------------------------------------------------------------

    def _handle_selection_adjust(self, text: str) -> bool:
        """Handle +provider / -provider selection adjustments."""
        from mchat.router import PREFIX_TO_PROVIDER
        op = text[0]  # '+' or '-'
        name = text[1:].strip().lower()
        provider = PREFIX_TO_PROVIDER.get(name)
        if provider is None:
            return False  # not a provider name — let normal parsing handle it

        if not self._router:
            self._chat.add_note("Error: no providers configured")
            return True

        configured = set(self._router._providers.keys())
        if provider not in configured:
            self._chat.add_note(f"Error: {_PROVIDER_DISPLAY[provider]} has no API key")
            return True

        current = list(self._router.selection)

        if op == "+":
            if provider not in current:
                current.append(provider)
                self._router.set_selection(current)
            names = ", ".join(_PROVIDER_DISPLAY[p] for p in self._router.selection)
            self._chat.add_note(f"selected: {names}")
        else:  # '-'
            if provider not in current:
                self._chat.add_note(f"{_PROVIDER_DISPLAY[provider]} is not in current selection")
                return True
            if len(current) <= 1:
                self._chat.add_note("Error: cannot remove the last provider from selection")
                return True
            current.remove(provider)
            self._router.set_selection(current)
            names = ", ".join(_PROVIDER_DISPLAY[p] for p in self._router.selection)
            self._chat.add_note(f"selected: {names}")

        self._save_selection()
        self._sync_checkboxes_from_selection()
        self._update_input_placeholder()
        self._update_input_color()
        return True

    def _handle_command(self, text: str) -> bool:
        stripped = text.strip()
        if not stripped.startswith("//"):
            return False
        parts = stripped.split(None, 1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""
        from mchat.ui.commands import dispatch
        return dispatch(cmd, arg, self)

    def _toggle_column_mode(self) -> None:
        self._column_mode = not self._column_mode
        if self._column_mode:
            self._column_btn.setText("⫐ Cols")
        else:
            self._column_btn.setText("⫏ List")
        self._config.set("column_mode", self._column_mode)
        self._config.save()

    # ------------------------------------------------------------------
    # Retry-stash accessors — commands._handle_retry still reaches into
    # these attribute names on MainWindow, so we forward them to the
    # SendController that now owns the state.
    # ------------------------------------------------------------------

    @property
    def _retry_contexts(self) -> dict[Provider, list[Message]]:
        return self._send.retry_contexts

    @property
    def _retry_failed(self) -> dict[Provider, tuple[str, bool]]:
        return self._send.retry_failed

    @property
    def _retry_error_msg_ids(self) -> dict[Provider, int | None]:
        return self._send.retry_error_msg_ids

    def _clear_retry_stash(self) -> None:
        self._send.clear_retry_stash()

    def _save_selection(self) -> None:
        """Persist the current selection to the conversation."""
        if self._current_conv and self._router:
            sel_str = ",".join(p.value for p in self._router.selection)
            self._current_conv.last_provider = sel_str
            self._db.update_conversation_last_provider(
                self._current_conv.id, sel_str
            )

    # ------------------------------------------------------------------

    def _on_message_submitted(self, text: str) -> None:
        """Delegate to SendController."""
        self._send.on_message_submitted(text)

    def _send_single(self, provider_id: Provider) -> None:
        self._send.send_single(provider_id)

    def _send_multi(
        self,
        targets: list[Provider],
        context_override: dict[Provider, list[Message]] | None = None,
    ) -> None:
        self._send.send_multi(targets, context_override=context_override)

    def _compute_excluded_indices(self, messages: list[Message]) -> set[int]:
        """Delegate to ui.context_builder — single source of truth for
        which messages fall outside the current context."""
        if not self._current_conv:
            return set()
        configured = set(self._router._providers.keys()) if self._router else set()
        return compute_excluded_indices(self._current_conv, self._db, configured)

    def _display_messages(self, messages: list[Message]) -> None:
        """Delegate rendering to MessageRenderer."""
        configured = set(self._router._providers.keys()) if self._router else set()
        self._renderer.display_messages(
            self._current_conv, messages, self._column_mode, configured
        )

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    def _restore_geometry(self) -> None:
        geo = self._config.get("window_geometry")
        if geo:
            try:
                x, y, w, h = (int(v) for v in geo.split(","))
                self.setGeometry(x, y, w, h)
            except (ValueError, TypeError):
                self.resize(1100, 750)
        else:
            self.resize(1100, 750)

    def _save_geometry(self) -> None:
        g = self.geometry()
        self._config.set("window_geometry", f"{g.x()},{g.y()},{g.width()},{g.height()}")
        self._config.save()

    def closeEvent(self, event) -> None:
        self._save_geometry()
        super().closeEvent(event)

    def _open_settings(self) -> None:
        providers = self._router._providers if self._router else {}
        # Harvest the model lists the combos already hold — MainWindow
        # fetches these asynchronously after startup, so they are usually
        # up-to-date and available without any extra API calls.
        models_cache: dict[Provider, list[str]] = {}
        for p, combo in self._combos.items():
            items = [combo.itemText(i) for i in range(combo.count())]
            if items:
                models_cache[p] = items
        dialog = SettingsDialog(
            self._config,
            providers=providers,
            models_cache=models_cache,
            parent=self,
        )
        if dialog.exec():
            self._init_providers()
            self._populate_model_combos()
            self._apply_all_combo_styles()
            self._sync_matrix_panel()
            self._update_input_placeholder()
            self._update_input_color()
            new_size = int(self._config.get("font_size") or 14)
            if new_size != self._font_size:
                self._font_size = new_size
                self._apply_font_size()
            self._chat.update_colors(
                **{meta["color_key"]: self._config.get(meta["color_key"])
                   for meta in PROVIDER_META.values()},
                color_user=self._config.get("color_user"),
            )
            self._chat.update_shading(
                mode=str(self._config.get("exclude_shade_mode") or "darken"),
                amount=int(self._config.get("exclude_shade_amount") or 20),
            )
