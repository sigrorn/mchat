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

    from mchat.ui.main_window import MainWindow
    window = MainWindow(cfg, db)
    qtbot.addWidget(window)
    yield window
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

    def test_combos_and_checkboxes_built_per_provider(self, main_window):
        assert set(main_window._combos.keys()) == set(Provider)
        assert set(main_window._checkboxes.keys()) == set(Provider)
        assert set(main_window._spend_labels.keys()) == set(Provider)

    def test_initial_conversation_created_or_loaded(self, main_window):
        """After startup, selecting a conversation must not blow up even
        when the DB is fresh (no prior conversations)."""
        # A fresh DB has no conversations; selecting in sidebar does nothing,
        # but the window should still be in a usable state.
        # Creating a new chat should populate _current_conv.
        main_window._on_new_chat()
        assert main_window._current_conv is not None


class TestSelectionSync:
    def test_checkbox_toggle_updates_router_selection(self, main_window):
        # Start with the default selection
        initial = set(main_window._router.selection)
        # Pick a provider not in the initial selection
        victim = next(p for p in Provider if p in main_window._checkboxes)
        cb = main_window._checkboxes[victim]
        if victim in initial:
            # Need to also have at least one other provider checked, so we
            # toggle on a different one first
            other = next(p for p in Provider if p != victim)
            main_window._checkboxes[other].setChecked(True)
        cb.setChecked(not cb.isChecked())
        # Router should reflect the new checkbox state
        assert set(main_window._router.selection) == set(
            p for p, c in main_window._checkboxes.items() if c.isChecked()
        )

    def test_empty_selection_allowed(self, main_window):
        """Stage 3A.4 — unchecking the last provider should be allowed;
        the selection becomes empty (persona-first UX)."""
        main_window._router.set_selection([Provider.CLAUDE])
        main_window._sync_checkboxes_from_selection()
        main_window._checkboxes[Provider.CLAUDE].setChecked(False)
        assert main_window._router.selection == []


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

    def test_all_prefix_selects_every_configured_provider(self, main_window):
        from mchat.models.message import Provider
        main_window._router.set_selection([Provider.CLAUDE])
        main_window._on_message_submitted("all,")
        # All four fake providers should now be selected
        assert set(main_window._router.selection) == set(Provider)
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

    def test_model_catalog_populated_after_construction(self, main_window):
        """FakeProvider.list_models returns two entries; after the
        fast populate those must be reachable via the catalog."""
        from mchat.models.message import Provider
        # populate_from_config seeds from config default; populate_from_providers
        # would push list_models results. For this smoke test we verify
        # the catalog has at least one entry per configured provider.
        for p in Provider:
            assert main_window._model_catalog.get(p) != []


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
            def __init__(self, db, config, conv_id_arg, parent=None):
                constructed.append((db, config, conv_id_arg))
                super().__init__(db, config, conv_id_arg, parent=parent)

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
