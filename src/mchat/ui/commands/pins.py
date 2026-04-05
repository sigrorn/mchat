# ------------------------------------------------------------------
# Component: commands.pins
# Responsibility: Pin management commands — //pin, //unpin, //pins.
# Collaborators: CommandHost, router, db
# ------------------------------------------------------------------
from __future__ import annotations

from PySide6.QtGui import QColor, QTextBlockFormat, QTextCursor

from mchat.config import PROVIDER_META
from mchat.models.message import Message, Provider, Role
from mchat.ui.commands.host import CommandHost

_PROVIDER_DISPLAY = {p: PROVIDER_META[p.value]["display"] for p in Provider}


def handle_pin(arg: str, host: CommandHost) -> bool:
    if not host._current_conv:
        host._on_new_chat()
    if not arg or "," not in arg:
        host._chat.add_note(
            "Error: //pin requires a target — e.g. //pin gemini, <instruction>"
        )
        return True
    target_part, _, instruction = arg.partition(",")
    target_part = target_part.strip().lower()
    instruction = instruction.strip()
    if not target_part or not instruction:
        host._chat.add_note(
            "Error: //pin requires both a target and an instruction — "
            "e.g. //pin claude, be concise"
        )
        return True

    from mchat.router import PREFIX_TO_PROVIDER
    if target_part == "all":
        pin_target = "all"
        label = "all"
    else:
        # Target may be multiple providers separated by spaces or commas.
        names = [n for n in target_part.replace(",", " ").split() if n]
        providers: list[Provider] = []
        unknown: list[str] = []
        for name in names:
            p = PREFIX_TO_PROVIDER.get(name)
            if p and p not in providers:
                providers.append(p)
            elif not p:
                unknown.append(name)
        if unknown or not providers:
            host._chat.add_note(
                f"Error: unknown provider(s) in //pin target: {', '.join(unknown) or target_part}"
            )
            return True
        pin_target = ",".join(p.value for p in providers)
        label = ",".join(_PROVIDER_DISPLAY[p] for p in providers)

    msg = Message(
        role=Role.USER,
        content=instruction,
        conversation_id=host._current_conv.id,
        pinned=True,
        pin_target=pin_target,
    )
    host._db.add_message(msg)
    host._current_conv.messages.append(msg)
    host._display_messages(host._current_conv.messages)
    preview = instruction if len(instruction) <= 60 else instruction[:57] + "..."
    host._chat.add_note(f"pinned to {label}: {preview}")
    return True


def handle_unpin(arg: str, host: CommandHost) -> bool:
    if not host._current_conv:
        host._chat.add_note("Error: no active conversation")
        return True
    messages = host._current_conv.messages
    if arg.strip().upper() == "ALL":
        any_pinned = False
        for m in messages:
            if m.pinned and m.id is not None:
                host._db.set_pinned(m.id, False, None)
                m.pinned = False
                m.pin_target = None
                any_pinned = True
        if not any_pinned:
            host._chat.add_note("no pinned messages to remove")
            return True
        host._display_messages(messages)
        host._chat.add_note("all pins removed")
        return True
    if not arg.strip().isdigit():
        host._chat.add_note("Error: //unpin requires a message number or ALL")
        return True
    n = int(arg.strip())
    if n < 1 or n > len(messages):
        host._chat.add_note(f"Error: message {n} out of range")
        return True
    m = messages[n - 1]
    if not m.pinned:
        host._chat.add_note(f"Error: message {n} is not pinned")
        return True
    if m.id is not None:
        host._db.set_pinned(m.id, False, None)
    m.pinned = False
    m.pin_target = None
    host._display_messages(messages)
    host._chat.add_note(f"unpinned message {n}")
    return True


def handle_pins(arg: str, host: CommandHost) -> bool:
    if not host._current_conv:
        host._chat.add_note("Error: no active conversation")
        return True

    # Optional provider filter: //pins claude → only pins that would be
    # delivered to Claude (i.e. pin_target is "all" or contains claude).
    filter_provider: Provider | None = None
    if arg.strip():
        from mchat.router import PREFIX_TO_PROVIDER
        name = arg.strip().lower()
        filter_provider = PREFIX_TO_PROVIDER.get(name)
        if filter_provider is None:
            host._chat.add_note(f"Error: unknown provider '{arg.strip()}'")
            return True

    messages = host._current_conv.messages
    pinned = [(i + 1, m) for i, m in enumerate(messages) if m.pinned]
    if filter_provider is not None:
        def _matches(m) -> bool:
            if not m.pin_target:
                return False
            if m.pin_target == "all":
                return True
            targets = {t.strip().lower() for t in m.pin_target.split(",") if t.strip()}
            return filter_provider.value in targets
        pinned = [(n, m) for n, m in pinned if _matches(m)]

    if not pinned:
        if filter_provider is not None:
            host._chat.add_note(
                f"no pinned messages for {_PROVIDER_DISPLAY[filter_provider]}"
            )
        else:
            host._chat.add_note("no pinned messages")
        return True

    def _label(target: str | None) -> str:
        if not target or target == "all":
            return "all"
        labels: list[str] = []
        for v in target.split(","):
            v = v.strip()
            try:
                labels.append(_PROVIDER_DISPLAY[Provider(v)])
            except ValueError:
                labels.append(v)
        return ",".join(labels)

    if filter_provider is not None:
        host._chat.add_note(
            f"Pinned instructions for {_PROVIDER_DISPLAY[filter_provider]}"
        )
    else:
        host._chat.add_note("Pinned instructions")
    cursor = host._chat.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    fmt = QTextBlockFormat()
    fmt.setBackground(QColor("#f5f5f5"))
    for n, m in pinned:
        line = f"  {n}: [{_label(m.pin_target)}] {m.content}"
        cursor.insertBlock(fmt)
        char_fmt = cursor.charFormat()
        char_fmt.setForeground(QColor("#666"))
        cursor.insertText(line, char_fmt)
    host._chat._scroll_to_bottom()
    return True
