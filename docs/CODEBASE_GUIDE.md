# Codebase Guide

This document is a first-pass orientation for a developer reading `mchat` for
the first time. It explains where the application starts, how the source tree is
organised, and which files are involved in the main runtime call paths.

The guide uses the project UI terms used in discussion:

- `toolbar`: the bar between the chat area and the input area, with
  persona/provider selection, model controls, cost display, and
  Personas / Providers / Settings buttons.
- `chatlist`: the conversation list on the left.
- `chat`: the conversation display area.
- `input`: the text entry area at the bottom.

## Central Entry Point

The package entry point is defined in `pyproject.toml`:

```toml
[project.scripts]
mchat = "mchat.main:main"
```

The startup path is:

```text
mchat.main.main()
  -> create QApplication
  -> create Config
  -> create Database
  -> create MainWindow(config, db)
  -> show the window
  -> enter the Qt event loop
```

The relevant files are:

| File | Role |
| --- | --- |
| `src/mchat/main.py` | Process entry point, crash logging hook, Qt application creation, icon setup, window creation. |
| `src/mchat/config.py` | Local configuration defaults, provider metadata, API key/model/color/system-prompt config keys. |
| `src/mchat/db.py` | Runtime database API for conversations, messages, personas, marks, spend, and visibility. |
| `src/mchat/db_migrations.py` | SQLite schema creation and migrations. |
| `src/mchat/ui/main_window.py` | Main composition root. Builds services, providers, router, UI widgets, controllers, and signal wiring. |

`MainWindow` is still the central composition root. It should be read as the
place where long-lived application objects are constructed and wired together,
not as the place where every behaviour is implemented.

## Source Hierarchy

### Root Package

| Path | Purpose |
| --- | --- |
| `src/mchat/main.py` | Starts the desktop app. |
| `src/mchat/config.py` | Config storage and `PROVIDER_META`. |
| `src/mchat/db.py` | High-level SQLite access. |
| `src/mchat/db_migrations.py` | Schema migration functions. |
| `src/mchat/router.py` | Provider-level `@` prefix parsing and provider selection fallback. |
| `src/mchat/provider_factory.py` | Builds configured provider instances from `Config` and `PROVIDER_META`. |
| `src/mchat/pricing.py` | Cost estimation. |
| `src/mchat/dot_renderer.py` | Graphviz/DOT rendering support. |
| `src/mchat/mermaid_renderer.py` | Mermaid rendering support. |
| `src/mchat/diagram_prompt.py` | Diagram-related prompt/context support. |
| `src/mchat/debug_logger.py` | Optional per-provider/persona I/O logging. |

### Models

| Path | Purpose |
| --- | --- |
| `src/mchat/models/message.py` | `Role`, `Provider`, and `Message`. This is the central message/provider identity model. |
| `src/mchat/models/conversation.py` | `Conversation` dataclass. |
| `src/mchat/models/persona.py` | `Persona`, persona id generation, slugging, and persona-name validation. |

### Providers

| Path | Purpose |
| --- | --- |
| `src/mchat/providers/base.py` | Provider interface and shared message-formatting helpers. |
| `src/mchat/providers/claude.py` | Anthropic Claude provider. |
| `src/mchat/providers/openai_compat.py` | Shared base for OpenAI-compatible providers. |
| `src/mchat/providers/openai_provider.py` | OpenAI provider. |
| `src/mchat/providers/gemini_provider.py` | Gemini via OpenAI-compatible endpoint. |
| `src/mchat/providers/perplexity_provider.py` | Perplexity provider. |
| `src/mchat/providers/mistral_provider.py` | Mistral provider via the Mistral SDK. |
| `src/mchat/providers/apertus_provider.py` | Apertus via Infomaniak's OpenAI-compatible endpoint. |

Provider creation is intentionally centralised in `provider_factory.py` so
`MainWindow` does not need a hand-written block for each provider.

### Services

| Path | Purpose |
| --- | --- |
| `src/mchat/services/persona_service.py` | Non-Qt persona create/update/remove/reorder/import/export operations used by commands and the persona dialog. |
| `src/mchat/ui/services.py` | `ServicesContext`, a shared holder for `Config`, `Database`, `Router`, and selection state used by UI controllers. |

### UI

| Path | Purpose |
| --- | --- |
| `src/mchat/ui/main_window.py` | UI composition root and high-level signal wiring. |
| `src/mchat/ui/sidebar.py` | Chatlist widget and context menu. |
| `src/mchat/ui/provider_panel.py` | Toolbar rows: personas/providers, model combos, checkboxes, spend labels. |
| `src/mchat/ui/chat_widget.py` | Chat display widget. Delegates rich document work to mixins. |
| `src/mchat/ui/chat_document.py` | QTextDocument rendering, markdown insertion, diagram resources, shading, column tables. |
| `src/mchat/ui/chat_export.py` | Copy/export helpers mixed into `ChatWidget`. |
| `src/mchat/ui/input_widget.py` | Input area and send button. |
| `src/mchat/ui/matrix_panel.py` | Persona/provider visibility matrix UI. |
| `src/mchat/ui/message_renderer.py` | Converts stored messages into chat display groups and calls `ChatWidget`. |
| `src/mchat/ui/html_exporter.py` | Conversation-to-HTML export. |
| `src/mchat/ui/send_controller.py` | Send orchestration: command guard, target resolution, context building, worker launch, completion/error handling, retry, edit replay. |
| `src/mchat/ui/conversation_manager.py` | Conversation load/switch/create/rename/save/delete flow. |
| `src/mchat/ui/title_generator.py` | Auto-title orchestration for new conversations. |
| `src/mchat/ui/persona_dialog.py` | Persona editor dialog. |
| `src/mchat/ui/persona_resolver.py` | Conversation-scoped `@persona`, `@provider`, `@all`, and `@others` target resolution. |
| `src/mchat/ui/persona_target.py` | `PersonaTarget` and synthetic default persona helper. |
| `src/mchat/ui/persona_resolution.py` | Effective prompt/model/color fallback helpers. |
| `src/mchat/ui/persona_color_resolver.py` | Cached persona color resolution for chat rendering. |
| `src/mchat/ui/persona_pins.py` | Ensures persona identity/system-prompt pins exist and stay current. |
| `src/mchat/ui/visibility.py` | Visibility filtering for persona/provider context. |
| `src/mchat/ui/state.py` | Shared UI state, including current conversation and selected persona targets. |
| `src/mchat/ui/preferences_adapter.py` | Adapter used by settings-related UI code. |
| `src/mchat/ui/settings_applier.py` | Applies Settings / Providers dialog changes to the running app. |
| `src/mchat/ui/settings_dialog.py` | General settings dialog. |
| `src/mchat/ui/providers_dialog.py` | Provider configuration dialog. |
| `src/mchat/ui/find_bar.py` | Chat search UI. |
| `src/mchat/ui/stats.py` | Context/cost statistics helpers. |
| `src/mchat/ui/dot_markdown_ext.py` | Markdown extension for DOT blocks. |
| `src/mchat/ui/mermaid_markdown_ext.py` | Markdown extension for Mermaid blocks. |
| `src/mchat/ui/message_bubble.py` | Message bubble rendering helpers, where used. |

### Commands

`src/mchat/ui/commands/` contains `//` command handling. The dispatcher is
`src/mchat/ui/commands/__init__.py`.

| Path | Purpose |
| --- | --- |
| `commands/__init__.py` | Dispatches `//command` text to a domain handler. |
| `commands/host.py` | Protocol documenting what command handlers may touch on the host. |
| `commands/history.py` | History/edit/limit/pop/hide/unhide/retry/rename/stats commands. |
| `commands/selection.py` | Selection/layout/visibility/provider-list commands. |
| `commands/pins.py` | Pin/unpin/list-pin commands. |
| `commands/personas.py` | Persona create/edit/remove/list commands. |
| `commands/help.py` | Help text command. |

### Workers

| Path | Purpose |
| --- | --- |
| `src/mchat/workers/stream_worker.py` | Background Qt thread for provider streaming, transient retry, and usage capture. |
| `src/mchat/workers/title_worker.py` | Background one-shot worker for generating short conversation titles. |

## Main Runtime Call Paths

### Startup

```text
main.py
  -> Config
  -> Database
  -> MainWindow
      -> ServicesContext
      -> build_providers(config)
          -> provider classes
      -> Router(providers, selection_state)
      -> Sidebar / ProviderPanel / ChatWidget / InputWidget / MatrixPanel
      -> ConversationManager
      -> SendController
      -> MessageRenderer
```

Key points:

- `Config` supplies provider metadata, keys, model defaults, colors, and prompt defaults.
- `Database` opens SQLite and runs migrations.
- `build_providers()` instantiates only providers that have required config.
- `Router` remains provider-level. Persona-level routing is handled later by `PersonaResolver`.
- `MainWindow` wires signals between widgets and controllers.

### Sending a Message

```text
InputWidget.message_submitted
  -> MainWindow._on_message_submitted
  -> SendController.on_message_submitted
      -> command handling, if text starts with //
      -> selection adjustment, if text is +name / -name
      -> PersonaResolver.resolve(text, conv_id, db)
          -> active personas from Database
          -> Router / SelectionState for provider fallback and sticky selection
      -> context_builder.build_context(...)
          -> Database.get_messages
          -> effective persona prompt resolution
          -> visibility filtering
          -> pin rescue
      -> StreamWorker(provider, context, model)
          -> provider.stream(...)
      -> SendController._on_complete or _on_error
          -> Database.add_message
          -> Database.add_conversation_spend
          -> MessageRenderer.display_messages
          -> ChatWidget
```

The important distinction is:

- `Router` knows about provider shorthands such as `@claude` and `@gpt`.
- `PersonaResolver` knows about conversation-scoped personas such as `@critic`
  or `@translator`.
- `SendController` owns the send lifecycle and worker callbacks.

### Commands

```text
SendController.on_message_submitted
  -> MainWindow._handle_command
  -> commands.dispatch(cmd, arg, host)
      -> commands.history / selection / pins / personas / help
      -> Database, Router, ChatWidget, InputWidget, Sidebar through CommandHost
```

Command handlers deliberately receive a `CommandHost` protocol rather than a
concrete `MainWindow` type. This does not remove all coupling, but it documents
the surface command handlers are allowed to use.

### Conversation Switching

```text
Sidebar.conversation_selected
  -> MainWindow._on_conversation_selected
  -> ConversationManager.on_conversation_selected
      -> save current draft
      -> Database.get_conversation
      -> Database.get_messages
      -> restore sticky selection / draft
      -> MainWindow UI sync hooks
      -> MessageRenderer.display_messages
```

`ConversationManager` is the first file to read when changing chatlist
behaviour.

### Persona Editing

```text
MainWindow._open_personas
  -> PersonaDialog
      -> PersonaService
          -> Database persona CRUD
          -> effective persona prompt resolution
  -> MainWindow sync after dialog changes
      -> toolbar refresh
      -> persona pins refresh
      -> chat re-render where needed
```

Use `PersonaService` for persona data operations that should be shared between
command and dialog paths. Use `PersonaDialog` only for dialog-specific UI.

### Rendering and Export

```text
MessageRenderer.display_messages
  -> resolve labels/personas
  -> group responses by persona/provider
  -> ChatWidget.load_messages / add_message / add_mark_list
      -> ChatDocumentMixin
          -> markdown conversion
          -> DOT/Mermaid resource wiring
          -> list or column layout

html_exporter.export_conversation
  -> Database messages/personas
  -> markdown conversion and diagram handling
  -> HTML file output
```

Read `message_renderer.py` before changing grouping, labels, or column/list
layout. Read `chat_document.py` before changing QTextDocument rendering details.

### Provider and Model Configuration

```text
ProvidersDialog / SettingsDialog
  -> Config
  -> SettingsApplier
  -> MainWindow._init_providers
      -> build_providers(config)
      -> Router
      -> toolbar model combo refresh
```

Provider metadata should usually be added to `PROVIDER_META` first. Provider
classes should stay small when they can reuse `OpenAICompatibleProvider`.

## File Relationship Map

This is a practical map of the main file-to-file dependencies. It is not an
exhaustive line-level call graph; it shows the relationships a developer should
understand first.

| File | Main local dependencies |
| --- | --- |
| `main.py` | `config`, `db`, `debug_logger`, `ui.main_window` |
| `ui/main_window.py` | `provider_factory`, `router`, `ui.services`, `ui.state`, `ui.sidebar`, `ui.provider_panel`, `ui.chat_widget`, `ui.input_widget`, `ui.matrix_panel`, `ui.conversation_manager`, `ui.send_controller`, `ui.message_renderer`, `ui.persona_dialog`, `ui.settings_applier` |
| `ui/services.py` | `config`, `db`, `router`, `ui.state` |
| `provider_factory.py` | `config.PROVIDER_META`, `models.message.Provider`, provider classes |
| `router.py` | `models.message.Provider`, `providers.base`, `ui.persona_target` |
| `ui/persona_resolver.py` | `db`, `router`, `models.message.Provider`, `ui.persona_target` |
| `ui/persona_target.py` | `models.message.Provider` |
| `ui/persona_resolution.py` | `config`, `models.persona` |
| `ui/send_controller.py` | `ui.services`, `ui.persona_resolver`, `ui.persona_resolution`, `ui.context_builder`, `ui.message_renderer`, `ui.title_generator`, `workers.stream_worker`, `pricing`, `debug_logger` |
| `ui/context_builder.py` | `db`, `models.conversation`, `models.message`, `models.persona`, `diagram_prompt`, `ui.visibility`, `ui.persona_resolution`, `ui.persona_target` |
| `ui/visibility.py` | `models.message`, `ui.persona_target` |
| `workers/stream_worker.py` | `providers.base`, `models.message`, `debug_logger` |
| `workers/title_worker.py` | `providers.base`, `models.message` |
| `ui/title_generator.py` | `workers.title_worker`, `models.message`, `ui.persona_target` |
| `ui/conversation_manager.py` | `ui.services`, `ui.html_exporter`, `ui.persona_target`, `models.message` |
| `ui/message_renderer.py` | `db`, `config`, `models.conversation`, `models.message`, `models.persona`, `ui.chat_widget`, `ui.context_builder` |
| `ui/chat_widget.py` | `ui.chat_document`, `ui.chat_export`, `models.message` |
| `ui/chat_document.py` | `models.message`, `ui.dot_markdown_ext`, `ui.mermaid_markdown_ext` |
| `ui/html_exporter.py` | `config`, `models.message`, `models.persona`, `ui.chat_export`, `ui.dot_markdown_ext`, `ui.mermaid_markdown_ext` |
| `ui/persona_dialog.py` | `db`, `config`, `models.persona`, `services.persona_service`, `ui.persona_resolution` |
| `services/persona_service.py` | `db`, `config`, `models.persona`, `models.message`, `ui.persona_resolution`, `ui.persona_resolver` |
| `ui/persona_pins.py` | `db`, `models.conversation`, `models.message`, `ui.persona_target`, `ui.state` |
| `ui/provider_panel.py` | `config`, `models.message.Provider`, `pricing` |
| `ui/providers_dialog.py` | `config`, `models.message.Provider`, `providers.base` |
| `ui/settings_applier.py` | `config`, `ui.providers_dialog`, `ui.settings_dialog`, `ui.services` |
| `ui/commands/__init__.py` | `ui.commands.history`, `selection`, `pins`, `personas`, `help`, `commands.host` |
| `ui/commands/history.py` | `commands.host`, `context_builder`, `stats`, `models.message` |
| `ui/commands/selection.py` | `commands.host`, `router`, `models.message` |
| `ui/commands/pins.py` | `commands.host`, `router`, `models.message`, `models.persona` |
| `ui/commands/personas.py` | `commands.host`, `router`, `models.message`, `models.persona`, `ui.persona_target` |
| `db.py` | `db_migrations`, `models.conversation`, `models.message`, `models.persona` |
| `db_migrations.py` | SQLite schema and migrations only |
| `providers/base.py` | `models.message` |
| `providers/openai_compat.py` | `providers.base`, `models.message` |
| `providers/openai_provider.py` | `providers.openai_compat`, `models.message.Provider` |
| `providers/gemini_provider.py` | `providers.openai_compat`, `models.message.Provider` |
| `providers/perplexity_provider.py` | `providers.openai_compat`, `models.message.Provider` |
| `providers/apertus_provider.py` | `providers.openai_compat`, `models.message.Provider` |
| `providers/claude.py` | `providers.base`, `models.message` |
| `providers/mistral_provider.py` | `providers.base`, `models.message` |

## Suggested Reading Order

For a first codebase pass, read in this order:

1. `README.md` for user-visible behaviour and vocabulary.
2. `src/mchat/main.py` for process startup.
3. `src/mchat/ui/main_window.py` for object construction and signal wiring.
4. `src/mchat/ui/services.py` and `src/mchat/ui/state.py` for shared state.
5. `src/mchat/provider_factory.py`, `src/mchat/router.py`, and
   `src/mchat/ui/persona_resolver.py` for provider/persona targeting.
6. `src/mchat/ui/send_controller.py` for the send lifecycle.
7. `src/mchat/ui/context_builder.py` and `src/mchat/ui/visibility.py` for what
   context is sent to providers.
8. `src/mchat/workers/stream_worker.py` and one provider implementation, usually
   `providers/openai_compat.py`, for API calls.
9. `src/mchat/ui/message_renderer.py`, `src/mchat/ui/chat_widget.py`, and
   `src/mchat/ui/chat_document.py` for display.
10. `src/mchat/db.py` and `src/mchat/db_migrations.py` for persistence.

When changing one feature, start from the runtime path instead of the directory
tree. For example, a routing change starts at `persona_resolver.py` / `router.py`;
a rendering change starts at `message_renderer.py`; a conversation-list change
starts at `conversation_manager.py` and `sidebar.py`.

## Testing

### Running

```bash
.venv-win/Scripts/pytest          # full suite (Windows)
.venv/bin/pytest                  # full suite (WSL/Linux)
```

There is no separate integration vs unit split. The entire suite runs as one
pass (~750 tests, ~90 seconds).

### Structure

Each test file is self-contained: fixtures are defined locally, not in a shared
`conftest.py`. There is no cross-module fixture import.

Every test file starts with the standard CRC header comment (`Component`,
`Responsibility`, `Collaborators`) matching the production code convention.

### Naming

- **Classes**: `Test<Feature>` — e.g. `TestComposition`,
  `TestSelectionSync`, `TestMoveUpDown`.
- **Methods**: `test_<action>_<expected_result>` — e.g.
  `test_checkbox_toggle_updates_selection`,
  `test_rename_updates_identity_pin_content`.
- **Docstrings** reference issue numbers when the test was written for a
  specific bug or feature: `"""#130 — retry in place ..."""`

### Fixtures

**Database isolation** — every DB test creates a fresh SQLite file in
`tmp_path`:

```python
@pytest.fixture
def db(tmp_path):
    database = Database(db_path=tmp_path / "test.db")
    yield database
    database.close()
```

**Config isolation** — same pattern, with pre-set keys as needed:

```python
@pytest.fixture
def config(tmp_path):
    cfg = Config(config_path=tmp_path / "cfg.json")
    cfg.set("anthropic_api_key", "test-key")
    cfg.save()
    return cfg
```

**Qt widgets** — `pytest-qt`'s `qtbot` fixture manages the event loop.
Widgets are registered for cleanup with `qtbot.addWidget()`:

```python
@pytest.fixture
def dialog(qtbot, db, config, conv):
    d = PersonaDialog(db, config, conv.id)
    qtbot.addWidget(d)
    return d
```

### Fake Providers

`test_main_window.py` defines `make_fake_provider_class(pid)` which returns a
concrete `BaseProvider` subclass hardwired to a `Provider` enum value. It
yields `"ok"` on `stream()`, returns `["fake-model-1", "fake-model-2"]` from
`list_models()`, and accepts `**kwargs` so provider-specific constructor
arguments (like Apertus `product_id`) don't break it.

The `main_window` fixture patches `build_providers` to return fake instances
for every provider, so no test hits the network:

```python
def _fake_build(config):
    return {p: make_fake_provider_class(p)(api_key="fake") for p in Provider}
monkeypatch.setattr(mw_mod, "build_providers", _fake_build)
```

### Dialog Mocking

Tests that open modal dialogs (PersonaDialog, settings) must prevent the Qt
event loop from blocking. The standard pattern patches `exec` to return
immediately:

```python
monkeypatch.setattr(pd_mod.PersonaDialog, "exec", lambda self: 0)
```

Variations exist for testing specific dialog behaviour:

- **SpyDialog** — records constructor args for assertion, then returns 0.
- **AutoCreateDialog** — creates a persona inside `exec()` before returning,
  so the test can verify post-dialog sync behaviour.
- **NoOpDialog** — closes without action, for testing the "cancel" path.

### Async Waiting

For tests that trigger background work (streaming, model fetching),
`qtbot.waitUntil` polls a condition with a timeout:

```python
qtbot.waitUntil(
    lambda: len(main_window._send._multi_workers) == 0,
    timeout=5000,
)
```

### Service-Level Testing

Many tests exercise service methods directly on dialog or controller
instances rather than simulating UI clicks. For example,
`test_persona_dialog.py` calls `dialog.create_persona(...)` and checks DB
state — this is fast and stable because it skips the Qt event loop. The
same pattern applies to `PersonaService` tests, which don't need `qtbot`
at all.

### Test-First Workflow

The project follows a strict test-first workflow (documented in
`~/.claude/CLAUDE.md`):

1. Write or update tests for the desired outcome.
2. Run them to confirm they **fail**.
3. Commit the tests alone.
4. Implement to make them pass.
5. Commit the implementation separately.

This means every feature or bug fix has at least two commits: one for the
failing tests and one for the implementation that makes them green.
