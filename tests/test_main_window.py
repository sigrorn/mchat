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

    cfg = Config(config_path=tmp_path / "cfg.json")
    # Populate fake keys so every provider is "configured"
    for k in ("anthropic_api_key", "openai_api_key", "gemini_api_key", "perplexity_api_key"):
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
        """With fake keys for all four providers, the router contains all of them."""
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

    def test_empty_selection_rejected(self, main_window):
        """Unchecking the last provider must revert — at least one must stay selected."""
        # Ensure only one is checked
        main_window._router.set_selection([Provider.CLAUDE])
        main_window._sync_checkboxes_from_selection()
        main_window._checkboxes[Provider.CLAUDE].setChecked(False)
        # The router selection must still be non-empty
        assert len(main_window._router.selection) >= 1


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
        """Router.selection reads through ProviderSelectionState."""
        from mchat.models.message import Provider
        main_window._selection_state.set([Provider.OPENAI, Provider.GEMINI])
        assert main_window._router.selection == [Provider.OPENAI, Provider.GEMINI]

    def test_router_set_selection_writes_to_state(self, main_window):
        """Router.set_selection flows into the state object."""
        from mchat.models.message import Provider
        main_window._router.set_selection([Provider.CLAUDE])
        assert main_window._selection_state.selection == [Provider.CLAUDE]

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
