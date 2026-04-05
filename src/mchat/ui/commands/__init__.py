# ------------------------------------------------------------------
# Component: commands (package)
# Responsibility: Dispatch // commands from user input to the
#                 appropriate domain handler. Handlers are organised
#                 into submodules by domain: history, selection, pins,
#                 help. The dispatcher matches the command string and
#                 forwards to the right handler with the CommandHost.
# Collaborators: CommandHost, history, selection, pins, help
# ------------------------------------------------------------------
from __future__ import annotations

from mchat.ui.commands import history, help as _help, personas, pins, selection
from mchat.ui.commands.host import CommandHost

__all__ = ["dispatch", "CommandHost"]


def dispatch(cmd: str, arg: str, host: CommandHost) -> bool:
    """Route a // command to its handler. Returns True if handled."""
    # Help
    if cmd == "//help":
        return _help.handle_help(host)

    # History / editing
    if cmd == "//limit":
        return history.handle_limit(arg, host)
    if cmd == "//pop":
        return history.handle_pop(host)
    if cmd == "//hide":
        return history.handle_hide(host)
    if cmd == "//unhide":
        return history.handle_unhide(host)
    if cmd == "//retry":
        return history.handle_retry(host)
    if cmd == "//rename":
        return history.handle_rename(arg, host)
    if cmd == "//vacuum":
        return history.handle_vacuum(host)

    # Selection / layout
    if cmd == "//select":
        return selection.handle_select(arg, host)
    if cmd == "//providers":
        return selection.handle_providers(host)
    if cmd in ("//columns", "//cols"):
        return selection.handle_columns(host)
    if cmd == "//lines":
        return selection.handle_lines(host)

    # Pins
    if cmd == "//pin":
        return pins.handle_pin(arg, host)
    if cmd == "//unpin":
        return pins.handle_unpin(arg, host)
    if cmd == "//pins":
        return pins.handle_pins(arg, host)

    # Personas (Stage 2.8)
    if cmd == "//addpersona":
        return personas.handle_addpersona(arg, host)
    if cmd == "//editpersona":
        return personas.handle_editpersona(arg, host)
    if cmd == "//removepersona":
        return personas.handle_removepersona(arg, host)
    if cmd == "//personas":
        return personas.handle_personas(host)

    return False
