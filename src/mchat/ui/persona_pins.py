# ------------------------------------------------------------------
# Component: persona_pins
# Responsibility: Ensure every active persona in a conversation has
#                 its pinned instruction messages (name identity +
#                 setup note). Extracted from MainWindow (#162) so the
#                 logic is independently testable without a running
#                 window.
# Collaborators: db, models.message, models.persona, ui.state
# ------------------------------------------------------------------
from __future__ import annotations

from mchat.db import Database
from mchat.models.conversation import Conversation
from mchat.models.message import Message, Role
from mchat.ui.persona_target import PersonaTarget
from mchat.ui.state import SelectionState


def ensure_persona_pins(
    db: Database,
    conv: Conversation,
    messages: list[Message],
    selection: SelectionState,
) -> None:
    """For every active persona in the conversation, create pinned
    instruction messages if they don't already exist. Also ensures
    each persona is in the current selection.

    ``messages`` is the in-memory message list (conv.messages or a
    snapshot). New pins are appended to it AND written to the DB.
    """
    personas = db.list_personas(conv.id)
    if not personas:
        return

    # Prune the selection: remove synthetic defaults for providers
    # that now have explicit personas, and stale persona_ids.
    valid_ids = {p.id for p in personas}
    explicit_providers = {p.provider.value for p in personas}
    current_sel = list(selection.selection)
    pruned = [
        t for t in current_sel
        if t.persona_id in valid_ids
        or (
            t.persona_id == t.provider.value
            and t.persona_id not in explicit_providers
        )
    ]
    if len(pruned) != len(current_sel):
        selection.set(pruned)

    # Index existing pinned messages by pin_target for identity detection.
    # Each persona gets two pins: an identity instruction ("use X as your
    # name") and a setup note ("Added persona X"). We key by pin_target
    # so renames update in-place instead of creating duplicates (#163).
    identity_pins_by_target: dict[str, Message] = {}
    note_pins_by_target: dict[str, Message] = {}
    for m in messages:
        if not m.pinned or not m.pin_target:
            continue
        if "as your name" in m.content:
            identity_pins_by_target[m.pin_target] = m
        elif m.content.startswith("Added persona "):
            note_pins_by_target[m.pin_target] = m

    for persona in personas:
        expected_identity = (
            f"Unless I say otherwise, for the scope of our chat, "
            f"if my inputs refer to your name, use {persona.name} "
            f"as your name. I might refer to it in order to use it "
            f"as a placeholder, and I want you to refer to yourself "
            f"as {persona.name}."
        )
        mode_label = (
            "inherit" if persona.created_at_message_index is None
            else "new"
        )
        prompt_text = persona.system_prompt_override or ""
        expected_note = (
            f'Added persona "{persona.name}" '
            f"({persona.provider.value}, {mode_label})"
            + (f": {prompt_text}" if prompt_text else "")
        )

        existing_identity = identity_pins_by_target.get(persona.id)
        existing_note = note_pins_by_target.get(persona.id)

        if existing_identity is not None:
            # Pin exists — update content if stale (rename case).
            if existing_identity.content != expected_identity:
                db.update_message_content(existing_identity.id, expected_identity)
                existing_identity.content = expected_identity
            if existing_note is not None and existing_note.content != expected_note:
                db.update_message_content(existing_note.id, expected_note)
                existing_note.content = expected_note
        else:
            # No pin yet — create both.
            name_instruction = Message(
                role=Role.USER,
                content=expected_identity,
                conversation_id=conv.id,
                pinned=True,
                pin_target=persona.id,
            )
            db.add_message(name_instruction)
            messages.append(name_instruction)

            note_msg = Message(
                role=Role.USER,
                content=expected_note,
                conversation_id=conv.id,
                pinned=True,
                pin_target=persona.id,
            )
            db.add_message(note_msg)
            messages.append(note_msg)

        # Ensure persona is in the selection.
        target = PersonaTarget(
            persona_id=persona.id, provider=persona.provider,
        )
        current = list(selection.selection)
        if target not in current:
            current.append(target)
            selection.set(current)
