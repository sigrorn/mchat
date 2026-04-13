# ------------------------------------------------------------------
# Component: commands.personas
# Responsibility: //addpersona, //editpersona, //removepersona, and
#                 //personas command handlers. The command layer
#                 edits system_prompt_override only — model_override
#                 and color_override are dialog-only per D6. See
#                 docs/plans/personas.md § Stage 2.8.
# Collaborators: CommandHost, models.persona, db, router
# ------------------------------------------------------------------
from __future__ import annotations

import re
import sqlite3

from PySide6.QtGui import QColor, QTextBlockFormat, QTextCursor

from mchat.models.message import Message, Role
from mchat.models.persona import (
    Persona,
    generate_persona_id,
    slugify_persona_name,
    validate_persona_name,
)
from mchat.router import PREFIX_TO_PROVIDER
from mchat.ui.commands.host import CommandHost


# Parse `<provider> as "<name>" [inherit|new] <prompt>`
#
# Groups:
#   1 — provider shorthand (bare word)
#   2 — quoted display name
#   3 — optional mode keyword (inherit/new)
#   4 — prompt text (may be empty after strip)
#
# The quoted name uses a lazy match so quotes inside the prompt
# don't confuse the parser.
_ADDPERSONA_RE = re.compile(
    r'^\s*(\S+)\s+as\s+"([^"]+)"\s*(?:\b(inherit|new)\b)?\s*(.*)$',
    re.IGNORECASE | re.DOTALL,
)

# Parse `"<name>" <new prompt>` for //editpersona
_EDITPERSONA_RE = re.compile(
    r'^\s*"([^"]+)"\s*(.*)$', re.DOTALL,
)

# Parse `"<name>"` for //removepersona
_REMOVEPERSONA_RE = re.compile(r'^\s*"([^"]+)"\s*$')


def handle_addpersona(arg: str, host: CommandHost) -> bool:
    """//addpersona <provider> as "<name>" [inherit|new] <prompt>

    Creates a new persona in the current conversation and leaves a
    pinned note in the transcript so the setup is visible and
    survives //limit.
    """
    if host._current_conv is None:
        host._on_new_chat()

    match = _ADDPERSONA_RE.match(arg)
    if not match:
        # No args (or malformed): open the PersonaDialog instead of
        # showing a usage error. The dialog provides a full GUI for
        # creating personas.
        host._on_personas_requested(host._current_conv.id)
        return True

    provider_token = match.group(1).lower()
    name = match.group(2).strip()
    mode = (match.group(3) or "").lower()
    prompt_text = match.group(4).strip()

    # Validate provider
    provider = PREFIX_TO_PROVIDER.get(provider_token)
    if provider is None:
        known = ", ".join(sorted(PREFIX_TO_PROVIDER.keys()))
        host._chat.add_note(
            f"Error: unknown provider '{provider_token}'. Known: {known}"
        )
        return True

    # #140: validate name against the new alphabet + reserved-token
    # rules. Produces a clean user-facing error on any violation.
    try:
        validate_persona_name(name)
    except ValueError as e:
        host._chat.add_note(f"Error: {e}")
        return True
    try:
        name_slug = slugify_persona_name(name)
    except ValueError:
        host._chat.add_note(
            f"Error: persona name {name!r} produces an empty slug"
        )
        return True

    # Determine mode → created_at_message_index
    message_count = len(host._current_conv.messages)
    if mode == "inherit":
        cutoff = None
    elif mode == "new":
        # Cut off at the current count. If the chat is empty, this is
        # 0, which is equivalent to None for a persona that sees full
        # history (nothing to exclude).
        cutoff = message_count if message_count > 0 else None
    else:
        # Default: new mid-chat, inherit at chat start.
        cutoff = message_count if message_count > 0 else None

    # Build and insert the persona. The partial unique index catches
    # duplicate active slugs as an IntegrityError.
    persona = Persona(
        conversation_id=host._current_conv.id,
        id=generate_persona_id(),
        provider=provider,
        name=name,
        name_slug=name_slug,
        system_prompt_override=prompt_text if prompt_text else None,
        created_at_message_index=cutoff,
        sort_order=host._db.next_persona_sort_order(host._current_conv.id),
    )
    try:
        host._db.create_persona(persona)
    except sqlite3.IntegrityError:
        host._chat.add_note(
            f"Error: a persona named {name!r} already exists in this chat"
        )
        return True

    # Pinned name instruction — tells the provider to use the persona
    # name as its identity for the duration of the chat.
    name_instruction = Message(
        role=Role.USER,
        content=(
            f"Unless I say otherwise, for the scope of our chat, if my inputs "
            f"refer to your name, use {name} as your name. I might refer to it "
            f"in order to use it as a placeholder, and I want you to refer to "
            f"yourself as {name}."
        ),
        conversation_id=host._current_conv.id,
        pinned=True,
        pin_target=persona.id,
    )
    host._db.add_message(name_instruction)
    host._current_conv.messages.append(name_instruction)

    # Pinned transcript note — targets this specific persona so
    # same-provider personas don't see each other's setup.
    mode_label = "inherit" if cutoff is None else "new"
    note_text = (
        f'Added persona "{name}" ({provider.value}, {mode_label})'
        + (f": {prompt_text}" if prompt_text else "")
    )
    note_msg = Message(
        role=Role.USER,
        content=note_text,
        conversation_id=host._current_conv.id,
        pinned=True,
        pin_target=persona.id,
    )
    host._db.add_message(note_msg)
    host._current_conv.messages.append(note_msg)
    host._display_messages(host._current_conv.messages)

    # Add the new persona to the current selection so it participates
    # in the next send, while preserving the existing selection.
    _add_persona_to_selection(host, persona)

    host._chat.add_note(f'persona "{name}" added ({provider.value})')
    return True


def handle_editpersona(arg: str, host: CommandHost) -> bool:
    """//editpersona "<name>" <new prompt text>

    Updates the persona's system_prompt_override only. Model and
    colour overrides are dialog-only (Phase 3A).
    """
    if host._current_conv is None:
        host._chat.add_note("Error: no active conversation")
        return True

    match = _EDITPERSONA_RE.match(arg)
    if not match:
        host._chat.add_note(
            'Error: //editpersona "<name>" <new prompt text>'
        )
        return True

    name = match.group(1).strip()
    new_prompt = match.group(2).strip()

    try:
        slug = slugify_persona_name(name)
    except ValueError:
        host._chat.add_note(f"Error: invalid persona name {name!r}")
        return True

    # Find the persona by slug (case-insensitive via the slugify step).
    personas = host._db.list_personas(host._current_conv.id)
    target = next((p for p in personas if p.name_slug == slug), None)
    if target is None:
        host._chat.add_note(f"Error: no active persona named {name!r}")
        return True

    target.system_prompt_override = new_prompt if new_prompt else None
    host._db.update_persona(target)

    # Pinned note recording the edit — targets only this persona's provider.
    note_msg = Message(
        role=Role.USER,
        content=(
            f'Edited persona "{target.name}": '
            + (new_prompt if new_prompt else "(cleared — inherits global)")
        ),
        conversation_id=host._current_conv.id,
        pinned=True,
        pin_target=target.id,
    )
    host._db.add_message(note_msg)
    host._current_conv.messages.append(note_msg)
    host._display_messages(host._current_conv.messages)

    host._chat.add_note(f'persona "{target.name}" updated')
    return True


def handle_removepersona(arg: str, host: CommandHost) -> bool:
    """//removepersona "<name>"

    Tombstones the persona (D3 — never hard-deletes). Historical
    messages tagged with the persona's id continue to render with
    its name via list_personas_including_deleted.
    """
    if host._current_conv is None:
        host._chat.add_note("Error: no active conversation")
        return True

    match = _REMOVEPERSONA_RE.match(arg)
    if not match:
        host._chat.add_note('Error: //removepersona "<name>"')
        return True

    name = match.group(1).strip()
    try:
        slug = slugify_persona_name(name)
    except ValueError:
        host._chat.add_note(f"Error: invalid persona name {name!r}")
        return True

    personas = host._db.list_personas(host._current_conv.id)
    target = next((p for p in personas if p.name_slug == slug), None)
    if target is None:
        host._chat.add_note(f"Error: no active persona named {name!r}")
        return True

    host._db.tombstone_persona(host._current_conv.id, target.id)

    # Pinned note recording the removal — targets only this persona's provider.
    note_msg = Message(
        role=Role.USER,
        content=f'Removed persona "{target.name}"',
        conversation_id=host._current_conv.id,
        pinned=True,
        pin_target=target.id,
    )
    host._db.add_message(note_msg)
    host._current_conv.messages.append(note_msg)
    host._display_messages(host._current_conv.messages)

    host._chat.add_note(f'persona "{target.name}" removed')
    return True


def _add_persona_to_selection(host: CommandHost, persona: Persona) -> None:
    """Add the newly created persona to the current selection,
    preserving any existing targets. If the host has no selection
    state (e.g. in unit tests without full MainWindow), this is
    a no-op."""
    from mchat.ui.persona_target import PersonaTarget
    state = getattr(host, "_selection_state", None)
    if state is None:
        return
    current = list(state.selection)
    new_target = PersonaTarget(persona_id=persona.id, provider=persona.provider)
    if new_target not in current:
        current.append(new_target)
    state.set(current)


def handle_personas(host: CommandHost) -> bool:
    """//personas — list all active personas in the current chat."""
    if host._current_conv is None:
        host._chat.add_note("Error: no active conversation")
        return True

    personas = host._db.list_personas(host._current_conv.id)
    if not personas:
        host._chat.add_note("no personas — use //addpersona to create one")
        return True

    # Header note
    host._chat.add_note(f"{len(personas)} persona(s):")
    # Detailed per-persona notes
    for p in personas:
        prompt_preview = p.system_prompt_override or "(inherits global)"
        if len(prompt_preview) > 60:
            prompt_preview = prompt_preview[:57] + "..."
        scope = (
            "inherit" if p.created_at_message_index is None
            else f"new @ msg {p.created_at_message_index}"
        )
        host._chat.add_note(
            f'  "{p.name}" ({p.provider.value}, {scope}) — {prompt_preview}'
        )
    return True
