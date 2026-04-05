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
from mchat.ui.matrix_panel import MatrixPanel
from mchat.ui.visibility import filter_for_provider
from mchat.ui.input_widget import InputWidget
from mchat.ui.settings_dialog import SettingsDialog
from mchat.ui.sidebar import Sidebar
from mchat.workers.stream_worker import StreamWorker

def _pin_matches(pin_target: str | None, provider_id: Provider) -> bool:
    """Return True if a pinned message targets the given provider."""
    if not pin_target:
        return False
    if pin_target == "all":
        return True
    targets = {t.strip().lower() for t in pin_target.split(",") if t.strip()}
    return provider_id.value in targets


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


# Display names for provider labels in "X's take:" prefixes
_PROVIDER_DISPLAY = {p: PROVIDER_META[p.value]["display"] for p in Provider}

# Stable display order for multi-provider responses
_PROVIDER_ORDER = [Provider.CLAUDE, Provider.OPENAI, Provider.GEMINI, Provider.PERPLEXITY]

# Patterns the LLMs may echo at the start of their response
import re as _re
_TAKE_ECHO_RE = _re.compile(
    r"^\*{0,2}(?:Claude|GPT|Gemini|Perplexity)(?:'s|'s)\s+take:?\*{0,2}\s*\n*",
    _re.IGNORECASE,
)


def _strip_echoed_heading(text: str) -> str:
    """Remove any LLM-echoed 'X's take:' heading from the start of a response."""
    return _TAKE_ECHO_RE.sub("", text)


class MainWindow(QMainWindow):
    def __init__(self, config: Config, db: Database) -> None:
        super().__init__()
        self._config = config
        self._db = db
        self._current_conv: Conversation | None = None
        self._multi_workers: dict[Provider, StreamWorker] = {}
        self._router: Router | None = None
        self._font_size = int(self._config.get("font_size") or 14)

        # Per-provider UI widgets (built dynamically)
        self._checkboxes: dict[Provider, QCheckBox] = {}
        self._combos: dict[Provider, QComboBox] = {}
        self._spend_labels: dict[Provider, QLabel] = {}

        # Column mode buffer — accumulates multi-provider results for table rendering
        self._column_buffer: dict[Provider, tuple[str, str, str, int, int, bool]] = {}
        # maps provider -> (display_label, full_text, model, input_tokens, output_tokens, estimated)

        # Retry stash — cleared when user sends a new regular message
        self._retry_contexts: dict[Provider, list[Message]] = {}
        self._retry_models: dict[Provider, str] = {}
        self._retry_failed: dict[Provider, tuple[str, bool]] = {}  # provider -> (error, transient)
        self._retry_error_msg_ids: dict[Provider, int | None] = {}  # provider -> message DB id

        self._init_providers()
        self._build_ui()
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
        context: list[Message] = []

        # Provider-specific system prompt + main system prompt
        parts: list[str] = []
        provider_prompt = self._config.get(
            PROVIDER_META[provider_id.value]["system_prompt_key"]
        )
        if provider_prompt:
            parts.append(provider_prompt)
        if self._current_conv.system_prompt:
            parts.append(self._current_conv.system_prompt)
        if parts:
            context.append(
                Message(role=Role.SYSTEM, content="\n\n".join(parts))
            )

        all_messages = self._current_conv.messages
        messages = all_messages
        limit_mark = self._current_conv.limit_mark
        cut_idx = 0
        if limit_mark is not None:
            idx = self._db.get_mark(self._current_conv.id, limit_mark)
            if idx is not None and idx < len(all_messages):
                cut_idx = idx
                messages = all_messages[idx:]

        # Include pinned messages that fall before the limit cut-off and
        # target this provider. They are prepended so the provider sees
        # them as early context regardless of //limit.
        if cut_idx > 0:
            pinned_before = [
                m for m in all_messages[:cut_idx]
                if m.pinned and _pin_matches(m.pin_target, provider_id)
            ]
            if pinned_before:
                messages = pinned_before + list(messages)

        # Apply per-provider visibility filtering (user message addressing
        # + assistant visibility matrix). Pinned messages were already
        # prepended and bypass these filters by design.
        matrix = self._current_conv.visibility_matrix or {}
        pinned_count = len(messages) - (len(all_messages) - cut_idx)
        if pinned_count > 0:
            pinned_prefix = messages[:pinned_count]
            rest = messages[pinned_count:]
            messages = pinned_prefix + filter_for_provider(rest, provider_id, matrix)
        else:
            messages = filter_for_provider(messages, provider_id, matrix)

        # Strip provider prefixes from user messages so providers don't
        # see routing metadata like "claude," or "flipped," in context
        from mchat.router import Router
        for msg in messages:
            if msg.role == Role.USER:
                _, cleaned = Router._strip_prefix(msg.content)
                context.append(
                    Message(role=msg.role, content=cleaned,
                            provider=msg.provider, model=msg.model,
                            conversation_id=msg.conversation_id, id=msg.id)
                )
            else:
                context.append(msg)
        return context

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

    def _clear_retry_stash(self) -> None:
        self._retry_contexts.clear()
        self._retry_models.clear()
        self._retry_failed.clear()
        self._retry_error_msg_ids.clear()

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
        if text.strip().startswith("//"):
            self._handle_command(text)
            return

        # +provider / -provider selection adjustment
        stripped = text.strip()
        if len(stripped) > 1 and stripped[0] in ("+", "-"):
            if self._handle_selection_adjust(stripped):
                return

        if not self._router:
            QMessageBox.warning(
                self, "No API Keys",
                "Please configure at least one API key in Settings.",
            )
            return

        if not self._current_conv:
            self._on_new_chat()

        # Route message
        targets, cleaned_text = self._router.parse(text)

        # If provider prefixes consumed everything, treat as selection change
        if not cleaned_text.strip() and targets != self._router.selection:
            self._sync_checkboxes_from_selection()
            self._update_input_placeholder()
            self._update_input_color()
            self._save_selection()
            names = ", ".join(_PROVIDER_DISPLAY[p] for p in targets)
            self._chat.add_note(f"selected: {names}")
            return

        # Validate all targets are configured
        configured = set(self._router._providers.keys())
        missing = [p for p in targets if p not in configured]
        targets = [p for p in targets if p in configured]
        if missing:
            names = ", ".join(_PROVIDER_DISPLAY[p] for p in missing)
            self._chat.add_note(f"{names} not configured — skipped")
        if not targets:
            QMessageBox.warning(
                self, "No Provider Available",
                "None of the target providers have API keys configured.",
            )
            return

        # Determine addressed_to: "all" if the user broadcast to every
        # configured provider, otherwise a comma-separated list of the
        # targeted provider values. This is what drives the "visible to
        # addressed only" rule for user messages.
        if set(targets) == configured:
            addressed_to = "all"
        else:
            addressed_to = ",".join(p.value for p in targets)

        # Save and display user message
        user_msg = Message(
            role=Role.USER,
            content=text,
            conversation_id=self._current_conv.id,
            addressed_to=addressed_to,
        )
        self._db.add_message(user_msg)
        self._current_conv.messages.append(user_msg)
        self._chat.add_message(user_msg)

        # Auto-title on first message
        if len(self._current_conv.messages) == 1:
            title = text[:50] + ("..." if len(text) > 50 else "")
            self._db.update_conversation_title(self._current_conv.id, title)
            self._load_conversations()
            self._sidebar.select_conversation(self._current_conv.id)

        self._input.set_enabled(False)
        self._save_selection()
        self._sync_checkboxes_from_selection()
        self._clear_retry_stash()

        if len(targets) == 1:
            self._send_single(targets[0])
        else:
            self._send_multi(targets)

    def _send_single(self, provider_id: Provider) -> None:
        """Send to a single provider."""
        model = self._selected_model(provider_id)
        provider = self._router.get_provider(provider_id)

        self._set_combo_waiting(provider_id, True)
        self._send_multi([provider_id])

    def _send_multi(self, targets: list[Provider], context_override: dict[Provider, list[Message]] | None = None) -> None:
        """Send to multiple providers simultaneously, render when each completes."""
        self._multi_workers.clear()
        self._column_buffer.clear()

        for provider_id in targets:
            model = self._selected_model(provider_id)
            provider = self._router.get_provider(provider_id)
            self._set_combo_waiting(provider_id, True)
            if context_override and provider_id in context_override:
                context_messages = context_override[provider_id]
            else:
                context_messages = self._build_context(provider_id)

            # Stash for //retry
            self._retry_contexts[provider_id] = context_messages
            self._retry_models[provider_id] = model

            worker = StreamWorker(provider, context_messages, model)
            worker.stream_complete.connect(
                lambda full_text, inp, out, est, pid=provider_id, mdl=model: (
                    self._on_multi_complete(pid, mdl, full_text, inp, out, est)
                )
            )
            worker.stream_error.connect(
                lambda error, pid=provider_id: self._on_multi_error(pid, error)
            )
            worker.retrying.connect(
                lambda attempt, mx, pid=provider_id: self._set_combo_retrying(pid)
            )
            self._multi_workers[provider_id] = worker
            worker.start()

    # ------------------------------------------------------------------
    # Multi-provider completion
    # ------------------------------------------------------------------

    def _on_multi_complete(
        self,
        provider_id: Provider,
        model: str,
        full_text: str,
        input_tokens: int,
        output_tokens: int,
        estimated: bool = False,
    ) -> None:
        self._set_combo_waiting(provider_id, False)
        self._multi_workers.pop(provider_id, None)

        # Update spend
        cost = estimate_cost(model, input_tokens, output_tokens)
        if cost is not None and self._current_conv:
            self._db.add_conversation_spend(
                self._current_conv.id, provider_id.value, cost, estimated
            )
        self._update_spend_labels()

        # Always buffer — render in stable order when all are done
        label = _PROVIDER_DISPLAY[provider_id]
        self._column_buffer[provider_id] = (
            label, full_text, model, input_tokens, output_tokens, estimated
        )

        if not self._multi_workers:
            # All providers done — render in stable order
            if self._column_mode:
                self._render_column_responses()
            else:
                self._render_list_responses()
            self._column_buffer.clear()
            self._input.set_enabled(True)
            self._update_input_placeholder()
            self._update_input_color()

    def _compute_excluded_indices(self, messages: list[Message]) -> set[int]:
        """Return message indices that would NOT be sent to providers.

        Pinned messages whose target includes any currently-configured
        provider are NOT excluded (they stay visually unshaded), giving
        a visual cue that they are still sent despite being before the
        //limit cut-off.
        """
        if not self._current_conv or self._current_conv.limit_mark is None:
            return set()
        idx = self._db.get_mark(self._current_conv.id, self._current_conv.limit_mark)
        if idx is None or idx <= 0:
            return set()
        configured = set(self._router._providers.keys()) if self._router else set()
        excluded: set[int] = set()
        for i in range(min(idx, len(messages))):
            m = messages[i]
            if m.pinned and any(_pin_matches(m.pin_target, p) for p in configured):
                continue
            excluded.add(i)
        return excluded

    def _display_messages(self, messages: list[Message]) -> None:
        """Load messages into chat, detecting multi-provider groups.

        In list mode: adds 'X's take:' heading for multi-provider groups.
        In column mode: renders multi-provider groups as column tables.
        """
        import markdown as md_lib
        self._chat.clear_messages()
        # Tell chat which messages are excluded (before the //limit mark)
        self._chat.set_excluded_indices(self._compute_excluded_indices(messages))
        self._chat.setUpdatesEnabled(False)
        try:
            i = 0
            while i < len(messages):
                msg = messages[i]
                if msg.role != Role.ASSISTANT:
                    self._chat._messages.append(msg)
                    self._chat._insert_rendered(msg)
                    i += 1
                    continue

                # Collect consecutive assistant messages from different providers
                group: list[Message] = [msg]
                seen_providers = {msg.provider}
                j = i + 1
                while j < len(messages):
                    nxt = messages[j]
                    if nxt.role != Role.ASSISTANT or nxt.provider in seen_providers:
                        break
                    group.append(nxt)
                    seen_providers.add(nxt.provider)
                    j += 1

                if len(group) > 1:
                    ordered = sorted(group, key=lambda m: _PROVIDER_ORDER.index(m.provider) if m.provider in _PROVIDER_ORDER else 99)

                    # Use stored display_mode if available, else fall back to global toggle
                    stored_mode = group[0].display_mode
                    use_cols = stored_mode == "cols" if stored_mode else self._column_mode

                    if use_cols:
                        # Column table
                        md = md_lib.Markdown(extensions=["tables", "fenced_code", "sane_lists"])
                        header_cells = []
                        body_cells = []
                        provider_colors = []
                        # Check if this group is excluded (all messages at indices < limit)
                        group_indices = [messages.index(m) for m in ordered]
                        excluded = any(idx in self._chat._excluded_indices for idx in group_indices)
                        for m in ordered:
                            label = _PROVIDER_DISPLAY.get(m.provider, "Assistant")
                            base_color = self._provider_color(m.provider) if m.provider else "#d4d4d4"
                            color = self._chat._shade(base_color) if excluded else base_color
                            provider_colors.append(color)
                            md.reset()
                            rendered = md.convert(_strip_echoed_heading(m.content))
                            header_cells.append(
                                f'<th style="background-color:{color}; padding:8px; '
                                f'text-align:left; vertical-align:top;">{label}\'s take</th>'
                            )
                            body_cells.append(
                                f'<td style="background-color:{color}; padding:8px; '
                                f'vertical-align:top;">{rendered}</td>'
                            )
                        table_html = (
                            f'<table style="width:100%; border-collapse:collapse;">'
                            f'<tr>{"".join(header_cells)}</tr>'
                            f'<tr>{"".join(body_cells)}</tr>'
                            f'</table>'
                        )
                        for m in ordered:
                            self._chat._messages.append(m)
                        self._chat._insert_column_table(table_html, provider_colors)
                    else:
                        # List mode with headings
                        for m in ordered:
                            label = _PROVIDER_DISPLAY.get(m.provider, "Assistant")
                            clean = _strip_echoed_heading(m.content)
                            display_msg = Message(
                                role=m.role,
                                content=f"**{label}'s take:**\n\n{clean}",
                                provider=m.provider, model=m.model,
                                conversation_id=m.conversation_id, id=m.id,
                            )
                            self._chat._messages.append(m)
                            self._chat._insert_rendered(display_msg)
                else:
                    # Single assistant message — render as-is
                    self._chat._messages.append(msg)
                    self._chat._insert_rendered(msg)

                i = j
        finally:
            self._chat.setUpdatesEnabled(True)
        self._chat._scroll_to_bottom()

    def _render_list_responses(self) -> None:
        """Render buffered multi-provider responses as a vertical list in stable order."""
        ordered = [p for p in _PROVIDER_ORDER if p in self._column_buffer]
        for p in ordered:
            label, full_text, model, inp, out, est = self._column_buffer[p]
            full_text = _strip_echoed_heading(full_text)
            # Store raw content with display mode
            msg = Message(
                role=Role.ASSISTANT,
                content=full_text,
                provider=p,
                model=model,
                display_mode="lines",
                conversation_id=self._current_conv.id,
            )
            self._db.add_message(msg)
            self._current_conv.messages.append(msg)
            # Display with heading
            display_msg = Message(
                role=msg.role, content=f"**{label}'s take:**\n\n{full_text}",
                provider=msg.provider, model=msg.model,
                conversation_id=msg.conversation_id, id=msg.id,
            )
            self._chat._messages.append(msg)
            self._chat._insert_rendered(display_msg)
            self._chat._scroll_to_bottom()

    def _render_column_responses(self) -> None:
        """Render buffered multi-provider responses as a side-by-side table."""
        import html as html_mod
        import markdown as md_lib

        md = md_lib.Markdown(extensions=["tables", "fenced_code", "sane_lists"])

        # Build table HTML — one column per provider, in stable order
        providers = [p for p in _PROVIDER_ORDER if p in self._column_buffer]
        header_cells = []
        body_cells = []
        for p in providers:
            label, full_text, model, inp, out, est = self._column_buffer[p]
            full_text = _strip_echoed_heading(full_text)
            color = self._provider_color(p)
            md.reset()
            rendered = md.convert(full_text)
            header_cells.append(
                f'<th style="background-color:{color}; padding:8px; '
                f'text-align:left; vertical-align:top;">{label}\'s take</th>'
            )
            body_cells.append(
                f'<td style="background-color:{color}; padding:8px; '
                f'vertical-align:top;">{rendered}</td>'
            )

        table_html = (
            f'<table style="width:100%; border-collapse:collapse;">'
            f'<tr>{"".join(header_cells)}</tr>'
            f'<tr>{"".join(body_cells)}</tr>'
            f'</table>'
        )

        # Save each response as a separate DB message (for persistence)
        # but display as a single combined message
        for p in providers:
            label, full_text, model, inp, out, est = self._column_buffer[p]
            msg = Message(
                role=Role.ASSISTANT,
                content=_strip_echoed_heading(full_text),
                provider=p,
                model=model,
                display_mode="cols",
                conversation_id=self._current_conv.id,
            )
            self._db.add_message(msg)
            self._current_conv.messages.append(msg)

        # Insert the table as a visual element
        # Use a dummy message with the combined content for display
        combined = Message(
            role=Role.ASSISTANT,
            content=table_html,
            provider=providers[0],
            conversation_id=self._current_conv.id,
        )
        provider_colors = [self._provider_color(p) for p in providers]
        self._chat._insert_column_table(table_html, provider_colors)

    def _on_multi_error(self, provider_id: Provider, error: str) -> None:
        self._set_combo_waiting(provider_id, False)
        worker = self._multi_workers.pop(provider_id, None)
        transient = worker.last_error_transient if worker else False

        error_msg = Message(
            role=Role.ASSISTANT,
            content=f"[Error from {provider_id.value}: {error}]",
            provider=provider_id,
            conversation_id=self._current_conv.id,
        )
        self._db.add_message(error_msg)
        self._current_conv.messages.append(error_msg)
        self._chat.add_message(error_msg)

        # Stash for //retry
        self._retry_failed[provider_id] = (error, transient)
        self._retry_error_msg_ids[provider_id] = error_msg.id

        if not self._multi_workers:
            self._input.set_enabled(True)
            self._update_input_placeholder()
            self._update_input_color()

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
