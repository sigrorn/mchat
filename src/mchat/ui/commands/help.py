# ------------------------------------------------------------------
# Component: commands.help
# Responsibility: //help command — lists available commands and
#                 provider prefixes, greying unconfigured providers.
# Collaborators: CommandHost, config
# ------------------------------------------------------------------
from __future__ import annotations

from PySide6.QtGui import QColor, QTextBlockFormat, QTextCursor

from mchat.config import PROVIDER_META
from mchat.models.message import Provider
from mchat.ui.commands.host import CommandHost

_PROVIDER_DISPLAY = {p: PROVIDER_META[p.value]["display"] for p in Provider}

HELP_COMMANDS = (
    "Available commands:\n"
    "  //edit [N] | //edit -N        — edit and re-send message N (default: last)\n"
    "  //limit <N>                   — only send chat from message N onwards\n"
    "  //limit last                  — limit to the last request sent\n"
    "  //limit ALL                   — remove the limit, send full chat history\n"
    "  //pop                         — remove the last request and its responses\n"
    "  //hide                        — hide the last request+responses\n"
    "  //unhide                      — unhide all hidden messages\n"
    "  //retry                       — re-attempt the last failed request\n"
    "  +<name>                       — add a persona to the selection\n"
    "  -<name>                       — remove a persona from the selection\n"
    "  //select <names>              — set target personas/providers\n"
    "  //select all                  — target all personas\n"
    "  //providers                   — list available providers and config status\n"
    "  //pin <target>, <instr>       — pin an instruction (bypasses //limit)\n"
    "  //unpin <N> | //unpin ALL     — remove a pin or all pins\n"
    "  //pins [name]                 — list pinned instructions (persona or provider)\n"
    "  //addpersona                  — open the persona editor dialog\n"
    '  //addpersona <p> as "<n>" [inherit|new] <prompt>\n'
    "                                — create a named persona via command\n"
    '  //editpersona "<n>" <prompt>  — update a persona\'s system prompt\n'
    '  //removepersona "<n>"         — tombstone a persona\n'
    "  //personas                    — list personas in this chat\n"
    "  //rename <text>               — rename the current chat\n"
    "  //mode                         — (deprecated: use Runs after in Personas)\n"
    "  //visibility separated|joined  — quick visibility presets\n"
    "  //columns (//cols) / //lines  — column vs list layout\n"
    "  //help                        — show this help\n"
    "  //stats                       — size breakdown for the current chat\n"
    "  //vacuum                      — compact the database (rarely needed)\n"
    "\n"
    "Graphics:\n"
    "  ```dot ...```                 — inline DOT graph (requires graphviz)\n"
    "  ```mermaid ...```             — inline Mermaid diagram (requires mmdc)\n"
    "                                  both fall back to source display\n"
    "                                  when the tool is unavailable\n"
    "\n"
    "Targeting (#140):\n"
    "  @<persona> <message>          — send to a specific persona\n"
    "  @<p1> @<p2> <message>         — send to multiple targets\n"
    "  @all <message>                — send to every persona in the chat\n"
    "  @others <message>             — send to non-selected personas\n"
    "  (no @)                        — send to the current selection\n"
    "\n"
    "Persona example (Italian tutor with 3 providers):\n"
    '  //addpersona claude as "friend" new Start an Italian conversation\n'
    '  //addpersona openai as "critic" new Review my replies for mistakes\n'
    '  //addpersona mistral as "translator" new Word-level translations only\n'
    "  @friend ciao come stai?\n"
    "  @critic @translator what about my last answer?"
)

HELP_PROVIDERS = [
    ("@<name> <message>", "send to a specific persona by name", None),
    ("@claude <message>", "send to Claude (synthetic default)", Provider.CLAUDE),
    ("@gpt <message>", "send to GPT (synthetic default)", Provider.OPENAI),
    ("@gemini <message>", "send to Gemini (synthetic default)", Provider.GEMINI),
    ("@perplexity <message>", "send to Perplexity (also: @pplx)", Provider.PERPLEXITY),
    ("@mistral <message>", "send to Mistral (synthetic default)", Provider.MISTRAL),
    ("@apertus <message>", "send to Apertus (synthetic default)", Provider.APERTUS),
    ("@all <message>", "send to all personas in the chat", None),
    ("@others <message>", "send to non-selected personas", None),
    ("(no prefix)", "send to current selection", None),
]


def handle_help(host: CommandHost) -> bool:
    host._chat.add_note("Help")
    cursor = host._chat.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    fmt = QTextBlockFormat()
    fmt.setBackground(QColor("#f5f5f5"))

    for line in HELP_COMMANDS.split("\n"):
        cursor.insertBlock(fmt)
        char_fmt = cursor.charFormat()
        char_fmt.setForeground(QColor("#666"))
        cursor.insertText(line, char_fmt)

    cursor.insertBlock(fmt)
    cursor.insertBlock(fmt)
    char_fmt = cursor.charFormat()
    char_fmt.setForeground(QColor("#666"))
    cursor.insertText("Provider prefixes:", char_fmt)

    configured = set(host._router._providers.keys()) if host._router else set()
    for prefix_text, desc, provider in HELP_PROVIDERS:
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

    host._chat._scroll_to_bottom()
    return True
