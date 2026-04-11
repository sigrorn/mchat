# ------------------------------------------------------------------
# Component: test_main_window
# Responsibility: pytest-qt smoke tests for MainWindow composition
#                 and the integration flows that cross multiple
#                 sub-components (send, commands, conversation
#                 switching, settings application). All tests run
#                 against fake providers so nothing touches the
#                 network.
# Collaborators: ui.main_window, db, config, providers (mocked)
# ------------------------------------------------------------------
from __future__ import annotations

import pytest

from mchat.config import Config
from mchat.db import Database
from mchat.models.message import Message, Provider, Role
from mchat.providers.base import BaseProvider


def make_fake_provider_class(pid: Provider):
    """Return a concrete BaseProvider subclass hardwired to the given Provider."""

    class _Fake(BaseProvider):
        def __init__(self, api_key: str = "fake", default_model: str = "fake-model"):
            super().__init__()
            self._default_model = default_model

        @property
        def provider_id(self) -> Provider:
            return pid

        @property
        def display_name(self) -> str:
            return pid.value

        def stream(self, messages, model=None):
            yield "ok"

        def list_models(self) -> list[str]:
            return ["fake-model-1", "fake-model-2"]

    _Fake.__name__ = f"Fake{pid.value.capitalize()}Provider"
    return _Fake


@pytest.fixture
def main_window(qtbot, tmp_path, monkeypatch):
    """Build a MainWindow wired against a tmp DB, tmp config, and fake
    providers. Every Provider gets a key so _init_providers wires them
    all up."""
    # Point Config + Database at tmp paths before importing main_window
    from mchat.ui import main_window as mw_mod

    # Patch every provider class used by _init_providers so nothing hits
    # a real API. Each fake is pre-bound to the Provider enum member it
    # stands in for so provider_id is correct.
    monkeypatch.setattr(mw_mod, "ClaudeProvider", make_fake_provider_class(Provider.CLAUDE))
    monkeypatch.setattr(mw_mod, "OpenAIProvider", make_fake_provider_class(Provider.OPENAI))
    monkeypatch.setattr(mw_mod, "GeminiProvider", make_fake_provider_class(Provider.GEMINI))
    monkeypatch.setattr(mw_mod, "PerplexityProvider", make_fake_provider_class(Provider.PERPLEXITY))
    monkeypatch.setattr(mw_mod, "MistralProvider", make_fake_provider_class(Provider.MISTRAL))

    cfg = Config(config_path=tmp_path / "cfg.json")
    # Populate fake keys so every provider is "configured"
    for k in ("anthropic_api_key", "openai_api_key", "gemini_api_key", "perplexity_api_key", "mistral_api_key"):
        cfg.set(k, "fake-key")
    cfg.save()

    db = Database(db_path=tmp_path / "test.db")

    # Patch PersonaDialog.exec so _on_new_chat's auto-open doesn't
    # block tests on a modal dialog.
    import mchat.ui.persona_dialog as pd_mod
    monkeypatch.setattr(pd_mod.PersonaDialog, "exec", lambda self: 0)

    from mchat.ui.main_window import MainWindow
    window = MainWindow(cfg, db)
    qtbot.addWidget(window)
    yield window
    # #129: stop any running TitleWorkers BEFORE closing the DB, so
    # they can't fire _on_title_ready against a closed DB and trigger
    # a "QThread: Destroyed while thread is still running" abort when
    # the parent MainWindow is garbage-collected.
    try:
        window._send.stop_all_title_workers()
    except Exception:
        pass
    db.close()


class TestComposition:
    def test_window_builds(self, main_window):
        """Every major sub-component is wired up and reachable."""
        assert main_window._chat is not None
        assert main_window._sidebar is not None
        assert main_window._input is not None
        assert main_window._provider_panel is not None
        assert main_window._matrix_panel is not None
        assert main_window._renderer is not None
        assert main_window._send is not None
        assert main_window._conv_mgr is not None
        assert main_window._prefs is not None
        assert main_window._router is not None

    def test_all_providers_wired_when_all_keys_present(self, main_window):
        """With fake keys for all providers, the router contains all of them."""
        assert set(main_window._router._providers.keys()) == set(Provider)

    def test_combos_and_checkboxes_built(self, main_window):
        """Toolbar rows are keyed by persona_id (string), not Provider.
        At startup with no conversation, the panel may be empty."""
        assert isinstance(main_window._combos, dict)
        assert isinstance(main_window._checkboxes, dict)
        assert isinstance(main_window._spend_labels, dict)

    def test_initial_conversation_created_or_loaded(self, main_window):
        """After startup, selecting a conversation must not blow up even
        when the DB is fresh (no prior conversations)."""
        # A fresh DB has no conversations; selecting in sidebar does nothing,
        # but the window should still be in a usable state.
        # Creating a new chat should populate _current_conv.
        main_window._on_new_chat()
        assert main_window._current_conv is not None


class TestSelectionSync:
    def test_checkbox_toggle_updates_selection(self, main_window):
        """Toggling a persona checkbox updates the SelectionState."""
        from mchat.models.persona import Persona, generate_persona_id
        from mchat.ui.persona_target import PersonaTarget
        main_window._on_new_chat()
        conv_id = main_window._current_conv.id
        p = Persona(
            conversation_id=conv_id, id=generate_persona_id(),
            provider=Provider.CLAUDE, name="Test", name_slug="test",
        )
        main_window._db.create_persona(p)
        main_window._sync_toolbar_personas()
        # Check the persona's checkbox
        cb = main_window._checkboxes[p.id]
        cb.setChecked(True)
        selection = main_window._selection_state.selection
        assert any(t.persona_id == p.id for t in selection)

    def test_empty_selection_allowed(self, main_window):
        """Stage 3A.4 — unchecking the last persona should be allowed."""
        from mchat.models.persona import Persona, generate_persona_id
        from mchat.ui.persona_target import PersonaTarget
        main_window._on_new_chat()
        conv_id = main_window._current_conv.id
        p = Persona(
            conversation_id=conv_id, id=generate_persona_id(),
            provider=Provider.CLAUDE, name="Test", name_slug="test",
        )
        main_window._db.create_persona(p)
        main_window._sync_toolbar_personas()
        target = PersonaTarget(persona_id=p.id, provider=Provider.CLAUDE)
        main_window._selection_state.set([target])
        main_window._sync_checkboxes_from_selection()
        main_window._checkboxes[p.id].setChecked(False)
        assert main_window._selection_state.selection == []


class TestCommandDrivenState:
    def test_toggle_column_mode_via_command(self, main_window):
        main_window._on_new_chat()
        initial = main_window._column_mode
        main_window._handle_command("//cols" if not initial else "//lines")
        assert main_window._column_mode != initial

    def test_limit_last_sets_limit_mark(self, main_window):
        main_window._on_new_chat()
        # Need at least one user message for //limit last to resolve
        main_window._db.add_message(Message(
            role=Role.USER, content="q", conversation_id=main_window._current_conv.id,
        ))
        main_window._current_conv.messages = main_window._db.get_messages(
            main_window._current_conv.id
        )
        main_window._handle_command("//limit last")
        assert main_window._current_conv.limit_mark is not None

    def test_limit_all_clears_limit_mark(self, main_window):
        main_window._on_new_chat()
        main_window._current_conv.limit_mark = "#1"
        main_window._handle_command("//limit ALL")
        assert main_window._current_conv.limit_mark is None

    def test_limit_uses_partial_update_not_full_rerender(
        self, main_window, monkeypatch,
    ):
        """#133 — //limit must call apply_excluded_indices on the chat
        widget (partial update) rather than _display_messages
        (full re-render) so it doesn't re-parse markdown or re-insert
        every message just to change background colours."""
        main_window._on_new_chat()
        # Need at least one user message for //limit last
        main_window._db.add_message(Message(
            role=Role.USER, content="q1",
            conversation_id=main_window._current_conv.id,
        ))
        main_window._db.add_message(Message(
            role=Role.ASSISTANT, content="a1",
            provider=Provider.CLAUDE,
            conversation_id=main_window._current_conv.id,
        ))
        main_window._current_conv.messages = main_window._db.get_messages(
            main_window._current_conv.id
        )
        main_window._display_messages(main_window._current_conv.messages)

        # Count calls
        display_calls = [0]
        apply_calls = [0]
        orig_display = main_window._display_messages
        orig_apply = main_window._chat.apply_excluded_indices

        def counting_display(msgs):
            display_calls[0] += 1
            return orig_display(msgs)

        def counting_apply(indices):
            apply_calls[0] += 1
            return orig_apply(indices)

        monkeypatch.setattr(main_window, "_display_messages", counting_display)
        monkeypatch.setattr(
            main_window._chat, "apply_excluded_indices", counting_apply,
        )

        main_window._handle_command("//limit last")

        assert apply_calls[0] >= 1, (
            "handle_limit must call apply_excluded_indices"
        )
        assert display_calls[0] == 0, (
            "handle_limit must NOT call _display_messages (full re-render)"
        )


class TestConversationSwitching:
    def test_new_chat_creates_conversation(self, main_window):
        before = len(main_window._db.list_conversations())
        main_window._on_new_chat()
        after = len(main_window._db.list_conversations())
        assert after == before + 1

    def test_switching_conversations_reloads_messages(self, main_window):
        # Create two conversations with different content
        conv1 = main_window._db.create_conversation("first")
        main_window._db.add_message(Message(
            role=Role.USER, content="one", conversation_id=conv1.id,
        ))
        conv2 = main_window._db.create_conversation("second")
        main_window._db.add_message(Message(
            role=Role.USER, content="two", conversation_id=conv2.id,
        ))

        main_window._on_conversation_selected(conv1.id)
        assert main_window._current_conv.id == conv1.id
        assert [m.content for m in main_window._current_conv.messages] == ["one"]

        main_window._on_conversation_selected(conv2.id)
        assert main_window._current_conv.id == conv2.id
        assert [m.content for m in main_window._current_conv.messages] == ["two"]

    def test_rename_updates_title_in_sidebar_and_db(self, main_window):
        main_window._on_new_chat()
        cid = main_window._current_conv.id
        main_window._on_rename_conversation(cid, "renamed title")
        # Sidebar item text updated
        found_label = None
        for i in range(main_window._sidebar._list.count()):
            item = main_window._sidebar._list.item(i)
            if item.data(1000) == cid or (
                hasattr(item, "data") and main_window._sidebar._conversations.get(cid)
            ):
                found_label = item.text()
                break
        assert found_label == "renamed title"
        # DB updated
        assert main_window._db.get_conversation(cid).title == "renamed title"


class TestEmptySelectionSend:
    """Stage 3A.4 — sending with zero targets must produce a clear
    user-facing message, not a silent no-op or crash."""

    def test_send_with_zero_targets_shows_note(self, main_window):
        """When the selection is empty and the user sends a message,
        a note should appear telling them to add a persona first."""
        main_window._on_new_chat()
        main_window._router.set_selection([])
        # Spy on chat notes
        notes = []
        original_add_note = main_window._chat.add_note
        main_window._chat.add_note = lambda msg: notes.append(msg)
        main_window._on_message_submitted("hello world")
        assert len(notes) >= 1
        assert any("persona" in n.lower() or "select" in n.lower() for n in notes)
        # No worker should have started
        assert main_window._send._multi_workers == {}
        # Restore
        main_window._chat.add_note = original_add_note

    def test_send_with_zero_targets_does_not_persist_message(self, main_window):
        """No user message should be persisted when the selection is empty."""
        main_window._on_new_chat()
        conv_id = main_window._current_conv.id
        main_window._router.set_selection([])
        main_window._on_message_submitted("hello world")
        msgs = main_window._db.get_messages(conv_id)
        assert len(msgs) == 0


class TestPrefixOnlySelection:
    """Regression tests for #60 — prefix-only provider inputs must be
    treated as selection changes, not as empty sends."""

    def test_bare_provider_prefix_changes_selection(self, main_window):
        """'gpt,' with no trailing text should select GPT and not start a send."""
        from mchat.models.message import Provider
        # Start with Claude selected
        main_window._router.set_selection([Provider.CLAUDE])
        # Submit prefix-only input
        main_window._on_message_submitted("gpt,")
        # Selection should have flipped to GPT
        assert main_window._router.selection == [Provider.OPENAI]
        # Critical: no worker should have been started. If the prefix-only
        # input had fallen through to the send path, _multi_workers would
        # contain at least the GPT worker (possibly finished and popped,
        # but the DB would have a user message).
        assert main_window._send._multi_workers == {}

    def test_all_prefix_with_no_personas_stays_unchanged(self, main_window):
        """Stage 4.4: all, with no explicit personas in the conversation
        returns empty — selection stays unchanged."""
        from mchat.models.message import Provider
        main_window._router.set_selection([Provider.CLAUDE])
        main_window._on_message_submitted("all,")
        # No personas in this conversation → all, resolves to empty →
        # selection stays as it was (prefix-only path doesn't change
        # selection when targets are empty)
        assert main_window._input.isEnabled() is True

    def test_prefix_only_does_not_save_user_message(self, main_window):
        """A prefix-only selection change must never be persisted as a
        user message — otherwise the conversation history fills up with
        empty or near-empty rows every time the user re-selects."""
        from mchat.models.message import Provider
        main_window._on_new_chat()
        conv_id = main_window._current_conv.id
        main_window._router.set_selection([Provider.CLAUDE])
        main_window._on_message_submitted("gpt,")
        msgs = main_window._db.get_messages(conv_id)
        assert all("gpt" not in (m.content or "").lower()[:10] for m in msgs), (
            "prefix-only input should not end up in DB as a user message"
        )

    def test_multi_prefix_only(self, main_window):
        from mchat.models.message import Provider
        main_window._router.set_selection([Provider.CLAUDE])
        main_window._on_message_submitted("gpt, gemini,")
        assert set(main_window._router.selection) == {
            Provider.OPENAI, Provider.GEMINI,
        }
        assert main_window._input.isEnabled() is True


class TestServicesContextStability:
    """Regression tests for #59 — long-lived controllers hold the
    ServicesContext by reference, so provider/settings rebuilds must
    update the context in place rather than replacing it."""

    def test_context_identity_stable_across_rebuild(self, main_window):
        """_rebuild_services must NOT reallocate the context."""
        before = main_window._services
        main_window._rebuild_services()
        assert main_window._services is before, (
            "ServicesContext identity changed — long-lived collaborators "
            "would now hold stale references"
        )

    def test_router_rebind_reaches_long_lived_collaborators(self, main_window):
        """Swap the router via the public path and verify every
        collaborator that cached self._services now sees it."""
        # Every collaborator should have been constructed with the
        # same context instance as MainWindow.
        assert main_window._send._services is main_window._services
        assert main_window._conv_mgr._services is main_window._services
        assert main_window._prefs._services is main_window._services
        assert main_window._settings_applier._services is main_window._services

        # Simulate the post-settings path: swap the router, rebuild.
        original_router = main_window._router
        assert original_router is not None
        # Create a new Router with the same providers — new object identity.
        from mchat.router import Router
        new_router = Router(
            dict(original_router._providers),
            default=list(original_router._providers.keys())[0],
            selection_state=main_window._selection_state,
        )
        main_window._router = new_router
        main_window._rebuild_services()

        # Every long-lived collaborator sees the new router through
        # its cached services reference.
        assert main_window._services.router is new_router
        assert main_window._send._services.router is new_router
        assert main_window._conv_mgr._services.router is new_router


class TestStateObjectsWired:
    def test_session_reflects_current_conv(self, main_window):
        """_current_conv is a property backed by ConversationSession."""
        main_window._on_new_chat()
        assert main_window._current_conv is main_window._session.current

    def test_session_writes_propagate_via_setter(self, main_window):
        """Setting _current_conv drives session.set_current / clear."""
        main_window._on_new_chat()
        assert main_window._session.is_active()
        main_window._current_conv = None
        assert main_window._session.is_active() is False

    def test_selection_state_is_the_router_source_of_truth(self, main_window):
        """Router.selection reads through SelectionState.providers_only()
        — state now holds list[PersonaTarget] (Stage 2.4), Router
        preserves its list[Provider] interface via the dedup wrapper.
        """
        from mchat.models.message import Provider
        from mchat.ui.persona_target import synthetic_default
        main_window._selection_state.set([
            synthetic_default(Provider.OPENAI),
            synthetic_default(Provider.GEMINI),
        ])
        assert main_window._router.selection == [Provider.OPENAI, Provider.GEMINI]

    def test_router_set_selection_writes_to_state(self, main_window):
        """Router.set_selection takes list[Provider], but the underlying
        state stores PersonaTargets. Router wraps via synthetic_default
        when writing."""
        from mchat.models.message import Provider
        from mchat.ui.persona_target import synthetic_default
        main_window._router.set_selection([Provider.CLAUDE])
        assert main_window._selection_state.selection == [
            synthetic_default(Provider.CLAUDE)
        ]

    def test_model_catalog_exists(self, main_window):
        """The model catalog object should exist after construction."""
        assert main_window._model_catalog is not None


class TestLayoutPersistence:
    def test_column_mode_persisted_to_config(self, main_window):
        initial = main_window._column_mode
        main_window._toggle_column_mode()
        assert main_window._config.get("column_mode") == (not initial)


class TestSidebarPersonasAction:
    """Stage 3A.3 — the sidebar's 'Personas...' context-menu action
    fires the personas_requested signal, which MainWindow handles
    by opening a PersonaDialog."""

    def test_sidebar_exposes_personas_requested_signal(self, main_window):
        """The signal exists and is connected to MainWindow's handler."""
        # Signal attribute exists
        assert hasattr(main_window._sidebar, "personas_requested")
        # MainWindow has a handler method for it
        assert hasattr(main_window, "_on_personas_requested")

    def test_personas_requested_signal_opens_dialog(self, main_window, monkeypatch):
        """Emitting the signal results in PersonaDialog being opened
        against the current DB + Config + conversation id. We patch
        exec() so the test doesn't block on a modal."""
        main_window._on_new_chat()
        conv_id = main_window._current_conv.id

        # Spy on PersonaDialog construction + exec
        constructed = []
        execed = []

        import mchat.ui.persona_dialog as pd_mod
        original = pd_mod.PersonaDialog

        class SpyDialog(original):
            def __init__(self, db, config, conv_id_arg, parent=None, **kwargs):
                constructed.append((db, config, conv_id_arg))
                super().__init__(db, config, conv_id_arg, parent=parent, **kwargs)

            def exec(self):
                execed.append(True)
                return 0  # don't actually show

        monkeypatch.setattr(pd_mod, "PersonaDialog", SpyDialog)

        # Emit the signal — same as right-click → Personas...
        main_window._sidebar.personas_requested.emit(conv_id)

        assert len(constructed) == 1
        assert constructed[0][2] == conv_id
        assert len(execed) == 1


class TestSendControllerPersonas:
    """Stage 2.6 — send_controller threads PersonaTargets through the
    send/retry flow. Sends produce persisted messages tagged with
    persona_id + provider; model selection honours persona.model_override.
    """

    def _make_persona(self, db, conv_id, name="Evaluator", slug="evaluator",
                      system_prompt_override=None, model_override=None):
        from mchat.models.persona import Persona, generate_persona_id
        p = Persona(
            conversation_id=conv_id,
            id=generate_persona_id(),
            provider=Provider.CLAUDE,
            name=name,
            name_slug=slug,
            system_prompt_override=system_prompt_override,
            model_override=model_override,
        )
        return db.create_persona(p)

    def _send_and_wait(self, main_window, qtbot, text: str):
        """Submit a message and let the fake worker's 'ok' yield make
        it through the Qt event loop. The send runs on a QThread so we
        wait until all multi_workers have drained — that's the real
        "send is done" signal (input-enabled flips true at the same
        moment but also starts as true, so we can't just poll that).
        """
        main_window._on_new_chat() if not main_window._current_conv else None
        main_window._on_message_submitted(text)
        # After submit, workers should be running. Wait for them to
        # drain back to empty.
        qtbot.waitUntil(
            lambda: len(main_window._send._multi_workers) == 0,
            timeout=3000,
        )

    def test_send_to_synthetic_default_works_like_today(
        self, main_window, qtbot,
    ):
        """Baseline: sending with a provider shorthand produces a
        persisted assistant message with persona_id == provider.value
        (the synthetic default exception, D1). This is the regression
        guarantee that legacy chats still behave identically."""
        main_window._on_new_chat()
        conv_id = main_window._current_conv.id
        self._send_and_wait(main_window, qtbot, "claude, hello")

        msgs = main_window._db.get_messages(conv_id)
        # First is user, second+ are assistant replies
        assistants = [m for m in msgs if m.role == Role.ASSISTANT]
        assert len(assistants) >= 1
        assert assistants[0].persona_id == "claude"
        assert assistants[0].provider == Provider.CLAUDE

    def test_send_to_explicit_persona_tags_message_with_persona_id(
        self, main_window, qtbot,
    ):
        """A send addressed to an explicit persona produces a persisted
        assistant message with persona_id == that persona's opaque id."""
        main_window._on_new_chat()
        conv_id = main_window._current_conv.id
        partner = self._make_persona(
            main_window._db, conv_id, name="Partner", slug="partner",
        )
        self._send_and_wait(main_window, qtbot, "partner, Ciao!")

        assistants = [
            m for m in main_window._db.get_messages(conv_id)
            if m.role == Role.ASSISTANT
        ]
        assert len(assistants) >= 1
        assert assistants[0].persona_id == partner.id
        assert assistants[0].provider == Provider.CLAUDE

    def test_persona_model_override_is_used_at_send_time(
        self, main_window, qtbot,
    ):
        """D6: resolve_persona_model is called in the send path, so a
        persona with model_override set sends with that model even
        though the global Claude model differs."""
        main_window._on_new_chat()
        conv_id = main_window._current_conv.id
        # Global default model is fake-model (from FakeProvider); set
        # an explicit override on the persona to something different.
        self._make_persona(
            main_window._db, conv_id,
            name="Translator", slug="translator",
            model_override="claude-haiku-override",
        )
        self._send_and_wait(main_window, qtbot, "translator, word")

        assistants = [
            m for m in main_window._db.get_messages(conv_id)
            if m.role == Role.ASSISTANT
        ]
        assert len(assistants) >= 1
        assert assistants[0].model == "claude-haiku-override"

    def test_persona_with_none_model_override_uses_config_model(
        self, main_window, qtbot,
    ):
        """A persona with model_override=None inherits the global
        provider model from config at send time."""
        main_window._on_new_chat()
        conv_id = main_window._current_conv.id
        main_window._config.set("claude_model", "global-sonnet")
        main_window._config.save()
        self._make_persona(
            main_window._db, conv_id,
            name="Partner", slug="partner",
            model_override=None,
        )
        self._send_and_wait(main_window, qtbot, "partner, hi")

        assistants = [
            m for m in main_window._db.get_messages(conv_id)
            if m.role == Role.ASSISTANT
        ]
        assert len(assistants) >= 1
        assert assistants[0].model == "global-sonnet"

    def test_two_same_provider_personas_send_independently(
        self, main_window, qtbot,
    ):
        """The killer use case: partner and evaluator, both on Claude,
        addressed in a single multi-prefix send. Two assistant messages
        should land — one per persona — with distinct persona_ids, and
        the workers must not clobber each other's transient state."""
        main_window._on_new_chat()
        conv_id = main_window._current_conv.id
        partner = self._make_persona(
            main_window._db, conv_id, name="Partner", slug="partner",
        )
        evaluator = self._make_persona(
            main_window._db, conv_id, name="Evaluator", slug="evaluator",
        )
        self._send_and_wait(main_window, qtbot, "partner, evaluator, go")

        assistants = [
            m for m in main_window._db.get_messages(conv_id)
            if m.role == Role.ASSISTANT
        ]
        persona_ids = {m.persona_id for m in assistants}
        assert partner.id in persona_ids
        assert evaluator.id in persona_ids
        # At least two distinct assistant rows
        assert len({m.persona_id for m in assistants}) == 2

    def test_prefix_only_input_still_short_circuits(
        self, main_window, qtbot,
    ):
        """Regression guard for #60: prefix-only input with PersonaResolver
        in the chain still doesn't start a worker."""
        from mchat.models.message import Provider
        main_window._on_new_chat()
        main_window._router.set_selection([Provider.CLAUDE])
        main_window._on_message_submitted("gpt,")
        # No worker should have started
        assert main_window._send._multi_workers == {}
        assert main_window._router.selection == [Provider.OPENAI]


class TestPersonaSelectionAdjust:
    """#85 — +/- selection adjust should resolve persona names, not
    just provider shorthands."""

    def _make_persona(self, db, conv_id, name, slug, provider=Provider.CLAUDE):
        from mchat.models.persona import Persona, generate_persona_id
        p = Persona(
            conversation_id=conv_id,
            id=generate_persona_id(),
            provider=provider,
            name=name,
            name_slug=slug,
        )
        return db.create_persona(p)

    def test_plus_persona_name_adds_to_selection(self, main_window):
        main_window._on_new_chat()
        conv_id = main_window._current_conv.id
        partner = self._make_persona(
            main_window._db, conv_id, "Partner", "partner",
        )
        # Start with empty selection
        main_window._selection_state.set([])
        handled = main_window._handle_selection_adjust("+partner")
        assert handled is True
        selection = main_window._selection_state.selection
        assert any(t.persona_id == partner.id for t in selection)

    def test_minus_persona_name_removes_from_selection(self, main_window):
        from mchat.ui.persona_target import PersonaTarget
        main_window._on_new_chat()
        conv_id = main_window._current_conv.id
        partner = self._make_persona(
            main_window._db, conv_id, "Partner", "partner",
        )
        evaluator = self._make_persona(
            main_window._db, conv_id, "Evaluator", "evaluator",
        )
        # Select both
        main_window._selection_state.set([
            PersonaTarget(persona_id=partner.id, provider=Provider.CLAUDE),
            PersonaTarget(persona_id=evaluator.id, provider=Provider.CLAUDE),
        ])
        handled = main_window._handle_selection_adjust("-partner")
        assert handled is True
        selection = main_window._selection_state.selection
        assert not any(t.persona_id == partner.id for t in selection)
        assert any(t.persona_id == evaluator.id for t in selection)

    def test_plus_provider_adds_synthetic_default_only(self, main_window):
        """'+claude' should add the synthetic default, not expand to
        explicit personas. Personas are addressed by name."""
        from mchat.ui.persona_target import synthetic_default
        main_window._on_new_chat()
        conv_id = main_window._current_conv.id
        self._make_persona(
            main_window._db, conv_id, "Partner", "partner",
        )
        main_window._selection_state.set([])
        handled = main_window._handle_selection_adjust("+claude")
        assert handled is True
        selection = main_window._selection_state.selection
        assert len(selection) == 1
        assert selection[0] == synthetic_default(Provider.CLAUDE)


class TestSequentialMode:
    """#115 — //mode sequential sends personas one at a time."""

    def test_mode_command_sets_flag(self, main_window):
        main_window._on_message_submitted("//mode sequential")
        assert main_window._send._sequential_mode is True
        main_window._on_message_submitted("//mode parallel")
        assert main_window._send._sequential_mode is False

    def test_sequential_sends_one_at_a_time(self, main_window, qtbot):
        """In sequential mode, only one worker runs at a time."""
        from mchat.models.persona import Persona, generate_persona_id
        from mchat.ui.persona_target import PersonaTarget
        main_window._on_new_chat()
        conv_id = main_window._current_conv.id
        p1 = Persona(
            conversation_id=conv_id, id=generate_persona_id(),
            provider=Provider.CLAUDE, name="A", name_slug="a",
        )
        p2 = Persona(
            conversation_id=conv_id, id=generate_persona_id(),
            provider=Provider.OPENAI, name="B", name_slug="b",
        )
        main_window._db.create_persona(p1)
        main_window._db.create_persona(p2)
        main_window._send._sequential_mode = True
        t1 = PersonaTarget(persona_id=p1.id, provider=Provider.CLAUDE)
        t2 = PersonaTarget(persona_id=p2.id, provider=Provider.OPENAI)
        main_window._selection_state.set([t1, t2])
        main_window._on_message_submitted("hello")
        # Only one worker should be running at a time
        assert len(main_window._send._multi_workers) <= 1
        # Wait for workers to finish to avoid teardown errors
        qtbot.waitUntil(
            lambda: len(main_window._send._multi_workers) == 0,
            timeout=5000,
        )


class TestNewChatOpensPersonaDialog:
    """#93 — new chat should auto-open PersonaDialog."""

    def test_new_chat_opens_persona_dialog(self, main_window, monkeypatch):
        """_on_new_chat should open PersonaDialog after creating
        the conversation."""
        constructed = []

        import mchat.ui.persona_dialog as pd_mod
        original = pd_mod.PersonaDialog

        class SpyDialog(original):
            def __init__(self, db, config, conv_id_arg, parent=None, **kwargs):
                constructed.append(conv_id_arg)
                super().__init__(db, config, conv_id_arg, parent=parent, **kwargs)

            def exec(self):
                return 0  # don't block

        monkeypatch.setattr(pd_mod, "PersonaDialog", SpyDialog)
        main_window._on_new_chat()
        assert len(constructed) == 1
        assert constructed[0] == main_window._current_conv.id

    def test_closing_dialog_without_persona_leaves_empty_chat(
        self, main_window, monkeypatch,
    ):
        """Closing the dialog without creating a persona should leave
        the chat in the empty state (no crash)."""
        import mchat.ui.persona_dialog as pd_mod
        original = pd_mod.PersonaDialog

        class NoOpDialog(original):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)

            def exec(self):
                return 0  # close without creating anything

        monkeypatch.setattr(pd_mod, "PersonaDialog", NoOpDialog)
        main_window._on_new_chat()
        # Should have a conversation but no personas
        assert main_window._current_conv is not None
        personas = main_window._db.list_personas(main_window._current_conv.id)
        assert len(personas) == 0


class TestDialogCreatedPersonasPinsAndSelection:
    """#93 follow-up — personas created via the PersonaDialog should
    get the same pinned instructions and selection updates as the
    command-line path."""

    def test_dialog_created_persona_gets_pins(self, main_window, monkeypatch):
        """After closing the PersonaDialog with a newly created persona,
        pinned name instruction + setup note should exist."""
        main_window._on_new_chat()
        conv_id = main_window._current_conv.id

        import mchat.ui.persona_dialog as pd_mod
        original = pd_mod.PersonaDialog

        class AutoCreateDialog(original):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)

            def exec(self):
                self.create_persona(
                    provider=Provider.CLAUDE,
                    name="Partner",
                    system_prompt_override="Be kind",
                )
                return 1

        monkeypatch.setattr(pd_mod, "PersonaDialog", AutoCreateDialog)
        main_window._on_personas_requested(conv_id)

        messages = main_window._db.get_messages(conv_id)
        pinned = [m for m in messages if m.pinned]
        assert len(pinned) >= 2
        assert any("use Partner as your name" in m.content for m in pinned)
        assert any("Added persona" in m.content and "Partner" in m.content for m in pinned)

    def test_dialog_created_persona_added_to_selection(self, main_window, monkeypatch):
        """After closing the PersonaDialog with a new persona, the
        persona should be in the selection."""
        main_window._on_new_chat()
        conv_id = main_window._current_conv.id

        import mchat.ui.persona_dialog as pd_mod
        original = pd_mod.PersonaDialog

        class AutoCreateDialog(original):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)

            def exec(self):
                self.create_persona(
                    provider=Provider.OPENAI,
                    name="Checker",
                )
                return 1

        monkeypatch.setattr(pd_mod, "PersonaDialog", AutoCreateDialog)
        main_window._on_personas_requested(conv_id)

        selection = main_window._selection_state.selection
        providers = {t.provider for t in selection}
        assert Provider.OPENAI in providers


    def test_existing_persona_missing_pins_gets_backfilled(
        self, main_window, monkeypatch,
    ):
        """Personas created before the pin code existed should get
        their pins backfilled when the dialog is opened."""
        from mchat.models.persona import Persona, generate_persona_id
        main_window._on_new_chat()
        conv_id = main_window._current_conv.id

        # Directly insert a persona via DB (simulating pre-existing persona
        # that was created before pin logic existed — no pins)
        p = Persona(
            conversation_id=conv_id,
            id=generate_persona_id(),
            provider=Provider.CLAUDE,
            name="OldPartner",
            name_slug="oldpartner",
            system_prompt_override="Be helpful",
        )
        main_window._db.create_persona(p)

        # No pins exist yet
        msgs = main_window._db.get_messages(conv_id)
        assert not any(m.pinned for m in msgs)

        # Open the dialog (no-op, just closes) — should backfill pins
        import mchat.ui.persona_dialog as pd_mod
        monkeypatch.setattr(pd_mod.PersonaDialog, "exec", lambda self: 0)
        main_window._on_personas_requested(conv_id)

        msgs = main_window._db.get_messages(conv_id)
        pinned = [m for m in msgs if m.pinned]
        assert len(pinned) >= 2
        assert any("use OldPartner as your name" in m.content for m in pinned)
        assert any("Added persona" in m.content and "OldPartner" in m.content
                    for m in pinned)


class TestPersonasButton:
    """#93 follow-up — a Personas button in the bar opens the dialog."""

    def test_personas_button_exists(self, main_window):
        assert hasattr(main_window, "_personas_btn")
        assert main_window._personas_btn is not None


class TestUnknownCommandHandling:
    """#92 — unknown // commands and single-/ typos should show an error,
    not be sent to a provider."""

    def test_unknown_command_shows_error(self, main_window):
        """//unknowncmd should produce an error note."""
        main_window._on_new_chat()
        notes = []
        original = main_window._chat.add_note
        main_window._chat.add_note = lambda msg: notes.append(msg)
        main_window._on_message_submitted("//unknowncmd hello")
        assert any("unknown" in n.lower() for n in notes)
        # No worker should have started
        assert main_window._send._multi_workers == {}
        main_window._chat.add_note = original

    def test_unknown_command_does_not_persist_message(self, main_window):
        """An unrecognized // command must not be saved as a user message."""
        main_window._on_new_chat()
        conv_id = main_window._current_conv.id
        main_window._on_message_submitted("//notacommand")
        msgs = main_window._db.get_messages(conv_id)
        assert len(msgs) == 0

    def test_single_slash_command_shows_hint(self, main_window):
        """/addpersona (single slash) should show a hint about //."""
        main_window._on_new_chat()
        notes = []
        original = main_window._chat.add_note
        main_window._chat.add_note = lambda msg: notes.append(msg)
        main_window._on_message_submitted("/addpersona")
        assert any("//" in n for n in notes)
        assert main_window._send._multi_workers == {}
        main_window._chat.add_note = original


class TestSyntheticDefaultEviction:
    """#121 — _ensure_persona_pins must evict synthetic defaults when
    explicit personas exist for the same provider, so the selection
    never contains both ('claude', 'claude') and ('p_xxx', 'claude').
    """

    def test_ensure_pins_evicts_synthetic_default_for_same_provider(
        self, main_window,
    ):
        """After _ensure_persona_pins, a synthetic default must be replaced
        by the explicit persona for the same provider."""
        from mchat.models.persona import Persona, generate_persona_id
        from mchat.ui.persona_target import PersonaTarget, synthetic_default

        main_window._on_new_chat()
        conv_id = main_window._current_conv.id

        # Pre-seed selection with a synthetic Claude default
        main_window._selection_state.set([synthetic_default(Provider.CLAUDE)])

        # Create an explicit Claude persona in the DB
        pid = generate_persona_id()
        main_window._db.create_persona(Persona(
            conversation_id=conv_id, id=pid,
            provider=Provider.CLAUDE, name="ClaudeBot", name_slug="claudebot",
        ))

        main_window._ensure_persona_pins(conv_id)

        sel = main_window._selection_state.selection
        persona_ids = [t.persona_id for t in sel]
        # The synthetic default must be gone
        assert "claude" not in persona_ids, (
            "synthetic default ('claude', 'claude') should be evicted"
        )
        # The explicit persona must be present
        assert pid in persona_ids

    def test_ensure_pins_keeps_synthetic_default_when_no_explicit_persona(
        self, main_window,
    ):
        """If there's no explicit persona for a provider, the synthetic
        default should remain in the selection."""
        from mchat.ui.persona_target import synthetic_default

        main_window._on_new_chat()
        conv_id = main_window._current_conv.id

        # Pre-seed selection with a synthetic Claude default
        main_window._selection_state.set([synthetic_default(Provider.CLAUDE)])

        # No personas in DB — _ensure_persona_pins should be a no-op
        main_window._ensure_persona_pins(conv_id)

        sel = main_window._selection_state.selection
        persona_ids = [t.persona_id for t in sel]
        assert "claude" in persona_ids

    def test_ensure_pins_evicts_only_matching_provider(self, main_window):
        """Synthetic defaults for providers WITHOUT an explicit persona must
        survive even when other providers get evicted."""
        from mchat.models.persona import Persona, generate_persona_id
        from mchat.ui.persona_target import PersonaTarget, synthetic_default

        main_window._on_new_chat()
        conv_id = main_window._current_conv.id

        # Selection: synthetic Claude + synthetic OpenAI
        main_window._selection_state.set([
            synthetic_default(Provider.CLAUDE),
            synthetic_default(Provider.OPENAI),
        ])

        # Only create an explicit Claude persona — not OpenAI
        pid = generate_persona_id()
        main_window._db.create_persona(Persona(
            conversation_id=conv_id, id=pid,
            provider=Provider.CLAUDE, name="ClaudeBot", name_slug="claudebot",
        ))

        main_window._ensure_persona_pins(conv_id)

        sel = main_window._selection_state.selection
        persona_ids = [t.persona_id for t in sel]
        # Synthetic Claude gone, replaced by explicit
        assert "claude" not in persona_ids
        assert pid in persona_ids
        # Synthetic OpenAI untouched
        assert "openai" in persona_ids

    def test_save_selection_excludes_synthetic_when_explicit_exists(
        self, main_window,
    ):
        """save_selection must not persist synthetic defaults when an
        explicit persona for the same provider is in the selection."""
        from mchat.models.persona import Persona, generate_persona_id
        from mchat.ui.persona_target import PersonaTarget, synthetic_default

        main_window._on_new_chat()
        conv_id = main_window._current_conv.id

        pid = generate_persona_id()
        main_window._db.create_persona(Persona(
            conversation_id=conv_id, id=pid,
            provider=Provider.CLAUDE, name="ClaudeBot", name_slug="claudebot",
        ))

        # Simulate the buggy state: both synthetic + explicit in selection
        main_window._selection_state.set([
            synthetic_default(Provider.CLAUDE),
            PersonaTarget(persona_id=pid, provider=Provider.CLAUDE),
        ])

        main_window._conv_mgr.save_selection()

        # Read back from DB
        conv = main_window._db.get_conversation(conv_id)
        tokens = conv.last_provider.split(",")
        # Synthetic default 'claude' must NOT be in the persisted string
        assert "claude" not in tokens, (
            "save_selection should filter out synthetic defaults when "
            "explicit personas for the same provider exist"
        )
        assert pid in tokens

    def test_ensure_pins_evicts_stale_persona_ids(self, main_window):
        """#121b — stale persona_ids from a previous import must be
        evicted when _ensure_persona_pins runs with a fresh persona set."""
        from mchat.models.persona import Persona, generate_persona_id
        from mchat.ui.persona_target import PersonaTarget

        main_window._on_new_chat()
        conv_id = main_window._current_conv.id

        # Simulate stale persona_ids in selection (from a previous session)
        stale_pid = generate_persona_id()  # not in DB
        main_window._selection_state.set([
            PersonaTarget(persona_id=stale_pid, provider=Provider.CLAUDE),
        ])

        # Create a fresh persona for this conversation
        new_pid = generate_persona_id()
        main_window._db.create_persona(Persona(
            conversation_id=conv_id, id=new_pid,
            provider=Provider.CLAUDE, name="ClaudeBot", name_slug="claudebot",
        ))

        main_window._ensure_persona_pins(conv_id)

        sel = main_window._selection_state.selection
        persona_ids = [t.persona_id for t in sel]
        assert stale_pid not in persona_ids, (
            "stale persona_id from a previous import should be evicted"
        )
        assert new_pid in persona_ids

    def test_conversation_switch_clears_selection_when_no_last_provider(
        self, main_window,
    ):
        """#121b — switching to a conversation with no last_provider must
        clear the selection so stale targets from the previous conv don't
        leak through."""
        from mchat.models.persona import Persona, generate_persona_id
        from mchat.ui.persona_target import PersonaTarget

        # Create conv A with a persona and set it in selection
        main_window._on_new_chat()
        conv_a = main_window._current_conv.id
        pid = generate_persona_id()
        main_window._db.create_persona(Persona(
            conversation_id=conv_a, id=pid,
            provider=Provider.CLAUDE, name="BotA", name_slug="bota",
        ))
        main_window._selection_state.set([
            PersonaTarget(persona_id=pid, provider=Provider.CLAUDE),
        ])

        # Create conv B — no personas, no last_provider
        main_window._on_new_chat()
        conv_b = main_window._current_conv.id

        # Switch to conv B
        main_window._conv_mgr.on_conversation_selected(conv_b)

        sel = main_window._selection_state.selection
        persona_ids = [t.persona_id for t in sel]
        # Conv A's persona must NOT leak into conv B's selection
        assert pid not in persona_ids, (
            "persona from conv A should not leak into conv B's selection"
        )


class TestAutoTitleFirstMessage:
    """#123 — auto-title must trigger on the first user prompt even when
    the conversation already has pinned persona setup messages."""

    def test_first_user_message_sets_title_with_pinned_messages_present(
        self, qtbot, main_window,
    ):
        """A chat with pre-existing pinned messages (persona name+setup)
        must still get its title set from the first real user prompt."""
        from mchat.models.persona import Persona, generate_persona_id
        from mchat.ui.persona_target import PersonaTarget

        main_window._on_new_chat()
        conv_id = main_window._current_conv.id

        # Create a persona — _ensure_persona_pins will add 2 pinned msgs
        pid = generate_persona_id()
        main_window._db.create_persona(Persona(
            conversation_id=conv_id, id=pid,
            provider=Provider.CLAUDE, name="Bot", name_slug="bot",
        ))
        main_window._ensure_persona_pins(conv_id)
        main_window._selection_state.set([
            PersonaTarget(persona_id=pid, provider=Provider.CLAUDE),
        ])

        # Title is still default
        assert main_window._db.get_conversation(conv_id).title == "New Chat"
        # And conv.messages is NOT length 1 — pins are already there
        assert len(main_window._current_conv.messages) > 1

        main_window._on_message_submitted("explain quicksort to me")

        # Wait for the send to complete
        qtbot.waitUntil(
            lambda: len(main_window._send._multi_workers) == 0,
            timeout=5000,
        )

        title = main_window._db.get_conversation(conv_id).title
        assert title != "New Chat", "title should be auto-set from first prompt"
        assert "quicksort" in title


class TestSendModeResetOnNewChat:
    """#124 — //mode parallel/sequential should not leak across chats.
    New chats must always start in parallel mode."""

    def test_new_chat_resets_mode_to_parallel(self, main_window):
        """After flipping to sequential, creating a new chat must reset
        to parallel."""
        main_window._on_new_chat()
        # Flip to sequential
        main_window._send._sequential_mode = True

        # Create a new chat
        main_window._on_new_chat()

        assert main_window._send._sequential_mode is False, (
            "new chat must start in parallel mode"
        )

    def test_switching_chats_restores_each_chats_mode(self, main_window):
        """If chat A is sequential and chat B is parallel, switching
        between them must restore each chat's own mode."""
        from mchat.ui.commands.selection import handle_mode

        main_window._on_new_chat()
        conv_a = main_window._current_conv.id
        # Set chat A to sequential via the //mode command (so persistence
        # path is exercised)
        host = main_window
        host._send._sequential_mode = False
        host._db = main_window._db  # ensure db attr matches
        handle_mode("sequential", host)
        assert main_window._send._sequential_mode is True

        # Create chat B (which should default to parallel)
        main_window._on_new_chat()
        conv_b = main_window._current_conv.id
        assert main_window._send._sequential_mode is False

        # Switch back to A — should restore sequential
        main_window._conv_mgr.on_conversation_selected(conv_a)
        assert main_window._send._sequential_mode is True, (
            "switching back to chat A should restore sequential mode"
        )

        # Switch to B — should restore parallel
        main_window._conv_mgr.on_conversation_selected(conv_b)
        assert main_window._send._sequential_mode is False


class TestLLMAutoTitle:
    """#125 — after the first user→assistant exchange, fire a one-shot
    background title-generation request to the first persona, and apply
    the result if the title is still 'New Chat'.
    """

    def test_title_worker_truncates_to_25_chars(self):
        """The TitleWorker post-processor must clean up the LLM response:
        strip quotes, trim whitespace, take first line, hard-truncate."""
        from mchat.workers.title_worker import clean_title
        assert clean_title('"sorting algorithms"') == "sorting algorithms"
        assert clean_title("Sorting Algorithms\nMore stuff") == "Sorting Algorithms"
        assert clean_title("a" * 100) == "a" * 25
        assert clean_title("   spaced   ") == "spaced"
        assert clean_title("'single quotes'") == "single quotes"
        assert clean_title("ends with period.") == "ends with period"
        assert clean_title("") == ""

    def test_title_applied_only_if_default(self, main_window):
        """The post-LLM apply step must skip if the user already
        renamed the conversation in the meantime."""
        from mchat.ui.send_controller import SendController

        main_window._on_new_chat()
        conv_id = main_window._current_conv.id
        main_window._db.update_conversation_title(conv_id, "user-renamed")

        # Apply a new auto-title — should be a no-op
        send: SendController = main_window._send
        send._apply_auto_title(conv_id, "auto title")

        title = main_window._db.get_conversation(conv_id).title
        assert title == "user-renamed", (
            "auto-title must not overwrite a user-set title"
        )

    def test_title_applied_when_still_default(self, main_window):
        """If the title is still 'New Chat', _apply_auto_title sets it."""
        from mchat.ui.send_controller import SendController

        main_window._on_new_chat()
        conv_id = main_window._current_conv.id
        send: SendController = main_window._send
        send._apply_auto_title(conv_id, "quicksort talk")

        title = main_window._db.get_conversation(conv_id).title
        assert title == "quicksort talk"

    def test_title_not_re_triggered_after_set(self, main_window):
        """Once a conversation has had its title generated, the trigger
        must not fire again on subsequent sends."""
        from mchat.ui.send_controller import SendController

        main_window._on_new_chat()
        conv_id = main_window._current_conv.id
        send: SendController = main_window._send

        # Mark as already-attempted
        send._title_generation_attempted.add(conv_id)
        assert not send._should_generate_title(conv_id)

    def test_should_generate_title_default_only(self, main_window):
        """_should_generate_title returns True only when title is default
        and we haven't already tried."""
        from mchat.ui.send_controller import SendController

        main_window._on_new_chat()
        conv_id = main_window._current_conv.id
        send: SendController = main_window._send

        assert send._should_generate_title(conv_id)
        # After rename, no
        main_window._db.update_conversation_title(conv_id, "renamed")
        assert not send._should_generate_title(conv_id)


class TestPopRestoresInputText:
    """#127 — //pop should put the removed user message's text back into
    the input box so the user can edit and resend it."""

    def test_pop_restores_last_user_text_into_input(self, qtbot, main_window):
        from PySide6.QtCore import QCoreApplication

        main_window._on_new_chat()
        conv_id = main_window._current_conv.id

        # Seed a user message + assistant response directly in the DB
        user_msg = Message(
            role=Role.USER,
            content="original pop-me text",
            conversation_id=conv_id,
        )
        main_window._db.add_message(user_msg)
        main_window._current_conv.messages.append(user_msg)
        asst_msg = Message(
            role=Role.ASSISTANT,
            content="some response",
            provider=Provider.CLAUDE,
            conversation_id=conv_id,
        )
        main_window._db.add_message(asst_msg)
        main_window._current_conv.messages.append(asst_msg)

        # Fire //pop
        main_window._on_message_submitted("//pop")

        # Flush the QTimer.singleShot that schedules the input restore
        QCoreApplication.processEvents()

        text = main_window._input._text_edit.toPlainText()
        assert text == "original pop-me text"

    def test_pop_with_nothing_leaves_input_unchanged(self, main_window):
        """//pop on an empty conversation should not clear or overwrite
        the existing input text."""
        from PySide6.QtCore import QCoreApplication

        main_window._on_new_chat()
        main_window._input._text_edit.setPlainText("don't touch me")

        main_window._on_message_submitted("//pop")
        QCoreApplication.processEvents()

        assert main_window._input._text_edit.toPlainText() == "don't touch me"


class TestInputDraftPerConversation:
    """#128 — each conversation should have its own input draft. Switching
    chats must save the outgoing draft and restore the incoming one."""

    def test_draft_saved_and_restored_on_switch(self, main_window):
        """Typing in chat A, switching to B, then back to A restores A's draft."""
        main_window._on_new_chat()
        conv_a = main_window._current_conv.id
        main_window._input._text_edit.setPlainText("partial thought in A")

        main_window._on_new_chat()
        conv_b = main_window._current_conv.id

        # Switching to B: A's draft is saved, B starts empty
        assert main_window._input._text_edit.toPlainText() == ""

        # Type in B
        main_window._input._text_edit.setPlainText("different thought in B")

        # Switch back to A
        main_window._conv_mgr.on_conversation_selected(conv_a)
        assert main_window._input._text_edit.toPlainText() == "partial thought in A"

        # Switch to B again
        main_window._conv_mgr.on_conversation_selected(conv_b)
        assert main_window._input._text_edit.toPlainText() == "different thought in B"

    def test_new_chat_starts_with_empty_input(self, main_window):
        """A brand-new conversation must always start with an empty input,
        even if the previous chat had a draft."""
        main_window._on_new_chat()
        main_window._input._text_edit.setPlainText("stale from before")

        main_window._on_new_chat()
        assert main_window._input._text_edit.toPlainText() == ""


class TestTitleWorkerRobustness:
    """#129 — TitleWorker callbacks must never crash the app. Background
    nicety; any exception (closed DB, gone widget, etc.) must be caught."""

    def test_on_title_ready_swallows_closed_db(self, main_window):
        """If the DB has been closed by the time the worker emits, the
        callback must swallow the exception silently."""
        from mchat.ui.send_controller import SendController
        main_window._on_new_chat()
        conv_id = main_window._current_conv.id
        send: SendController = main_window._send

        # Close the DB out from under the worker
        main_window._db.close()

        # Must NOT raise
        send._on_title_ready(conv_id, "some title")

    def test_on_title_failed_swallows_closed_db(self, main_window):
        """Same contract for the failed callback."""
        from mchat.ui.send_controller import SendController
        main_window._on_new_chat()
        conv_id = main_window._current_conv.id
        send: SendController = main_window._send

        main_window._db.close()
        # Must NOT raise
        send._on_title_failed(conv_id)

    def test_apply_auto_title_swallows_closed_db(self, main_window):
        """_apply_auto_title must not raise if the DB is gone."""
        from mchat.ui.send_controller import SendController
        main_window._on_new_chat()
        conv_id = main_window._current_conv.id
        send: SendController = main_window._send

        main_window._db.close()
        send._apply_auto_title(conv_id, "new title")

    def test_close_event_stops_title_workers(self, qtbot, main_window):
        """MainWindow.closeEvent must stop/wait any running TitleWorkers
        so they don't fire after the DB is closed."""
        from unittest.mock import MagicMock
        from PySide6.QtGui import QCloseEvent
        from mchat.ui.send_controller import SendController
        main_window._on_new_chat()
        send: SendController = main_window._send

        # Inject a fake worker that records its quit/wait calls
        fake_worker = MagicMock()
        fake_worker.isRunning.return_value = True
        send._title_workers[main_window._current_conv.id] = fake_worker

        main_window.closeEvent(QCloseEvent())

        assert fake_worker.quit.called or fake_worker.requestInterruption.called
        assert fake_worker.wait.called


class TestRetryInPlaceReplacement:
    """#130 — //retry updates the original error message's content in
    place instead of hiding it and appending a new message."""

    def test_retry_updates_error_message_content(self, qtbot, main_window):
        """After a successful retry, the error message row's content is
        replaced with the successful response text (same id, same position)."""
        from mchat.models.persona import Persona, generate_persona_id
        from mchat.ui.persona_target import PersonaTarget

        main_window._on_new_chat()
        conv_id = main_window._current_conv.id
        conv = main_window._current_conv

        # Create a persona to retry
        pid = generate_persona_id()
        main_window._db.create_persona(Persona(
            conversation_id=conv_id, id=pid,
            provider=Provider.CLAUDE, name="BotA", name_slug="bota",
        ))
        target = PersonaTarget(persona_id=pid, provider=Provider.CLAUDE)

        # Seed a user message and an error "assistant" message in the DB
        user_msg = Message(
            role=Role.USER, content="what is 2+2?",
            conversation_id=conv_id,
        )
        main_window._db.add_message(user_msg)
        conv.messages.append(user_msg)

        error_msg = Message(
            role=Role.ASSISTANT,
            content="[Error from claude: overloaded]",
            provider=Provider.CLAUDE,
            persona_id=pid,
            conversation_id=conv_id,
            display_mode=None,
        )
        main_window._db.add_message(error_msg)
        conv.messages.append(error_msg)
        # Re-fetch to get the real id
        conv.messages = main_window._db.get_messages(conv_id)
        error_id = next(
            m.id for m in conv.messages
            if m.content.startswith("[Error from")
        )

        # Seed SendController retry stash as if _on_error had fired
        send = main_window._send
        send._retry_targets[pid] = target
        send._retry_contexts[pid] = []  # empty context ok for fake provider
        send._retry_models[pid] = "fake-model"
        send._retry_labels[pid] = "BotA"
        send._retry_failed[pid] = ("overloaded", True)
        send._retry_error_msg_ids[pid] = error_id

        # Fire //retry
        main_window._on_message_submitted("//retry")

        # Wait for the fake worker to complete
        qtbot.waitUntil(
            lambda: len(main_window._send._multi_workers) == 0,
            timeout=5000,
        )

        # The error message row MUST still exist with the same id,
        # but with updated content (the fake provider yields "ok").
        msgs = main_window._db.get_messages(conv_id)
        retried = [m for m in msgs if m.id == error_id]
        assert len(retried) == 1, (
            "error message row should still exist (in-place update)"
        )
        assert "ok" in retried[0].content.lower()
        assert not retried[0].content.startswith("[Error from"), (
            f"content should no longer be an error string, got {retried[0].content!r}"
        )

    def test_retry_updates_display_mode_to_match_siblings(
        self, qtbot, main_window,
    ):
        """When siblings have display_mode='cols', the retried message's
        display_mode must also become 'cols' so the renderer groups them."""
        from mchat.models.persona import Persona, generate_persona_id
        from mchat.ui.persona_target import PersonaTarget

        main_window._on_new_chat()
        conv_id = main_window._current_conv.id
        conv = main_window._current_conv

        # Two personas
        p_a_id = generate_persona_id()
        p_b_id = generate_persona_id()
        main_window._db.create_persona(Persona(
            conversation_id=conv_id, id=p_a_id,
            provider=Provider.CLAUDE, name="A", name_slug="a",
        ))
        main_window._db.create_persona(Persona(
            conversation_id=conv_id, id=p_b_id,
            provider=Provider.OPENAI, name="B", name_slug="b",
        ))

        user_msg = Message(
            role=Role.USER, content="q", conversation_id=conv_id,
        )
        main_window._db.add_message(user_msg)
        conv.messages.append(user_msg)

        # A errored (display_mode=None), B succeeded (display_mode='cols')
        err = Message(
            role=Role.ASSISTANT, content="[Error from claude: x]",
            provider=Provider.CLAUDE, persona_id=p_a_id,
            conversation_id=conv_id, display_mode=None,
        )
        success = Message(
            role=Role.ASSISTANT, content="b-answer",
            provider=Provider.OPENAI, persona_id=p_b_id,
            conversation_id=conv_id, display_mode="cols",
        )
        main_window._db.add_message(err)
        main_window._db.add_message(success)
        conv.messages = main_window._db.get_messages(conv_id)
        err_id = next(m.id for m in conv.messages if m.persona_id == p_a_id)

        # Enable column mode so the retry picks "cols"
        main_window._column_mode = True

        send = main_window._send
        target = PersonaTarget(persona_id=p_a_id, provider=Provider.CLAUDE)
        send._retry_targets[p_a_id] = target
        send._retry_contexts[p_a_id] = []
        send._retry_models[p_a_id] = "fake-model"
        send._retry_labels[p_a_id] = "A"
        send._retry_failed[p_a_id] = ("x", True)
        send._retry_error_msg_ids[p_a_id] = err_id

        main_window._on_message_submitted("//retry")
        qtbot.waitUntil(
            lambda: len(main_window._send._multi_workers) == 0,
            timeout=5000,
        )

        msgs = main_window._db.get_messages(conv_id)
        retried = next(m for m in msgs if m.id == err_id)
        assert retried.display_mode == "cols", (
            f"retried message should adopt 'cols' display_mode, got {retried.display_mode!r}"
        )


class TestErrorOnSwitchedChat:
    """#134 — if the user switches conversations mid-send, a subsequent
    provider error must still persist to the ORIGINAL conversation, not
    the currently-visible one. Mirrors the #122 fix for success path."""

    def test_on_error_persists_to_original_conv_after_switch(
        self, qtbot, main_window,
    ):
        from mchat.ui.persona_target import PersonaTarget

        # Create conv A and seed the send state as if a send were in flight
        main_window._on_new_chat()
        conv_a_id = main_window._current_conv.id
        send = main_window._send
        send._seq_conv_id = conv_a_id
        # Register a fake outstanding worker so _on_error's pop path works
        fake_worker = type("W", (), {"last_error_transient": True})()
        send._multi_workers["claude"] = fake_worker

        # Create conv B and switch to it
        main_window._on_new_chat()
        conv_b_id = main_window._current_conv.id
        assert conv_b_id != conv_a_id

        # Fire _on_error as if the claude worker errored AFTER the switch
        target = PersonaTarget(persona_id="claude", provider=Provider.CLAUDE)
        send._on_error(target, "overloaded 529")

        # Error message must be in conv A's messages, NOT conv B's
        conv_a_msgs = main_window._db.get_messages(conv_a_id)
        conv_b_msgs = main_window._db.get_messages(conv_b_id)

        error_in_a = any(
            m.content.startswith("[Error from")
            for m in conv_a_msgs
        )
        error_in_b = any(
            m.content.startswith("[Error from")
            for m in conv_b_msgs
        )
        assert error_in_a, "error message should be persisted to conv A (original)"
        assert not error_in_b, "error message must NOT leak into conv B (current)"

    def test_on_error_retry_stash_populated_even_after_switch(
        self, main_window,
    ):
        """Even when the user switched chats, //retry must still be
        able to find the error message, so the retry stash needs the
        DB id of the error row persisted to the original conversation."""
        from mchat.ui.persona_target import PersonaTarget

        main_window._on_new_chat()
        conv_a_id = main_window._current_conv.id
        send = main_window._send
        send._seq_conv_id = conv_a_id
        fake_worker = type("W", (), {"last_error_transient": True})()
        send._multi_workers["claude"] = fake_worker

        main_window._on_new_chat()

        target = PersonaTarget(persona_id="claude", provider=Provider.CLAUDE)
        send._on_error(target, "overloaded")

        assert "claude" in send._retry_failed
        err_id = send._retry_error_msg_ids.get("claude")
        assert err_id is not None
        # And that id must resolve to a message in conv A
        conv_a_msgs = main_window._db.get_messages(conv_a_id)
        assert any(m.id == err_id for m in conv_a_msgs)

    def test_on_error_without_switch_behaves_as_before(self, main_window):
        """When no conversation switch happened, _on_error should still
        persist + render into the current (and original) conversation."""
        from mchat.ui.persona_target import PersonaTarget

        main_window._on_new_chat()
        conv_id = main_window._current_conv.id
        send = main_window._send
        send._seq_conv_id = conv_id
        fake_worker = type("W", (), {"last_error_transient": True})()
        send._multi_workers["claude"] = fake_worker

        target = PersonaTarget(persona_id="claude", provider=Provider.CLAUDE)
        send._on_error(target, "some error")

        msgs = main_window._db.get_messages(conv_id)
        assert any(m.content.startswith("[Error from") for m in msgs)
