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

    # Scan existing pinned messages to avoid duplicates.
    existing_pins = {m.content for m in messages if m.pinned}

    for persona in personas:
        name_marker = f"use {persona.name} as your name"
        has_name_pin = any(name_marker in pin for pin in existing_pins)

        if not has_name_pin:
            name_instruction = Message(
                role=Role.USER,
                content=(
                    f"Unless I say otherwise, for the scope of our chat, "
                    f"if my inputs refer to your name, use {persona.name} "
                    f"as your name. I might refer to it in order to use it "
                    f"as a placeholder, and I want you to refer to yourself "
                    f"as {persona.name}."
                ),
                conversation_id=conv.id,
                pinned=True,
                pin_target=persona.id,
            )
            db.add_message(name_instruction)
            messages.append(name_instruction)

            mode_label = (
                "inherit" if persona.created_at_message_index is None
                else "new"
            )
            prompt_text = persona.system_prompt_override or ""
            note_text = (
                f'Added persona "{persona.name}" '
                f"({persona.provider.value}, {mode_label})"
                + (f": {prompt_text}" if prompt_text else "")
            )
            note_msg = Message(
                role=Role.USER,
                content=note_text,
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
