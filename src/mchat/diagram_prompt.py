# ------------------------------------------------------------------
# Component: diagram_prompt
# Responsibility: Decide what diagramming instruction to inject into
#                 the system prompt based on which rendering tools are
#                 available and the user's preference setting.
# Collaborators: dot_renderer, mermaid_renderer, config
# ------------------------------------------------------------------
from __future__ import annotations

from mchat.config import Config


def diagram_instruction(config: Config) -> str | None:
    """Return a system-prompt fragment telling the model which diagram
    format to use, or None if no instruction should be injected.

    The decision depends on:
      1. ``config.get("diagram_format")`` — ``"auto"`` (default),
         ``"mermaid"``, ``"graphviz"``, or ``"none"``.
      2. Tool availability (only consulted in ``"auto"`` mode):
         ``is_graphviz_available()`` and ``is_mmdc_available()``.
    """
    pref = config.get("diagram_format")

    if pref == "none":
        return None

    if pref == "mermaid":
        return (
            "When producing diagrams, use ```mermaid fenced code blocks. "
            "Do not use ASCII art or DOT."
        )

    if pref == "graphviz":
        return (
            "When producing diagrams, use ```dot fenced code blocks "
            "(Graphviz DOT language). Do not use mermaid or ASCII art."
        )

    # pref == "auto" (or any unrecognised value — treat as auto)
    from mchat.dot_renderer import is_graphviz_available
    from mchat.mermaid_renderer import is_mmdc_available

    has_graphviz = is_graphviz_available()
    has_mmdc = is_mmdc_available()

    if has_mmdc and has_graphviz:
        return (
            "When producing diagrams, use ```mermaid fenced code blocks. "
            "You may also use ```dot (Graphviz DOT) when the diagram is "
            "a pure directed or undirected graph. Do not use ASCII art."
        )

    if has_mmdc:
        return (
            "When producing diagrams, use ```mermaid fenced code blocks. "
            "Do not use ASCII art or DOT."
        )

    if has_graphviz:
        return (
            "When producing diagrams, use ```dot fenced code blocks "
            "(Graphviz DOT language). Do not use mermaid or ASCII art."
        )

    return None
