# ------------------------------------------------------------------
# Component: MainWindow
# Responsibility: Top-level window — wires sidebar, chat, input, providers
# Collaborators: all ui components, router, db, config, workers  (external: PySide6)
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

from mchat.config import Config, PROVIDER_META
from mchat.db import Database
from mchat.models.conversation import Conversation
from mchat.models.message import Message, Provider, Role
from mchat.provider_factory import build_providers
from mchat.providers.base import BaseProvider
from mchat.router import Router
from mchat.ui.chat_widget import ChatWidget
from mchat.ui.find_bar import FindBar
from mchat.ui.context_builder import build_context, compute_excluded_indices
from mchat.ui.conversation_manager import ConversationManager
from mchat.ui.matrix_panel import MatrixPanel
from mchat.ui.message_renderer import (
    MessageRenderer,
    PROVIDER_DISPLAY as _PROVIDER_DISPLAY_FROM_RENDERER,
    PROVIDER_ORDER as _PROVIDER_ORDER_FROM_RENDERER,
    strip_echoed_heading as _strip_echoed_heading,
)
from mchat.ui.preferences_adapter import PreferencesAdapter
from mchat.ui.settings_applier import SettingsApplier
from mchat.ui.provider_panel import ProviderPanel
from mchat.ui.send_controller import SendController
from mchat.ui.services import ServicesContext
from mchat.ui.state import ConversationSession, ModelCatalog, SelectionState
from mchat.ui.input_widget import InputWidget
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
        self._router: Router | None = None
        self._font_size = int(self._config.get("font_size") or 14)

        # Application-state objects (see ui/state.py).
        # ConversationSession owns the active conversation + its messages.
        # SelectionState owns which personas the next send addresses
        # (list[PersonaTarget] as of Stage 2.4).
        # ModelCatalog owns the per-provider model-id cache.
        self._session = ConversationSession(self)
        self._selection_state = SelectionState(parent=self)
        self._model_catalog = ModelCatalog(self)

        self._init_providers()

        # Shared services + state context — passed into extracted
        # controllers so they can depend on a narrow typed surface
        # instead of a full MainWindow reference. Constructed exactly
        # once here; subsequent provider rebuilds mutate it in place
        # via _rebuild_services() → set_router(). See ui/services.py
        # and #59.
        self._build_services()

        # Action bundle: whenever the provider selection changes (via
        # router.set_selection, checkbox toggle, //select, +/-, or the
        # parse path for prefix-only messages), fan out the dependent
        # UI refreshes as a single side-effect group. This replaces the
        # previous pattern where every caller had to manually invoke
        # _sync_checkboxes_from_selection / _update_input_placeholder /
        # _update_input_color in sequence after each mutation. See #58.
        self._selection_state.selection_changed.connect(
            self._on_selection_state_changed
        )
        # PreferencesAdapter + SettingsApplier must exist before _build_ui,
        # because _build_ui calls _restore_geometry -> prefs.restore_geometry
        # and wires the Settings button to settings_applier.open.
        self._prefs = PreferencesAdapter(self, self._services)
        self._settings_applier = SettingsApplier(self, self._services)
        self._build_ui()
        self._renderer = MessageRenderer(self._chat, self._config, self._db)
        self._send = SendController(self, self._services)
        self._conv_mgr = ConversationManager(self, self._services)
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

    # ------------------------------------------------------------------
    # _current_conv is now backed by the ConversationSession state object.
    # Existing callers that do ``self._current_conv`` or
    # ``host._current_conv = conv`` still work — reads forward to the
    # session, and writes go through session.set_current() so the
    # conversation_changed signal fires.
    # ------------------------------------------------------------------

    @property
    def _current_conv(self) -> Conversation | None:
        return self._session.current

    @_current_conv.setter
    def _current_conv(self, conv: Conversation | None) -> None:
        if conv is None:
            self._session.clear()
        else:
            self._session.set_current(conv)

    def _build_services(self) -> None:
        """Construct the ServicesContext exactly once, during startup.

        The context is a long-lived object that every extracted
        controller holds a reference to. Most fields (config, db,
        session, selection, model_catalog) are stable for the app's
        lifetime; the only field that changes at runtime is
        ``router`` (rebuilt when API keys are added/removed in
        Settings). Use ``_rebuild_services()`` after _init_providers
        to push the new router into the existing context.
        """
        self._services = ServicesContext(
            config=self._config,
            db=self._db,
            router=self._router,
            session=self._session,
            selection=self._selection_state,
            model_catalog=self._model_catalog,
        )

    def _rebuild_services(self) -> None:
        """Update the existing ServicesContext after a provider rebuild.

        Mutates ``router`` in place rather than replacing the whole
        context so every long-lived collaborator that cached the
        context reference stays correctly wired. See #59.
        """
        self._services.set_router(self._router)

    def _init_providers(self) -> None:
        providers = build_providers(self._config)

        try:
            default = Provider(self._config.get("default_provider"))
        except ValueError:
            default = Provider.CLAUDE
        # Fall back to first configured provider if default is unconfigured
        if default not in providers and providers:
            default = next(iter(providers))
        self._router = (
            Router(providers, default, selection_state=self._selection_state)
            if providers
            else None
        )

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
        self._sidebar.personas_requested.connect(self._on_personas_requested)
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
            color_mistral=self._config.get("color_mistral"),
            exclude_shade_mode=str(self._config.get("exclude_shade_mode") or "darken"),
            exclude_shade_amount=int(self._config.get("exclude_shade_amount") or 20),
        )
        # Delegate ChatWidget rebuilds through _display_messages so
        # multi-provider column groups are preserved
        self._chat._rebuild_callback = lambda: self._display_messages(
            self._current_conv.messages if self._current_conv else []
        )
        # Persona-aware colour resolver (Stage 3A.2). The resolver
        # caches per-conversation persona rows; set_conversation is
        # called on conversation switch, invalidate() after persona
        # add/edit/remove.
        from mchat.ui.persona_color_resolver import PersonaColorResolver
        self._persona_color_resolver = PersonaColorResolver(self._db, self._config)
        self._chat.set_persona_color_resolver(self._persona_color_resolver)
        self._find_bar = FindBar(self._chat)
        right_layout.addWidget(self._find_bar)
        right_layout.addWidget(self._chat, stretch=1)

        # ---- Provider bar (between chat and input) ----
        self._provider_panel = ProviderPanel(self._config, self._font_size)
        self._provider_panel.selection_changed.connect(self._on_checkbox_changed)
        self._bar_layout = self._provider_panel.layout_ref()

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

        # Personas button — opens PersonaDialog for the current conversation
        self._personas_btn = QPushButton("👤 Personas")
        self._apply_settings_btn_style(self._personas_btn)
        self._personas_btn.clicked.connect(self._open_personas)
        self._bar_layout.addWidget(self._personas_btn)

        # Providers button (per-provider config — API keys, models,
        # provider colours, provider system prompts) next to Settings.
        self._providers_btn = QPushButton("☁ Providers")
        self._apply_settings_btn_style(self._providers_btn)
        self._providers_btn.clicked.connect(self._open_providers)
        self._bar_layout.addWidget(self._providers_btn)

        # Settings button (general settings — font, shading, global
        # system prompt, user colour, default provider)
        self._settings_btn = QPushButton("⚙ Settings")
        self._apply_settings_btn_style()
        self._settings_btn.clicked.connect(self._open_settings)
        self._bar_layout.addWidget(self._settings_btn)

        right_layout.addWidget(self._provider_panel)

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

    # ------------------------------------------------------------------
    # Provider-bar delegators — the real state lives on self._provider_panel
    # ------------------------------------------------------------------

    @property
    def _combos(self) -> dict[str, "QComboBox"]:
        return self._provider_panel.combos()

    @property
    def _checkboxes(self) -> dict[str, "QCheckBox"]:
        return self._provider_panel.checkboxes()

    @property
    def _spend_labels(self) -> dict[str, "QLabel"]:
        return self._provider_panel.spend_labels()

    def _configured_provider_set(self) -> set[Provider]:
        return set(self._router._providers.keys()) if self._router else set()

    def _sync_toolbar_personas(self) -> None:
        """Rebuild the toolbar rows from the current conversation's
        personas. Called on conversation switch and after persona changes."""
        entries: list[tuple[str, str, Provider]] = []
        personas = []
        if self._current_conv:
            personas = self._db.list_personas(self._current_conv.id)
            for p in personas:
                entries.append((p.id, p.name, p.provider))
        self._provider_panel.set_personas(entries)
        if entries:
            # Populate model combos from the catalog (full model lists
            # fetched at startup) rather than just the config default.
            for p in personas:
                cached = self._model_catalog.get(p.provider)
                if cached:
                    self._provider_panel.set_persona_models(
                        p.id, cached, p.model_override,
                    )
                else:
                    # Fall back to config default only
                    meta = PROVIDER_META.get(p.provider.value, {})
                    model_key = meta.get("model_key", "")
                    default = self._config.get(model_key) if model_key else ""
                    self._provider_panel.set_persona_models(
                        p.id, [default] if default else [], p.model_override,
                    )
            self._apply_all_combo_styles()
            self._sync_checkboxes_from_selection()

    def _populate_model_combos_fast(self) -> None:
        configured = self._configured_provider_set()
        self._provider_panel.populate_from_config(configured)

    def _populate_model_combos_async(self) -> None:
        """Fetch model lists in background. Updates the model catalog
        directly from fetch results (not from combos, which may not
        exist yet if no personas are in the current conversation).
        Then refreshes the toolbar if persona rows exist."""
        providers = self._router._providers if self._router else {}
        if not providers:
            return

        import concurrent.futures

        def fetch_all() -> dict[Provider, list[str]]:
            results: dict[Provider, list[str]] = {}
            with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
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

        from PySide6.QtCore import QThread, Signal as _Sig

        class _Fetcher(QThread):
            done = _Sig(object)
            def run(self_inner):
                if not self_inner.isInterruptionRequested():
                    self_inner.done.emit(fetch_all())

        self._model_fetcher = _Fetcher()

        def _on_done(results: dict) -> None:
            for provider, models in results.items():
                if models:
                    self._model_catalog.set(provider, models)
            # Refresh toolbar combos if persona rows exist
            if self._provider_panel._personas:
                self._sync_toolbar_personas()
            self._model_fetcher = None

        self._model_fetcher.done.connect(_on_done)
        self._model_fetcher.start()

    def _populate_model_combos(self) -> None:
        providers = self._router._providers if self._router else {}
        self._provider_panel.populate_from_providers(providers)
        for p, combo in self._provider_panel.combos().items():
            items = [combo.itemText(i) for i in range(combo.count())]
            if items:
                self._model_catalog.set(p, items)

    def _sync_checkboxes_from_selection(self) -> None:
        selected_ids = {t.persona_id for t in self._selection_state.selection}
        self._provider_panel.sync_checkboxes(selected_ids)

    def _on_selection_state_changed(self, _selection) -> None:
        """Action bundle: runs every UI refresh that depends on the
        current persona selection."""
        self._sync_checkboxes_from_selection()
        self._update_input_placeholder()
        self._update_input_color()

    def _on_checkbox_changed(self, persona_id: str) -> None:
        """Handle a checkbox toggle in the toolbar. persona_id is the
        persona whose checkbox changed."""
        from mchat.ui.persona_target import PersonaTarget
        checked_ids = set(self._provider_panel.checked_persona_ids())
        # Build new selection from checked persona_ids
        new_selection = [
            t for t in self._selection_state.selection
            if t.persona_id in checked_ids
        ]
        # Add any newly checked that aren't in the current selection
        existing_ids = {t.persona_id for t in new_selection}
        for pid in checked_ids - existing_ids:
            provider = self._provider_panel._persona_providers.get(pid)
            if provider:
                new_selection.append(PersonaTarget(persona_id=pid, provider=provider))
        self._selection_state.set(new_selection)
        self._save_selection()

    def _apply_settings_btn_style(self, btn=None) -> None:
        """Apply the bar-button style. Without an argument, styles
        every button in the right-hand group (Settings + Providers)
        so font-size changes flow through to both uniformly."""
        style = (
            f"QPushButton {{ background: none; border: 1px solid #ccc; border-radius: 6px; "
            f"padding: 4px 12px; color: #666; font-size: {self._font_size - 1}px; }}"
            f"QPushButton:hover {{ background-color: #eee; }}"
        )
        if btn is not None:
            btn.setStyleSheet(style)
            return
        # No argument → style every bar button that exists
        for attr in ("_settings_btn", "_providers_btn", "_personas_btn"):
            b = getattr(self, attr, None)
            if b is not None:
                b.setStyleSheet(style)

    def _provider_color(self, p: Provider) -> str:
        return self._config.get(PROVIDER_META[p.value]["color_key"])

    def _apply_all_combo_styles(self) -> None:
        self._provider_panel.apply_all_combo_styles()

    def _set_combo_waiting(self, p, waiting: bool) -> None:
        """Accept either a Provider or a persona_id string."""
        pid = p.value if isinstance(p, Provider) else p
        self._provider_panel.set_combo_waiting(pid, waiting)

    def _set_combo_retrying(self, p) -> None:
        pid = p.value if isinstance(p, Provider) else p
        self._provider_panel.set_combo_retrying(pid)

    def _update_input_color(self) -> None:
        if not self._router:
            return
        sel = self._selection_state.selection
        if len(sel) == 1:
            color = self._provider_color(sel[0].provider)
        else:
            color = self._config.get("color_user")
        self._input.set_background(color)

    def _update_spend_labels(self) -> None:
        if self._current_conv:
            spend = self._db.get_conversation_spend(self._current_conv.id)
        else:
            spend = {}
        self._provider_panel.update_spend(spend)

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
        import os
        title = self._current_conv.title.replace(" ", "_")[:40]
        default_path = os.path.join(self._config.work_dir(), f"{title}.html")
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Chat", default_path, "HTML Files (*.html)"
        )
        if path:
            from mchat.ui.html_exporter import exporter_from_config
            personas = self._db.list_personas_including_deleted(
                self._current_conv.id
            )
            html = exporter_from_config(self._config).export(
                self._current_conv.messages, personas=personas,
            )
            with open(path, "w", encoding="utf-8") as f:
                f.write(html)

    def _zoom_in(self) -> None:
        self._prefs.zoom_in()

    def _zoom_out(self) -> None:
        self._prefs.zoom_out()

    def _zoom_reset(self) -> None:
        self._prefs.zoom_reset()

    def _set_font_size(self, size: int) -> None:
        self._prefs.set_font_size(size)

    def _apply_font_size(self) -> None:
        self._chat.update_font_size(self._font_size)
        self._input.update_font_size(self._font_size)
        self._sidebar.update_font_size(self._font_size)
        self._apply_settings_btn_style()
        self._provider_panel.update_font_size(self._font_size)

    # ------------------------------------------------------------------
    # Conversations
    # ------------------------------------------------------------------

    def _load_conversations(self) -> None:
        self._conv_mgr.load_conversations()

    def _on_conversation_selected(self, conv_id: int) -> None:
        self._conv_mgr.on_conversation_selected(conv_id)

    def _sync_matrix_panel(self) -> None:
        """Rebuild the matrix panel from the current conversation's
        explicit personas only. No synthetic defaults — providers
        without a persona simply don't appear in the matrix."""
        entries: list[tuple[str, str, Provider]] = []
        if self._current_conv:
            for p in self._db.list_personas(self._current_conv.id):
                entries.append((p.id, p.name, p.provider))
        self._matrix_panel.set_personas(entries)
        if self._current_conv:
            self._matrix_panel.load_matrix(self._current_conv.visibility_matrix or {})

    def _on_visibility_changed(self, matrix: dict) -> None:
        if not self._current_conv:
            return
        self._current_conv.visibility_matrix = matrix
        self._db.set_visibility_matrix(self._current_conv.id, matrix)

    def _on_new_chat(self) -> None:
        self._conv_mgr.new_chat()
        # Auto-open PersonaDialog so the user can set up their first
        # persona immediately (persona-first UX).
        if self._current_conv:
            self._on_personas_requested(self._current_conv.id)

    def _on_rename_conversation(self, conv_id: int, new_title: str) -> None:
        self._conv_mgr.on_rename(conv_id, new_title)

    def _on_save_conversation(self, conv_id: int) -> None:
        self._conv_mgr.on_save(conv_id)

    def _on_delete_conversation(self, conv_id: int) -> None:
        self._conv_mgr.on_delete(conv_id)

    def _on_personas_requested(self, conv_id: int) -> None:
        """Open the PersonaDialog for the given conversation.

        After the dialog closes, ensure every active persona has its
        pinned instructions (name identity + setup note) and is in the
        selection. Handles both newly created personas and existing
        ones that were created before this code existed.
        """
        from mchat.ui.persona_dialog import PersonaDialog

        dialog = PersonaDialog(
            self._db, self._config, conv_id, parent=self,
            models_cache=self._model_catalog.all(),
        )
        dialog.exec()

        conv = self._current_conv
        if conv and conv.id == conv_id:
            self._ensure_persona_pins(conv_id)
            self._display_messages(conv.messages)
            self._sync_matrix_panel()
            self._sync_toolbar_personas()

    def _ensure_persona_pins(self, conv_id: int) -> None:
        """Delegate to the extracted ensure_persona_pins function."""
        from mchat.ui.persona_pins import ensure_persona_pins

        conv = self._current_conv
        if conv is None or conv.id != conv_id:
            return
        ensure_persona_pins(
            self._db, conv, conv.messages, self._selection_state,
        )

    # ------------------------------------------------------------------
    # Messaging
    # ------------------------------------------------------------------

    def _update_input_placeholder(self) -> None:
        if not self._router:
            self._input.set_placeholder("Configure an API key in Settings to start chatting")
            return
        sel = self._selection_state.selection
        if not sel:
            self._input.set_placeholder(
                "No personas selected \u2014 use //addpersona or prefix a persona name"
            )
        elif len(sel) == 1:
            # Show persona name if we can find it, else provider display name
            pid = sel[0].persona_id
            label = _PROVIDER_DISPLAY.get(sel[0].provider, pid)
            if self._current_conv:
                for p in self._db.list_personas(self._current_conv.id):
                    if p.id == pid:
                        label = p.name
                        break
            self._input.set_placeholder(f"Message {label}")
        else:
            labels = []
            personas = (
                self._db.list_personas(self._current_conv.id)
                if self._current_conv else []
            )
            pid_to_name = {p.id: p.name for p in personas}
            for t in sel:
                labels.append(pid_to_name.get(t.persona_id, t.persona_id))
            self._input.set_placeholder(f"Message {', '.join(labels)}")

    def _selected_model(self, p) -> str:
        """Accept either a Provider or a persona_id string."""
        key = p.value if isinstance(p, Provider) else p
        combo = self._combos.get(key)
        return combo.currentText() if combo else ""

    def _build_context(self, target, visible_persona_ids=None) -> list[Message]:
        """Delegate to ui.context_builder — MainWindow is just the host.

        ``target`` may be either a Provider (legacy callers) or a
        PersonaTarget (Stage 2.6+). build_context handles both via
        the synthetic-default wrap.
        """
        return build_context(
            self._current_conv, target, self._db, self._config,
            visible_persona_ids=visible_persona_ids,
        )

    # ------------------------------------------------------------------
    # // commands (delegated to ui.commands module)
    # ------------------------------------------------------------------

    def _handle_selection_adjust(self, text: str) -> bool:
        """Handle +name / -name selection adjustments.

        Stage 4.3: resolves persona names first, then falls back to
        provider shorthands. Provider shorthands expand to all active
        personas on that provider (or the synthetic default if none).
        """
        from mchat.models.persona import slugify_persona_name
        from mchat.router import PREFIX_TO_PROVIDER
        from mchat.ui.persona_target import PersonaTarget, synthetic_default

        op = text[0]  # '+' or '-'
        name = text[1:].strip().lower()

        if not self._router:
            self._chat.add_note("Error: no providers configured")
            return True

        # Build the targets to add/remove
        targets_to_adjust: list[PersonaTarget] = []
        display_name = name

        # Try persona name first (if a conversation is active)
        if self._current_conv:
            try:
                slug = slugify_persona_name(name)
            except ValueError:
                slug = ""
            if slug:
                personas = self._db.list_personas(self._current_conv.id)
                matched = [p for p in personas if p.name_slug == slug]
                if matched:
                    for p in matched:
                        targets_to_adjust.append(
                            PersonaTarget(persona_id=p.id, provider=p.provider)
                        )
                    display_name = matched[0].name

        # Fall back to provider shorthand — synthetic default only.
        # Personas are addressed by name, not provider shorthand.
        if not targets_to_adjust:
            provider = PREFIX_TO_PROVIDER.get(name)
            if provider is None:
                return False  # not a provider or persona name

            configured = set(self._router._providers.keys())
            if provider not in configured:
                self._chat.add_note(f"Error: {_PROVIDER_DISPLAY[provider]} has no API key")
                return True

            display_name = _PROVIDER_DISPLAY[provider]
            targets_to_adjust.append(synthetic_default(provider))

        # Apply the adjustment to the current selection
        current = list(self._selection_state.selection)

        if op == "+":
            changed = False
            for t in targets_to_adjust:
                if t not in current:
                    current.append(t)
                    changed = True
            if changed:
                self._selection_state.set(current)
            self._chat.add_note(f"selected: +{display_name}")
        else:  # '-'
            to_remove = set(targets_to_adjust)
            new_selection = [t for t in current if t not in to_remove]
            if len(new_selection) == len(current):
                self._chat.add_note(f"{display_name} is not in current selection")
                return True
            if not new_selection:
                self._chat.add_note("Error: cannot remove the last target from selection")
                return True
            self._selection_state.set(new_selection)
            self._chat.add_note(f"selected: -{display_name}")

        self._save_selection()
        return True

    def _handle_command(self, text: str) -> bool:
        stripped = text.strip()
        if not stripped.startswith("//"):
            return False
        parts = stripped.split(None, 1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""
        from mchat.ui.commands import dispatch
        handled = dispatch(cmd, arg, self)
        if not handled:
            self._chat.add_note(f"Unknown command: {cmd} — type //help for a list")
        return True  # always consume // input, even if unrecognized

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
        self._conv_mgr.save_selection()

    # ------------------------------------------------------------------

    def _on_message_submitted(self, text: str) -> None:
        """Delegate to SendController."""
        self._send.on_message_submitted(text)

    def _send_single(self, target) -> None:
        """Accepts either a Provider (legacy) or a PersonaTarget (new)."""
        if isinstance(target, Provider):
            from mchat.ui.persona_target import synthetic_default
            target = synthetic_default(target)
        self._send.send_single(target)

    def _send_multi(
        self,
        targets,
        context_override=None,
    ) -> None:
        """Accepts list[Provider] (legacy) or list[PersonaTarget] (new).
        ``context_override`` is keyed by persona_id."""
        if targets and isinstance(targets[0], Provider):
            from mchat.ui.persona_target import synthetic_default
            targets = [synthetic_default(p) for p in targets]
        self._send.send_multi(targets, context_override=context_override)

    def _compute_excluded_indices(self, messages: list[Message]) -> set[int]:
        """Delegate to ui.context_builder — single source of truth for
        which messages fall outside the current context."""
        if not self._current_conv:
            return set()
        configured = set(self._router._providers.keys()) if self._router else set()
        return compute_excluded_indices(self._current_conv, self._db, configured)

    def _display_messages(self, messages: list[Message]) -> None:
        """Delegate rendering to MessageRenderer.

        Refreshes the persona colour resolver's cache before every
        render so persona add/edit/remove (via commands or dialog)
        immediately takes effect. This is a cheap DB query per
        render — much cheaper than tracking every mutation site.
        """
        configured = set(self._router._providers.keys()) if self._router else set()
        if self._current_conv is not None:
            self._persona_color_resolver.set_conversation(self._current_conv.id)
        else:
            self._persona_color_resolver.set_conversation(None)
        self._renderer.display_messages(
            self._current_conv, messages, self._column_mode, configured
        )

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    def _restore_geometry(self) -> None:
        self._prefs.restore_geometry()

    def _save_geometry(self) -> None:
        self._prefs.save_geometry()

    def closeEvent(self, event) -> None:
        self._prefs.save_geometry()
        # #175: stop active StreamWorkers so in-flight API calls don't
        # outlive the window and crash during teardown.
        try:
            self._send.stop_all_workers()
        except Exception:
            pass
        # #129: stop any background TitleWorkers so they don't fire
        # after the DB is closed and crash the app during teardown.
        try:
            self._send.stop_all_title_workers()
        except Exception:
            pass
        # #185: stop the background model fetcher if still running.
        try:
            if self._model_fetcher is not None:
                self._model_fetcher.requestInterruption()
                self._model_fetcher.wait(2000)
                self._model_fetcher = None
        except Exception:
            pass
        super().closeEvent(event)

    def _open_personas(self) -> None:
        if self._current_conv:
            self._on_personas_requested(self._current_conv.id)
        else:
            self._chat.add_note("Error: no active conversation")

    def _open_settings(self) -> None:
        self._settings_applier.open_settings()

    def _open_providers(self) -> None:
        self._settings_applier.open_providers()
