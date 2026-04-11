# ------------------------------------------------------------------
# Component: test_commands_personas
# Responsibility: Tests for the //addpersona, //editpersona,
#                 //removepersona, and //personas command handlers.
# Collaborators: ui.commands.personas, ui.commands (dispatch), db
# ------------------------------------------------------------------
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from mchat.config import Config
from mchat.db import Database
from mchat.models.message import Provider, Role


@pytest.fixture
def db(tmp_path):
    d = Database(db_path=tmp_path / "cmds.db")
    yield d
    d.close()


@pytest.fixture
def config(tmp_path):
    return Config(config_path=tmp_path / "cfg.json")


@pytest.fixture
def host(db, config):
    """Build a minimal CommandHost fake. Most persona commands only
    need _db, _current_conv, _chat, and _display_messages — no full
    MainWindow required."""
    h = MagicMock()
    h._db = db
    conv = db.create_conversation()
    h._current_conv = conv
    h._current_conv.messages = []

    # _chat.add_note captures notes
    h._chat.notes = []
    h._chat.add_note = lambda text: h._chat.notes.append(text)
    h._chat.textCursor = MagicMock()
    h._chat._scroll_to_bottom = MagicMock()

    # _display_messages is a no-op (we don't render in these tests)
    h._display_messages = MagicMock()
    h._on_new_chat = MagicMock()
    return h


class TestAddPersona:
    def test_basic_add_creates_row(self, host, db):
        from mchat.ui.commands.personas import handle_addpersona
        result = handle_addpersona(
            'claude as "Partner" new Start an Italian conversation', host,
        )
        assert result is True

        personas = db.list_personas(host._current_conv.id)
        assert len(personas) == 1
        p = personas[0]
        assert p.name == "Partner"
        assert p.name_slug == "partner"
        assert p.provider == Provider.CLAUDE
        assert p.system_prompt_override == "Start an Italian conversation"

    def test_explicit_inherit_mode(self, host, db):
        """`inherit` mode means the persona sees full history —
        created_at_message_index is None."""
        from mchat.ui.commands.personas import handle_addpersona
        # Add a few pre-existing messages so "mid-chat" matters
        conv = host._current_conv
        host._current_conv.messages = [MagicMock(), MagicMock(), MagicMock()]

        handle_addpersona(
            'claude as "Inheritor" inherit see everything', host,
        )
        p = db.list_personas(conv.id)[0]
        assert p.created_at_message_index is None

    def test_explicit_new_mode_mid_chat(self, host, db):
        """`new` mode mid-chat sets created_at_message_index to the
        current message count — the persona starts fresh."""
        from mchat.ui.commands.personas import handle_addpersona
        host._current_conv.messages = [MagicMock(), MagicMock(), MagicMock()]
        handle_addpersona(
            'claude as "Fresh" new start here', host,
        )
        p = db.list_personas(host._current_conv.id)[0]
        assert p.created_at_message_index == 3

    def test_default_mode_at_chat_start_is_none(self, host, db):
        """Omitting the mode when the chat is empty → inherit (None).
        Nothing to inherit either way."""
        from mchat.ui.commands.personas import handle_addpersona
        handle_addpersona('claude as "First" the first persona', host)
        p = db.list_personas(host._current_conv.id)[0]
        assert p.created_at_message_index is None

    def test_default_mode_mid_chat_is_new(self, host, db):
        """Omitting the mode mid-chat → new (cutoff at current count)."""
        from mchat.ui.commands.personas import handle_addpersona
        host._current_conv.messages = [MagicMock(), MagicMock()]
        handle_addpersona('claude as "Late" added mid-chat', host)
        p = db.list_personas(host._current_conv.id)[0]
        assert p.created_at_message_index == 2

    def test_empty_prompt_is_none_override(self, host, db):
        """Whitespace-only prompt → system_prompt_override = None
        (persona inherits the global provider prompt per D6)."""
        from mchat.ui.commands.personas import handle_addpersona
        handle_addpersona('claude as "Inheritor" new   ', host)
        p = db.list_personas(host._current_conv.id)[0]
        assert p.system_prompt_override is None

    def test_rejects_reserved_name(self, host, db):
        """#140: 'flipped' was renamed to 'others'. The validator
        blocks both (plus provider shorthands) on new personas."""
        from mchat.ui.commands.personas import handle_addpersona
        for reserved in ("all", "others", "claude", "gpt", "gemini", "pplx"):
            before = len(db.list_personas(host._current_conv.id))
            handle_addpersona(
                f'claude as "{reserved}" new text', host,
            )
            after = len(db.list_personas(host._current_conv.id))
            assert after == before, f"reserved name {reserved} should be rejected"

    def test_rejects_duplicate_slug(self, host, db):
        from mchat.ui.commands.personas import handle_addpersona
        handle_addpersona('claude as "Partner" new first', host)
        handle_addpersona('claude as "Partner" new second', host)
        # Only the first one should exist
        personas = db.list_personas(host._current_conv.id)
        assert len(personas) == 1

    def test_rejects_unknown_provider(self, host, db):
        from mchat.ui.commands.personas import handle_addpersona
        handle_addpersona('notaprovider as "X" new text', host)
        assert len(db.list_personas(host._current_conv.id)) == 0

    def test_rejects_malformed_input(self, host, db):
        from mchat.ui.commands.personas import handle_addpersona
        # Missing "as" keyword
        handle_addpersona('claude "X" new text', host)
        assert len(db.list_personas(host._current_conv.id)) == 0
        # Missing quoted name
        handle_addpersona('claude as Name new text', host)
        assert len(db.list_personas(host._current_conv.id)) == 0

    def test_creates_pinned_notes_in_transcript(self, host, db):
        from mchat.ui.commands.personas import handle_addpersona
        handle_addpersona('claude as "Partner" new start it', host)
        messages = db.get_messages(host._current_conv.id)
        pinned = [m for m in messages if m.pinned]
        # Two pins: name instruction + setup note
        assert len(pinned) == 2
        # First pin is the name instruction
        assert "use Partner as your name" in pinned[0].content
        # Second pin is the setup note
        assert "Added persona" in pinned[1].content
        assert "Partner" in pinned[1].content

    def test_pin_target_is_persona_id_not_provider(self, host, db):
        """All pins must target the persona's id (not provider.value)
        so same-provider personas don't see each other's instructions."""
        from mchat.ui.commands.personas import handle_addpersona
        handle_addpersona('claude as "Partner" new be kind', host)
        personas = db.list_personas(host._current_conv.id)
        persona_id = personas[0].id
        messages = db.get_messages(host._current_conv.id)
        pinned = [m for m in messages if m.pinned]
        assert len(pinned) == 2
        assert all(p.pin_target == persona_id for p in pinned)

    def test_addpersona_adds_to_selection(self, host, db):
        """After //addpersona, the new persona should be added to
        the current selection (so the next send includes it)."""
        from mchat.ui.commands.personas import handle_addpersona
        from mchat.ui.persona_target import PersonaTarget, synthetic_default
        from mchat.ui.state import SelectionState
        # Set up a real selection state with Claude selected
        state = SelectionState([synthetic_default(Provider.CLAUDE)])
        host._selection_state = state
        handle_addpersona(
            'gpt as "Evaluator" new review my replies', host,
        )
        # The selection should now include the new persona
        providers = state.providers_only()
        assert Provider.CLAUDE in providers
        assert Provider.OPENAI in providers

    def test_addpersona_restores_previous_selection(self, host, db):
        """The pre-existing selection should be preserved (not replaced)
        when the new persona is added."""
        from mchat.ui.commands.personas import handle_addpersona
        from mchat.ui.persona_target import synthetic_default
        from mchat.ui.state import SelectionState
        state = SelectionState([
            synthetic_default(Provider.CLAUDE),
            synthetic_default(Provider.GEMINI),
        ])
        host._selection_state = state
        handle_addpersona(
            'gpt as "Checker" new check it', host,
        )
        providers = state.providers_only()
        assert Provider.CLAUDE in providers
        assert Provider.GEMINI in providers
        assert Provider.OPENAI in providers


class TestEditPersona:
    def test_update_system_prompt_only(self, host, db):
        from mchat.models.persona import Persona, generate_persona_id
        from mchat.ui.commands.personas import handle_editpersona

        p = Persona(
            conversation_id=host._current_conv.id,
            id=generate_persona_id(),
            provider=Provider.CLAUDE,
            name="Evaluator",
            name_slug="evaluator",
            system_prompt_override="original prompt",
            model_override="claude-opus-original",
            color_override="#112233",
        )
        db.create_persona(p)

        handle_editpersona('"Evaluator" be more critical', host)

        updated = db.list_personas(host._current_conv.id)[0]
        assert updated.system_prompt_override == "be more critical"
        # Model/color overrides untouched — command path only edits prompt
        assert updated.model_override == "claude-opus-original"
        assert updated.color_override == "#112233"

    def test_edit_unknown_persona_errors(self, host, db):
        from mchat.ui.commands.personas import handle_editpersona
        handle_editpersona('"Nobody" new text', host)
        # No personas exist, command should fail gracefully
        assert len(db.list_personas(host._current_conv.id)) == 0

    def test_edit_pin_target_is_persona_id(self, host, db):
        """Edit pin must target the persona's id."""
        from mchat.models.persona import Persona, generate_persona_id
        from mchat.ui.commands.personas import handle_editpersona
        p = Persona(
            conversation_id=host._current_conv.id,
            id=generate_persona_id(),
            provider=Provider.OPENAI,
            name="Checker", name_slug="checker",
        )
        db.create_persona(p)
        handle_editpersona('"Checker" revised prompt', host)
        messages = db.get_messages(host._current_conv.id)
        pinned = [m for m in messages if m.pinned]
        assert len(pinned) == 1
        assert pinned[0].pin_target == p.id

    def test_edit_case_insensitive_name_match(self, host, db):
        from mchat.models.persona import Persona, generate_persona_id
        from mchat.ui.commands.personas import handle_editpersona

        p = Persona(
            conversation_id=host._current_conv.id,
            id=generate_persona_id(),
            provider=Provider.CLAUDE,
            name="Partner",
            name_slug="partner",
            system_prompt_override="old",
        )
        db.create_persona(p)

        handle_editpersona('"PARTNER" new prompt', host)
        updated = db.list_personas(host._current_conv.id)[0]
        assert updated.system_prompt_override == "new prompt"


class TestRemovePersona:
    def test_tombstones_not_hard_deletes(self, host, db):
        from mchat.models.persona import Persona, generate_persona_id
        from mchat.ui.commands.personas import handle_removepersona

        p = Persona(
            conversation_id=host._current_conv.id,
            id=generate_persona_id(),
            provider=Provider.CLAUDE,
            name="Gone",
            name_slug="gone",
        )
        db.create_persona(p)

        handle_removepersona('"Gone"', host)

        # Active list is empty
        assert db.list_personas(host._current_conv.id) == []
        # But tombstoned list still has the row
        all_personas = db.list_personas_including_deleted(host._current_conv.id)
        assert len(all_personas) == 1
        assert all_personas[0].deleted_at is not None

    def test_remove_pin_target_is_persona_id(self, host, db):
        """Remove pin must target the persona's id."""
        from mchat.models.persona import Persona, generate_persona_id
        from mchat.ui.commands.personas import handle_removepersona
        p = Persona(
            conversation_id=host._current_conv.id,
            id=generate_persona_id(),
            provider=Provider.GEMINI,
            name="Gone", name_slug="gone",
        )
        db.create_persona(p)
        handle_removepersona('"Gone"', host)
        messages = db.get_messages(host._current_conv.id)
        pinned = [m for m in messages if m.pinned]
        assert len(pinned) == 1
        assert pinned[0].pin_target == p.id

    def test_remove_unknown_persona_errors(self, host, db):
        from mchat.ui.commands.personas import handle_removepersona
        handle_removepersona('"NoSuch"', host)
        # Should not raise, just no-op


class TestListPersonas:
    def test_lists_active_personas_only(self, host, db):
        from mchat.models.persona import Persona, generate_persona_id
        from mchat.ui.commands.personas import handle_personas

        # One active, one tombstoned
        active = Persona(
            conversation_id=host._current_conv.id,
            id=generate_persona_id(),
            provider=Provider.CLAUDE,
            name="Active",
            name_slug="active",
        )
        tomb = Persona(
            conversation_id=host._current_conv.id,
            id=generate_persona_id(),
            provider=Provider.OPENAI,
            name="Tombstoned",
            name_slug="tombstoned",
        )
        db.create_persona(active)
        db.create_persona(tomb)
        db.tombstone_persona(host._current_conv.id, tomb.id)

        handle_personas(host)

        # Notes should mention Active but not Tombstoned
        notes = "\n".join(host._chat.notes)
        assert "Active" in notes
        assert "Tombstoned" not in notes

    def test_empty_list_shows_none_message(self, host, db):
        from mchat.ui.commands.personas import handle_personas
        handle_personas(host)
        notes = "\n".join(host._chat.notes)
        assert "no personas" in notes.lower() or "none" in notes.lower()


class TestAddPersonaOpensDialog:
    """#93 — //addpersona with no args should open the PersonaDialog."""

    def test_no_args_opens_dialog(self, host, db):
        """//addpersona (no args) should call _on_personas_requested."""
        from mchat.ui.commands.personas import handle_addpersona
        handle_addpersona("", host)
        host._on_personas_requested.assert_called_once_with(
            host._current_conv.id
        )

    def test_no_args_does_not_show_error(self, host, db):
        """//addpersona (no args) should NOT show the usage error."""
        from mchat.ui.commands.personas import handle_addpersona
        handle_addpersona("", host)
        assert not any("Error" in n for n in host._chat.notes)

    def test_with_args_still_uses_command_path(self, host, db):
        """//addpersona with args should NOT open the dialog."""
        from mchat.ui.commands.personas import handle_addpersona
        handle_addpersona('claude as "Partner" new hello', host)
        host._on_personas_requested.assert_not_called()
        personas = db.list_personas(host._current_conv.id)
        assert len(personas) == 1


class TestDispatch:
    """Verify the dispatch router in commands/__init__.py wires the
    new handlers correctly."""

    def test_dispatch_addpersona(self, host, db):
        from mchat.ui.commands import dispatch
        dispatch("//addpersona", 'claude as "X" new hello', host)
        assert len(db.list_personas(host._current_conv.id)) == 1

    def test_dispatch_personas(self, host, db):
        from mchat.ui.commands import dispatch
        dispatch("//personas", "", host)
        # Should not raise; produces some output

    def test_dispatch_editpersona(self, host, db):
        from mchat.ui.commands import dispatch
        dispatch("//addpersona", 'claude as "X" new old', host)
        dispatch("//editpersona", '"X" new prompt', host)
        p = db.list_personas(host._current_conv.id)[0]
        assert p.system_prompt_override == "new prompt"

    def test_dispatch_removepersona(self, host, db):
        from mchat.ui.commands import dispatch
        dispatch("//addpersona", 'claude as "X" new text', host)
        dispatch("//removepersona", '"X"', host)
        assert db.list_personas(host._current_conv.id) == []
