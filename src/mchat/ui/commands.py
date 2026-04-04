# ------------------------------------------------------------------
# Component: commands
# Responsibility: Handle // commands from user input
# Collaborators: main_window (via callback interface)
# ------------------------------------------------------------------
from __future__ import annotations

from PySide6.QtGui import QColor, QTextBlockFormat, QTextCursor

from mchat.config import PROVIDER_META
from mchat.models.message import Message, Provider, Role

# Display names for providers
_PROVIDER_DISPLAY = {p: PROVIDER_META[p.value]["display"] for p in Provider}

_HELP_COMMANDS = (
    "Available commands:\n"
    "  //limit <N>           — only send chat from message N onwards\n"
    "  //limit last          — limit to the last request sent to providers\n"
    "  //limit ALL           — remove the limit, send full chat history\n"
    "  //pop                 — remove the last request and its responses\n"
    "  //hide                — hide the last request+responses, copy request to input\n"
    "  //unhide              — unhide all hidden messages\n"
    "  //retry               — re-attempt the last failed request\n"
    "  //select <providers>  — set target providers (e.g. //select gpt, claude)\n"
    "  //select all          — target all configured providers\n"
    "  //providers           — list available providers and config status\n"
    "  //rename <text>       — rename the current chat\n"
    "  //columns (//cols)    — show multi-provider responses side by side\n"
    "  //lines               — show multi-provider responses as a list (default)\n"
    "  //help                — show this help\n"
    "  //vacuum              — compact the database (rarely needed)"
)

_HELP_PROVIDERS = [
    ("claude, <message>", "send to Claude", Provider.CLAUDE),
    ("gpt, <message>", "send to GPT", Provider.OPENAI),
    ("gemini, <message>", "send to Gemini", Provider.GEMINI),
    ("perplexity, <message>", "send to Perplexity (also: pplx,)", Provider.PERPLEXITY),
    ("all, <message>", "send to all configured providers", None),
    ("flipped, <message>", "send to non-selected providers", None),
    ("(no prefix)", "send to current selection", None),
]


def dispatch(cmd: str, arg: str, app) -> bool:
    """Dispatch a // command. Returns True if handled.

    ``app`` is the MainWindow instance, used to access state and UI.
    """
    if cmd == "//help":
        return _handle_help(app)
    if cmd == "//limit":
        return _handle_limit(arg, app)
    if cmd == "//pop":
        return _handle_pop(app)
    if cmd == "//retry":
        return _handle_retry(app)
    if cmd == "//hide":
        return _handle_hide(app)
    if cmd == "//unhide":
        return _handle_unhide(app)
    if cmd == "//rename":
        return _handle_rename(arg, app)
    if cmd == "//select":
        return _handle_select(arg, app)
    if cmd == "//providers":
        return _handle_providers(app)
    if cmd in ("//columns", "//cols"):
        if not app._column_mode:
            app._toggle_column_mode()
        app._chat.add_note("column layout enabled")
        return True
    if cmd == "//lines":
        if app._column_mode:
            app._toggle_column_mode()
        app._chat.add_note("list layout enabled")
        return True
    if cmd == "//vacuum":
        return _handle_vacuum(app)
    return False


def _handle_help(app) -> bool:
    app._chat.add_note("Help")
    cursor = app._chat.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    fmt = QTextBlockFormat()
    fmt.setBackground(QColor("#f5f5f5"))

    for line in _HELP_COMMANDS.split("\n"):
        cursor.insertBlock(fmt)
        char_fmt = cursor.charFormat()
        char_fmt.setForeground(QColor("#666"))
        cursor.insertText(line, char_fmt)

    cursor.insertBlock(fmt)
    cursor.insertBlock(fmt)
    char_fmt = cursor.charFormat()
    char_fmt.setForeground(QColor("#666"))
    cursor.insertText("Provider prefixes:", char_fmt)

    configured = set(app._router._providers.keys()) if app._router else set()
    for prefix_text, desc, provider in _HELP_PROVIDERS:
        cursor.insertBlock(fmt)
        line = f"  {prefix_text:24s}— {desc}"
        if provider is not None and provider not in configured:
            cursor.insertHtml(
                f'<span style="color:#666; font-style:italic;">{line}</span>'
            )
        else:
            char_fmt = cursor.charFormat()
            char_fmt.setForeground(QColor("#666"))
            cursor.insertText(line, char_fmt)

    app._chat._scroll_to_bottom()
    return True


def _handle_limit(tag: str, app) -> bool:
    if not app._current_conv:
        app._on_new_chat()
    if not tag:
        app._chat.add_note("Error: //limit requires a message number, 'last', or 'ALL'")
        return True
    if tag.upper() == "ALL":
        app._current_conv.limit_mark = None
        app._db.set_conversation_limit(app._current_conv.id, None)
        app._chat.add_note("limit removed — full chat history will be sent")
        return True
    if tag.lower() == "last":
        messages = app._current_conv.messages
        last_user_idx = None
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].role == Role.USER:
                last_user_idx = i
                break
        if last_user_idx is None:
            app._chat.add_note("Error: no user message found")
            return True
        msg_num = last_user_idx + 1
        mark_name = f"#{msg_num}"
        app._db.set_mark(app._current_conv.id, mark_name, last_user_idx)
        app._current_conv.limit_mark = mark_name
        app._db.set_conversation_limit(app._current_conv.id, mark_name)
        app._chat.add_note(f"limit set to last request (message {msg_num}) — earlier context will not be sent")
        return True
    if tag.isdigit():
        idx = int(tag)
        if idx < 1 or idx > len(app._current_conv.messages):
            app._chat.add_note(f"Error: message {idx} out of range")
            return True
        mark_name = f"#{idx}"
        app._db.set_mark(app._current_conv.id, mark_name, idx - 1)
        app._current_conv.limit_mark = mark_name
        app._db.set_conversation_limit(app._current_conv.id, mark_name)
        app._chat.add_note(f"limit set to message {idx} — earlier context will not be sent")
        return True
    app._chat.add_note(f"Error: '{tag}' is not a valid message number — use //limit <N>, //limit last, or //limit ALL")
    return True


def _handle_rename(name: str, app) -> bool:
    if not name:
        app._chat.add_note("Error: //rename requires a name")
        return True
    if not app._current_conv:
        app._chat.add_note("Error: no active conversation")
        return True
    app._db.update_conversation_title(app._current_conv.id, name)
    app._current_conv.title = name
    app._load_conversations()
    app._chat.add_note(f"renamed to '{name}'")
    return True


def _handle_pop(app) -> bool:
    if not app._current_conv or not app._current_conv.messages:
        app._chat.add_note("Error: nothing to pop")
        return True
    messages = app._current_conv.messages
    last_user_idx = None
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].role == Role.USER:
            last_user_idx = i
            break
    if last_user_idx is None:
        app._chat.add_note("Error: no user message found to pop")
        return True
    to_remove = messages[last_user_idx:]
    ids_to_delete = [m.id for m in to_remove if m.id is not None]
    count = len(to_remove)
    app._db.delete_messages(ids_to_delete)
    del app._current_conv.messages[last_user_idx:]
    app._display_messages(app._current_conv.messages)
    app._chat.add_note(f"popped {count} message(s)")
    return True


def _handle_hide(app) -> bool:
    if not app._current_conv or not app._current_conv.messages:
        app._chat.add_note("Error: nothing to hide")
        return True
    messages = app._current_conv.messages
    last_user_idx = None
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].role == Role.USER:
            last_user_idx = i
            break
    if last_user_idx is None:
        app._chat.add_note("Error: no user message found to hide")
        return True
    to_hide = messages[last_user_idx:]
    ids_to_hide = [m.id for m in to_hide if m.id is not None]
    user_text = messages[last_user_idx].content
    count = len(to_hide)
    app._db.hide_messages(ids_to_hide)
    del app._current_conv.messages[last_user_idx:]
    app._display_messages(app._current_conv.messages)
    app._chat.add_note(f"hidden {count} message(s)")
    app._input._text_edit.setPlainText(user_text)
    return True


def _handle_unhide(app) -> bool:
    if not app._current_conv:
        app._chat.add_note("Error: no active conversation")
        return True
    app._db.unhide_all_messages(app._current_conv.id)
    app._current_conv.messages = app._db.get_messages(app._current_conv.id)
    app._display_messages(app._current_conv.messages)
    app._chat.add_note("all hidden messages restored")
    return True


def _handle_retry(app) -> bool:
    if not app._retry_failed:
        app._chat.add_note("Error: nothing to retry")
        return True
    for pid, (error, transient) in app._retry_failed.items():
        if not transient:
            name = _PROVIDER_DISPLAY[pid]
            app._chat.add_note(
                f"Warning: {name} error was non-transient ({error[:60]}) — retrying anyway"
            )
    error_ids = [mid for mid in app._retry_error_msg_ids.values() if mid is not None]
    if error_ids:
        app._db.hide_messages(error_ids)
        hidden_set = set(error_ids)
        app._current_conv.messages = [
            m for m in app._current_conv.messages if m.id not in hidden_set
        ]
        app._display_messages(app._current_conv.messages)
    failed_providers = list(app._retry_failed.keys())
    context_override = {
        pid: app._retry_contexts[pid]
        for pid in failed_providers
        if pid in app._retry_contexts
    }
    app._retry_failed.clear()
    app._retry_error_msg_ids.clear()
    app._input.set_enabled(False)
    app._send_multi(failed_providers, context_override=context_override)
    app._chat.add_note(
        f"retrying {', '.join(_PROVIDER_DISPLAY[p] for p in failed_providers)}..."
    )
    return True


def _handle_select(arg: str, app) -> bool:
    if not app._router:
        app._chat.add_note("Error: no providers configured")
        return True
    if not app._current_conv:
        app._on_new_chat()
    configured = set(app._router._providers.keys())
    if arg.strip().upper() == "ALL":
        selected = [p for p in Provider if p in configured]
        if not selected:
            app._chat.add_note("Error: no providers configured")
            return True
        app._router.set_selection(selected)
        names = ", ".join(_PROVIDER_DISPLAY[p] for p in selected)
        app._chat.add_note(f"selected: {names}")
    else:
        from mchat.router import PREFIX_TO_PROVIDER
        requested: list[Provider] = []
        unknown: list[str] = []
        for name in arg.split(","):
            name = name.strip().lower()
            if not name:
                continue
            p = PREFIX_TO_PROVIDER.get(name)
            if p and p not in requested:
                requested.append(p)
            else:
                unknown.append(name)
        if unknown:
            app._chat.add_note(f"Error: unknown provider(s): {', '.join(unknown)}")
        skipped = [p for p in requested if p not in configured]
        valid = [p for p in requested if p in configured]
        if skipped:
            names = ", ".join(_PROVIDER_DISPLAY[p] for p in skipped)
            app._chat.add_note(f"{names} skipped (no API key)")
        if not valid:
            app._chat.add_note("Error: no valid providers in selection")
            return True
        app._router.set_selection(valid)
        names = ", ".join(_PROVIDER_DISPLAY[p] for p in valid)
        app._chat.add_note(f"selected: {names}")

    app._save_selection()
    app._sync_checkboxes_from_selection()
    app._update_input_placeholder()
    app._update_input_color()
    return True


def _handle_providers(app) -> bool:
    lines: list[str] = []
    configured = set(app._router._providers.keys()) if app._router else set()
    for p in Provider:
        name = _PROVIDER_DISPLAY[p]
        if p not in configured:
            lines.append(f"  {name} (no API key)")
        else:
            lines.append(f"  {name}")
    app._chat.add_note("Providers")
    cursor = app._chat.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    fmt = QTextBlockFormat()
    fmt.setBackground(QColor("#f5f5f5"))
    for line in lines:
        cursor.insertBlock(fmt)
        char_fmt = cursor.charFormat()
        char_fmt.setForeground(QColor("#666"))
        cursor.insertText(line, char_fmt)
    app._chat._scroll_to_bottom()
    return True


def _handle_vacuum(app) -> bool:
    import os
    db_path = app._db._path
    size_before = os.path.getsize(db_path)
    app._db._conn.execute("VACUUM")
    size_after = os.path.getsize(db_path)
    saved = size_before - size_after
    if saved > 0:
        app._chat.add_note(
            f"database compacted: {size_before:,} → {size_after:,} bytes "
            f"({saved:,} bytes freed)"
        )
    else:
        app._chat.add_note(
            f"database already compact ({size_after:,} bytes)"
        )
    return True
