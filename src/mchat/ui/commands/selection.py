# ------------------------------------------------------------------
# Component: commands.selection
# Responsibility: Selection/layout commands — //select, //providers,
#                 //columns (//cols), //lines.
# Collaborators: CommandHost, router
# ------------------------------------------------------------------
from __future__ import annotations

from PySide6.QtGui import QColor, QTextBlockFormat, QTextCursor

from mchat.config import PROVIDER_META
from mchat.models.message import Provider
from mchat.ui.commands.host import CommandHost

_PROVIDER_DISPLAY = {p: PROVIDER_META[p.value]["display"] for p in Provider}


def handle_select(arg: str, host: CommandHost) -> bool:
    if not host._router:
        host._chat.add_note("Error: no providers configured")
        return True
    if not host._current_conv:
        host._on_new_chat()
    configured = set(host._router._providers.keys())
    if arg.strip().upper() == "ALL":
        selected = [p for p in Provider if p in configured]
        if not selected:
            host._chat.add_note("Error: no providers configured")
            return True
        host._router.set_selection(selected)
        names = ", ".join(_PROVIDER_DISPLAY[p] for p in selected)
        host._chat.add_note(f"selected: {names}")
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
            host._chat.add_note(f"Error: unknown provider(s): {', '.join(unknown)}")
        skipped = [p for p in requested if p not in configured]
        valid = [p for p in requested if p in configured]
        if skipped:
            names = ", ".join(_PROVIDER_DISPLAY[p] for p in skipped)
            host._chat.add_note(f"{names} skipped (no API key)")
        if not valid:
            host._chat.add_note("Error: no valid providers in selection")
            return True
        host._router.set_selection(valid)
        names = ", ".join(_PROVIDER_DISPLAY[p] for p in valid)
        host._chat.add_note(f"selected: {names}")

    host._save_selection()
    host._sync_checkboxes_from_selection()
    host._update_input_placeholder()
    host._update_input_color()
    return True


def handle_providers(host: CommandHost) -> bool:
    lines: list[str] = []
    configured = set(host._router._providers.keys()) if host._router else set()
    for p in Provider:
        name = _PROVIDER_DISPLAY[p]
        if p not in configured:
            lines.append(f"  {name} (no API key)")
        else:
            lines.append(f"  {name}")
    host._chat.add_note("Providers")
    cursor = host._chat.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    fmt = QTextBlockFormat()
    fmt.setBackground(QColor("#f5f5f5"))
    for line in lines:
        cursor.insertBlock(fmt)
        char_fmt = cursor.charFormat()
        char_fmt.setForeground(QColor("#666"))
        cursor.insertText(line, char_fmt)
    host._chat._scroll_to_bottom()
    return True


def handle_columns(host: CommandHost) -> bool:
    if not host._column_mode:
        host._toggle_column_mode()
    host._chat.add_note("column layout enabled")
    return True


def handle_lines(host: CommandHost) -> bool:
    if host._column_mode:
        host._toggle_column_mode()
    host._chat.add_note("list layout enabled")
    return True
