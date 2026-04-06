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

from mchat.config import Config, PROVIDER_META
from mchat.db import Database
from mchat.models.conversation import Conversation
from mchat.models.message import Message, Provider, Role
from mchat.providers.base import BaseProvider
from mchat.providers.claude import ClaudeProvider
from mchat.providers.gemini_provider import GeminiProvider
from mchat.providers.openai_provider import OpenAIProvider
from mchat.providers.mistral_provider import MistralProvider
from mchat.providers.perplexity_provider import PerplexityProvider
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

        mistral_key = self._config.get("mistral_api_key")
        if mistral_key:
            providers[Provider.MISTRAL] = MistralProvider(
                api_key=mistral_key,
                default_model=self._config.get("mistral_model"),
            )

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
    def _combos(self) -> dict[Provider, "QComboBox"]:
        return self._provider_panel.combos()

    @property
    def _checkboxes(self) -> dict[Provider, "QCheckBox"]:
        return self._provider_panel.checkboxes()

    @property
    def _spend_labels(self) -> dict[Provider, "QLabel"]:
        return self._provider_panel.spend_labels()

    def _configured_provider_set(self) -> set[Provider]:
        return set(self._router._providers.keys()) if self._router else set()

    def _populate_model_combos_fast(self) -> None:
        configured = self._configured_provider_set()
        self._provider_panel.populate_from_config(configured)
        # Seed the catalog with whatever ended up in the combos so it's
        # never empty for configured providers (later async refresh
        # overwrites with the live lists).
        for p in configured:
            items = [
                self._provider_panel.combos()[p].itemText(i)
                for i in range(self._provider_panel.combos()[p].count())
            ]
            if items:
                self._model_catalog.set(p, items)

    def _populate_model_combos_async(self) -> None:
        providers = self._router._providers if self._router else {}

        def _on_async_done() -> None:
            # Sync the catalog from the newly-populated combos.
            for p, combo in self._provider_panel.combos().items():
                items = [combo.itemText(i) for i in range(combo.count())]
                if items:
                    self._model_catalog.set(p, items)

        self._provider_panel.populate_async(providers, on_done=_on_async_done)

    def _populate_model_combos(self) -> None:
        providers = self._router._providers if self._router else {}
        self._provider_panel.populate_from_providers(providers)
        for p, combo in self._provider_panel.combos().items():
            items = [combo.itemText(i) for i in range(combo.count())]
            if items:
                self._model_catalog.set(p, items)

    def _sync_checkboxes_from_selection(self) -> None:
        if not self._router:
            return
        self._provider_panel.sync_checkboxes(set(self._router.selection))

    def _on_selection_state_changed(self, _selection) -> None:
        """Action bundle: runs every UI refresh that depends on the
        current provider selection. Connected to
        ``ProviderSelectionState.selection_changed`` in __init__, so
        any code path that mutates selection via ``router.set_selection``
        or parse-driven updates automatically triggers the fan-out.
        """
        self._sync_checkboxes_from_selection()
        self._update_input_placeholder()
        self._update_input_color()

    def _on_checkbox_changed(self, provider_id: Provider) -> None:
        if not self._router:
            return
        selected = self._provider_panel.checked_providers()
        # Stage 3A.4: empty selection is valid (persona-first UX).
        # set_selection writes through SelectionState which emits
        # selection_changed → _on_selection_state_changed fan-out
        # handles sync/placeholder/color. We still need to persist.
        self._router.set_selection(selected)
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
        for attr in ("_settings_btn", "_providers_btn"):
            b = getattr(self, attr, None)
            if b is not None:
                b.setStyleSheet(style)

    def _provider_color(self, p: Provider) -> str:
        return self._config.get(PROVIDER_META[p.value]["color_key"])

    def _apply_all_combo_styles(self) -> None:
        self._provider_panel.apply_all_combo_styles()

    def _set_combo_waiting(self, p: Provider, waiting: bool) -> None:
        self._provider_panel.set_combo_waiting(p, waiting)

    def _set_combo_retrying(self, p: Provider) -> None:
        self._provider_panel.set_combo_retrying(p)

    def _update_input_color(self) -> None:
        if not self._router:
            return
        sel = self._router.selection
        if len(sel) == 1:
            color = self._provider_color(sel[0])
        else:
            # Multi-provider or empty selection → user colour
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
        title = self._current_conv.title.replace(" ", "_")[:40]
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Chat", f"{title}.html", "HTML Files (*.html)"
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
        """Rebuild the matrix panel for the current conversation's
        personas (explicit + synthetic defaults for uncovered providers)
        and populate it from the stored visibility matrix."""
        configured = set(self._router._providers.keys()) if self._router else set()
        entries: list[tuple[str, str, Provider]] = []
        if self._current_conv:
            from mchat.config import PROVIDER_META
            personas = self._db.list_personas(self._current_conv.id)
            covered_providers: set[Provider] = set()
            for p in personas:
                entries.append((p.id, p.name, p.provider))
                covered_providers.add(p.provider)
            # Synthetic defaults for configured providers with no explicit personas
            for prov in Provider:
                if prov in configured and prov not in covered_providers:
                    entries.append((prov.value, PROVIDER_META[prov.value]["display"], prov))
        else:
            from mchat.config import PROVIDER_META
            for prov in Provider:
                if prov in configured:
                    entries.append((prov.value, PROVIDER_META[prov.value]["display"], prov))
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

    def _on_rename_conversation(self, conv_id: int, new_title: str) -> None:
        self._conv_mgr.on_rename(conv_id, new_title)

    def _on_save_conversation(self, conv_id: int) -> None:
        self._conv_mgr.on_save(conv_id)

    def _on_delete_conversation(self, conv_id: int) -> None:
        self._conv_mgr.on_delete(conv_id)

    def _on_personas_requested(self, conv_id: int) -> None:
        """Open the PersonaDialog for the given conversation.

        Stage 3A.3: the sidebar's "Personas..." context-menu action
        emits this signal, we construct a modal PersonaDialog bound
        to the current DB + Config + conversation id, exec it, and
        refresh the chat after the dialog closes so any model/
        colour override changes take immediate effect.
        """
        from mchat.ui.persona_dialog import PersonaDialog
        dialog = PersonaDialog(
            self._db, self._config, conv_id, parent=self,
            models_cache=self._model_catalog.all(),
        )
        dialog.exec()
        # Any persona change should trigger a re-render so the new
        # colour / label / cutoff takes effect. _display_messages
        # refreshes the persona colour resolver cache internally.
        if self._current_conv and self._current_conv.id == conv_id:
            self._display_messages(self._current_conv.messages)

    # ------------------------------------------------------------------
    # Messaging
    # ------------------------------------------------------------------

    def _update_input_placeholder(self) -> None:
        if not self._router:
            self._input.set_placeholder("Configure an API key in Settings to start chatting")
            return
        sel = self._router.selection
        if not sel:
            self._input.set_placeholder(
                "No personas selected \u2014 use //addpersona or prefix a provider name"
            )
        elif len(sel) == 1:
            name = _PROVIDER_DISPLAY[sel[0]]
            self._input.set_placeholder(f"Message {name} \u2014 prefix another provider or use //select")
        else:
            names = ", ".join(_PROVIDER_DISPLAY[p] for p in sel)
            self._input.set_placeholder(f"Message {names} \u2014 use //select to change")

    def _selected_model(self, p: Provider) -> str:
        return self._combos[p].currentText()

    def _build_context(self, target) -> list[Message]:
        """Delegate to ui.context_builder — MainWindow is just the host.

        ``target`` may be either a Provider (legacy callers) or a
        PersonaTarget (Stage 2.6+). build_context handles both via
        the synthetic-default wrap.
        """
        return build_context(
            self._current_conv, target, self._db, self._config
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

        # set_selection fires ProviderSelectionState.selection_changed
        # which drives sync/placeholder/color via the fan-out handler.
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
        super().closeEvent(event)

    def _open_settings(self) -> None:
        self._settings_applier.open_settings()

    def _open_providers(self) -> None:
        self._settings_applier.open_providers()
