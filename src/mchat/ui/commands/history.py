# ------------------------------------------------------------------
# Component: commands.history
# Responsibility: History/editing commands — //limit, //pop, //hide,
#                 //unhide, //retry, //rename, //vacuum.
# Collaborators: CommandHost, config, db
# ------------------------------------------------------------------
from __future__ import annotations

from mchat.config import PROVIDER_META
from mchat.models.message import Provider, Role
from mchat.ui.commands.host import CommandHost

_PROVIDER_DISPLAY = {p: PROVIDER_META[p.value]["display"] for p in Provider}


def handle_limit(tag: str, host: CommandHost) -> bool:
    if not host._current_conv:
        host._on_new_chat()
    if not tag:
        host._chat.add_note("Error: //limit requires a message number, 'last', or 'ALL'")
        return True
    if tag.upper() == "ALL":
        host._current_conv.limit_mark = None
        host._db.set_conversation_limit(host._current_conv.id, None)
        host._display_messages(host._current_conv.messages)
        host._chat.add_note("limit removed — full chat history will be sent")
        return True
    if tag.lower() == "last":
        messages = host._current_conv.messages
        last_user_idx = None
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].role == Role.USER:
                last_user_idx = i
                break
        if last_user_idx is None:
            host._chat.add_note("Error: no user message found")
            return True
        msg_num = last_user_idx + 1
        mark_name = f"#{msg_num}"
        host._db.set_mark(host._current_conv.id, mark_name, last_user_idx)
        host._current_conv.limit_mark = mark_name
        host._db.set_conversation_limit(host._current_conv.id, mark_name)
        host._display_messages(host._current_conv.messages)
        host._chat.add_note(
            f"limit set to last request (message {msg_num}) — earlier context will not be sent"
        )
        return True
    if tag.isdigit():
        idx = int(tag)
        messages = host._current_conv.messages
        if idx < 1 or idx > len(messages):
            host._chat.add_note(f"Error: message {idx} out of range")
            return True
        if messages[idx - 1].role != Role.USER:
            host._chat.add_note(
                f"Error: message {idx} is not a user prompt — //limit must "
                f"target a user message so the cut-off starts at a request"
            )
            return True
        mark_name = f"#{idx}"
        host._db.set_mark(host._current_conv.id, mark_name, idx - 1)
        host._current_conv.limit_mark = mark_name
        host._db.set_conversation_limit(host._current_conv.id, mark_name)
        host._display_messages(messages)
        host._chat.add_note(
            f"limit set to message {idx} — earlier context will not be sent"
        )
        return True
    host._chat.add_note(
        f"Error: '{tag}' is not a valid message number — use //limit <N>, //limit last, or //limit ALL"
    )
    return True


def handle_rename(name: str, host: CommandHost) -> bool:
    if not name:
        host._chat.add_note("Error: //rename requires a name")
        return True
    if not host._current_conv:
        host._chat.add_note("Error: no active conversation")
        return True
    host._db.update_conversation_title(host._current_conv.id, name)
    host._current_conv.title = name
    # Update the sidebar item in place — no full reload/re-render.
    host._sidebar.update_conversation_title(host._current_conv.id, name)
    host._chat.add_note(f"renamed to '{name}'")
    return True


def handle_pop(host: CommandHost) -> bool:
    if not host._current_conv or not host._current_conv.messages:
        host._chat.add_note("Error: nothing to pop")
        return True
    messages = host._current_conv.messages
    last_user_idx = None
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].role == Role.USER:
            last_user_idx = i
            break
    if last_user_idx is None:
        host._chat.add_note("Error: no user message found to pop")
        return True
    to_remove = messages[last_user_idx:]
    ids_to_delete = [m.id for m in to_remove if m.id is not None]
    user_text = messages[last_user_idx].content
    count = len(to_remove)
    host._db.delete_messages(ids_to_delete)
    del host._current_conv.messages[last_user_idx:]
    host._display_messages(host._current_conv.messages)
    host._chat.add_note(f"popped {count} message(s)")
    # #127: restore the popped user text into the input box so the
    # user can edit and resend. Deferred via QTimer because _submit()
    # clears the input after the command handler returns.
    from PySide6.QtCore import QTimer
    QTimer.singleShot(0, lambda: host._input._text_edit.setPlainText(user_text))
    return True


def handle_hide(host: CommandHost) -> bool:
    if not host._current_conv or not host._current_conv.messages:
        host._chat.add_note("Error: nothing to hide")
        return True
    messages = host._current_conv.messages
    last_user_idx = None
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].role == Role.USER:
            last_user_idx = i
            break
    if last_user_idx is None:
        host._chat.add_note("Error: no user message found to hide")
        return True
    to_hide = messages[last_user_idx:]
    ids_to_hide = [m.id for m in to_hide if m.id is not None]
    user_text = messages[last_user_idx].content
    count = len(to_hide)
    host._db.hide_messages(ids_to_hide)
    del host._current_conv.messages[last_user_idx:]
    host._display_messages(host._current_conv.messages)
    host._chat.add_note(f"hidden {count} message(s)")
    # Deferred: _submit() clears the input after command handler returns
    from PySide6.QtCore import QTimer
    QTimer.singleShot(0, lambda: host._input._text_edit.setPlainText(user_text))
    return True


def handle_unhide(host: CommandHost) -> bool:
    if not host._current_conv:
        host._chat.add_note("Error: no active conversation")
        return True
    host._db.unhide_all_messages(host._current_conv.id)
    host._current_conv.messages = host._db.get_messages(host._current_conv.id)
    host._display_messages(host._current_conv.messages)
    host._chat.add_note("all hidden messages restored")
    return True


def handle_retry(host: CommandHost) -> bool:
    """Re-send the last failed requests. As of #130, successful retries
    update the original error message's content in place rather than
    hiding it and appending a new message — the retried response lands
    in the same transcript slot as the error it replaces, and inherits
    the sibling group's display_mode so the renderer groups it properly.
    """
    if not host._retry_failed:
        host._chat.add_note("Error: nothing to retry")
        return True

    # Access the controller directly — the retry stash lives there.
    send = host._send

    # Warn on non-transient errors, one note per persona.
    for persona_id, (error, transient) in send.retry_failed.items():
        if not transient:
            name = send.retry_labels.get(persona_id, persona_id)
            host._chat.add_note(
                f"Warning: {name} error was non-transient ({error[:60]}) — retrying anyway"
            )

    # Collect targets to re-send (as PersonaTargets from the stash)
    failed_persona_ids = list(send.retry_failed.keys())
    failed_targets = [
        send.retry_targets[pid]
        for pid in failed_persona_ids
        if pid in send.retry_targets
    ]
    context_override = {
        pid: send.retry_contexts[pid]
        for pid in failed_persona_ids
        if pid in send.retry_contexts
    }
    labels_copy = dict(send.retry_labels)

    # #130: stash error msg ids for _on_complete so it can update in
    # place instead of appending a new assistant row.
    send._retry_in_progress_ids = {
        pid: mid
        for pid, mid in send.retry_error_msg_ids.items()
        if mid is not None and pid in failed_persona_ids
    }

    # Clear only the failure bits; targets/contexts/labels stay populated
    # as the new send fills them in.
    send.retry_failed.clear()
    send.retry_error_msg_ids.clear()

    host._input.set_enabled(False)
    send.send_multi(failed_targets, context_override=context_override)
    host._chat.add_note(
        f"retrying {', '.join(labels_copy.get(pid, pid) for pid in failed_persona_ids)}..."
    )
    return True


def handle_edit(arg: str, host: CommandHost) -> bool:
    """//edit [n] — load a user message into the input box for editing.

    - //edit        → last user message
    - //edit 5      → message #5 (1-indexed, error if not a user msg)
    - //edit -2     → 2nd-last user message

    After submit, the amended text is sent to the original recipients.
    Old assistant responses following the edited message are hidden.
    Subsequent user messages are queued for review one at a time.
    """
    if not host._current_conv or not host._current_conv.messages:
        host._chat.add_note("Error: no user message to edit")
        return True

    messages = host._current_conv.messages
    arg = arg.strip()

    target_idx: int | None = None

    if not arg:
        # No arg → last user message
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].role == Role.USER:
                target_idx = i
                break
        if target_idx is None:
            host._chat.add_note("Error: no user message to edit")
            return True

    elif arg.lstrip("-").isdigit() and arg[0] == "-":
        # Negative offset: -N = Nth-last user message
        n = int(arg)  # negative
        user_indices = [i for i, m in enumerate(messages) if m.role == Role.USER]
        offset = abs(n)
        if offset < 1 or offset > len(user_indices):
            host._chat.add_note(
                f"Error: not enough user messages (have {len(user_indices)}, "
                f"requested {offset}th-last)"
            )
            return True
        target_idx = user_indices[-offset]

    elif arg.isdigit():
        # Absolute message number (1-indexed)
        idx = int(arg)
        if idx < 1 or idx > len(messages):
            host._chat.add_note(f"Error: message {idx} out of range (1–{len(messages)})")
            return True
        if messages[idx - 1].role != Role.USER:
            host._chat.add_note(
                f"Error: message {idx} is not a user message"
            )
            return True
        target_idx = idx - 1

    else:
        host._chat.add_note("Error: //edit [n] — n must be a message number or negative offset")
        return True

    target_msg = messages[target_idx]

    # Hide all messages after the target (assistant responses + subsequent pairs)
    to_hide = messages[target_idx + 1:]
    ids_to_hide = [m.id for m in to_hide if m.id is not None]
    if ids_to_hide:
        host._db.hide_messages(ids_to_hide)

    # Also hide the original user message itself (it will be re-sent)
    if target_msg.id is not None:
        host._db.hide_messages([target_msg.id])

    # Remove hidden messages from the in-memory list
    hidden_ids = set(ids_to_hide)
    if target_msg.id is not None:
        hidden_ids.add(target_msg.id)
    host._current_conv.messages = [
        m for m in messages if m.id not in hidden_ids
    ]
    host._display_messages(host._current_conv.messages)

    # Build the replay queue: subsequent user messages after the target
    replay_queue = [
        m for m in to_hide if m.role == Role.USER
        and not (m.content or "").strip().startswith("//")
    ]

    # Store edit state on the host for the send path to use
    host._edit_state = {
        "original_msg": target_msg,
        "replay_queue": replay_queue,
        "replay_index": 0,
    }

    # Pre-fill the input with the original message text. Deferred via
    # QTimer.singleShot because _submit() clears the input after the
    # command handler returns — we need the fill to happen after that.
    from PySide6.QtCore import QTimer

    def _fill_input():
        host._input._text_edit.setPlainText(target_msg.content)
        host._input._edit_mode = True

    QTimer.singleShot(0, _fill_input)
    host._chat.add_note(
        f"editing message {target_idx + 1} → {target_msg.addressed_to or 'current selection'}"
        f" — submit to re-send, empty to remove"
    )
    return True


def handle_vacuum(host: CommandHost) -> bool:
    import os
    db_path = host._db._path
    size_before = os.path.getsize(db_path)
    host._db._conn.execute("VACUUM")
    size_after = os.path.getsize(db_path)
    saved = size_before - size_after
    if saved > 0:
        host._chat.add_note(
            f"database compacted: {size_before:,} → {size_after:,} bytes "
            f"({saved:,} bytes freed)"
        )
    else:
        host._chat.add_note(
            f"database already compact ({size_after:,} bytes)"
        )
    return True
