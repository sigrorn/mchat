# ------------------------------------------------------------------
# Component: MessageRenderer
# Responsibility: Render conversations (full re-render from history)
#                 and incremental multi-provider groups into a
#                 ChatWidget. Understands the list vs column display
#                 modes, detects multi-provider assistant groups, and
#                 hands column tables to ChatWidget._insert_column_table.
#                 Pulls its exclusion information from context_builder —
#                 never redefines context policy inline.
# Collaborators: ui.chat_widget, ui.context_builder, config, db,
#                models.message, models.conversation
# ------------------------------------------------------------------
from __future__ import annotations

import re

import markdown as md_lib

from mchat.config import PROVIDER_META, Config
from mchat.db import Database
from mchat.models.conversation import Conversation
from mchat.models.message import Message, Provider, Role
from mchat.ui.chat_widget import ChatWidget
from mchat.ui.context_builder import compute_excluded_indices

# Stable display order for multi-provider responses
PROVIDER_ORDER: list[Provider] = [
    Provider.CLAUDE,
    Provider.OPENAI,
    Provider.GEMINI,
    Provider.PERPLEXITY,
]

PROVIDER_DISPLAY: dict[Provider, str] = {
    p: PROVIDER_META[p.value]["display"] for p in Provider
}

# Patterns the LLMs may echo at the start of their response.
_TAKE_ECHO_RE = re.compile(
    r"^\*{0,2}(?:Claude|GPT|Gemini|Perplexity)(?:'s|'s)\s+take:?\*{0,2}\s*\n*",
    re.IGNORECASE,
)


def strip_echoed_heading(text: str) -> str:
    """Remove any LLM-echoed 'X's take:' heading from the start of a response."""
    return _TAKE_ECHO_RE.sub("", text)


class MessageRenderer:
    """Draws conversations into a ChatWidget.

    The renderer never invents context-policy rules; all shading
    decisions come from context_builder.compute_excluded_indices.
    """

    def __init__(self, chat: ChatWidget, config: Config, db: Database) -> None:
        self._chat = chat
        self._config = config
        self._db = db

    # ------------------------------------------------------------------
    # Full re-render
    # ------------------------------------------------------------------

    def display_messages(
        self,
        conv: Conversation | None,
        messages: list[Message],
        column_mode: bool,
        configured_providers: set[Provider],
    ) -> None:
        """Clear the chat and re-render ``messages`` from scratch.

        Detects consecutive assistant messages from different providers
        and renders them either as a columned table (if their stored
        display_mode is "cols", or the global toggle is on for legacy
        messages without a stored mode) or as stacked list items with
        "X's take:" headings.
        """
        self._chat.clear_messages()
        if conv is not None:
            excluded = compute_excluded_indices(conv, self._db, configured_providers)
        else:
            excluded = set()
        self._chat.set_excluded_indices(excluded)
        self._chat.setUpdatesEnabled(False)
        try:
            i = 0
            while i < len(messages):
                msg = messages[i]
                if msg.role != Role.ASSISTANT:
                    self._chat._messages.append(msg)
                    self._chat._insert_rendered(msg)
                    i += 1
                    continue

                # Collect consecutive assistant messages from distinct providers
                # as (original_index, message) pairs so we never have to
                # re-derive positions via value-based list.index().
                group: list[tuple[int, Message]] = [(i, msg)]
                seen_providers = {msg.provider}
                j = i + 1
                while j < len(messages):
                    nxt = messages[j]
                    if nxt.role != Role.ASSISTANT or nxt.provider in seen_providers:
                        break
                    group.append((j, nxt))
                    seen_providers.add(nxt.provider)
                    j += 1

                if len(group) > 1:
                    ordered_pairs = sorted(
                        group,
                        key=lambda pair: (
                            PROVIDER_ORDER.index(pair[1].provider)
                            if pair[1].provider in PROVIDER_ORDER else 99
                        ),
                    )
                    ordered = [m for _idx, m in ordered_pairs]
                    stored_mode = group[0][1].display_mode
                    use_cols = (
                        stored_mode == "cols" if stored_mode else column_mode
                    )
                    if use_cols:
                        group_indices = [idx for idx, _m in ordered_pairs]
                        self._render_column_group(ordered, group_indices)
                    else:
                        self._render_list_group(ordered)
                else:
                    # Single assistant message — render as-is
                    self._chat._messages.append(msg)
                    self._chat._insert_rendered(msg)

                i = j
        finally:
            self._chat.setUpdatesEnabled(True)
        self._chat._scroll_to_bottom()

    # ------------------------------------------------------------------
    # Incremental rendering (used when a multi-provider request completes)
    # ------------------------------------------------------------------

    def render_list_responses(self, responses: list[Message]) -> None:
        """Append already-persisted multi-provider responses as list items.

        Callers must have already stored the messages in the DB and
        appended them to the conversation's in-memory list; this method
        only handles the visual rendering into the chat widget.
        """
        ordered = self._stable_order(responses)
        for m in ordered:
            label = PROVIDER_DISPLAY.get(m.provider, "Assistant")
            clean = strip_echoed_heading(m.content)
            display_msg = Message(
                role=m.role,
                content=f"**{label}'s take:**\n\n{clean}",
                provider=m.provider,
                model=m.model,
                conversation_id=m.conversation_id,
                id=m.id,
            )
            self._chat._messages.append(m)
            self._chat._insert_rendered(display_msg)
            self._chat._scroll_to_bottom()

    def render_column_responses(self, responses: list[Message]) -> None:
        """Append already-persisted multi-provider responses as a column table."""
        ordered = self._stable_order(responses)
        for m in ordered:
            self._chat._messages.append(m)
        table_html, provider_colors = self._build_column_table(ordered, excluded=False)
        self._chat._insert_column_table(table_html, provider_colors)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _stable_order(self, messages: list[Message]) -> list[Message]:
        return sorted(
            messages,
            key=lambda m: (
                PROVIDER_ORDER.index(m.provider)
                if m.provider in PROVIDER_ORDER else 99
            ),
        )

    def _provider_color(self, p: Provider) -> str:
        return self._config.get(PROVIDER_META[p.value]["color_key"])

    def _render_list_group(self, ordered: list[Message]) -> None:
        for m in ordered:
            label = PROVIDER_DISPLAY.get(m.provider, "Assistant")
            clean = strip_echoed_heading(m.content)
            display_msg = Message(
                role=m.role,
                content=f"**{label}'s take:**\n\n{clean}",
                provider=m.provider,
                model=m.model,
                conversation_id=m.conversation_id,
                id=m.id,
            )
            self._chat._messages.append(m)
            self._chat._insert_rendered(display_msg)

    def _render_column_group(
        self, ordered: list[Message], group_indices: list[int]
    ) -> None:
        excluded = any(idx in self._chat._excluded_indices for idx in group_indices)
        table_html, provider_colors = self._build_column_table(ordered, excluded)
        for m in ordered:
            self._chat._messages.append(m)
        self._chat._insert_column_table(table_html, provider_colors)

    def _build_column_table(
        self, ordered: list[Message], excluded: bool
    ) -> tuple[str, list[str]]:
        md = md_lib.Markdown(extensions=["tables", "fenced_code", "sane_lists"])
        header_cells: list[str] = []
        body_cells: list[str] = []
        provider_colors: list[str] = []
        for m in ordered:
            label = PROVIDER_DISPLAY.get(m.provider, "Assistant")
            base_color = self._provider_color(m.provider) if m.provider else "#d4d4d4"
            color = self._chat._shade(base_color) if excluded else base_color
            provider_colors.append(color)
            md.reset()
            rendered = md.convert(strip_echoed_heading(m.content))
            header_cells.append(
                f'<th style="background-color:{color}; padding:8px; '
                f'text-align:left; vertical-align:top;">{label}\'s take</th>'
            )
            body_cells.append(
                f'<td style="background-color:{color}; padding:8px; '
                f'vertical-align:top;">{rendered}</td>'
            )
        table_html = (
            f'<table style="width:100%; border-collapse:collapse;">'
            f'<tr>{"".join(header_cells)}</tr>'
            f'<tr>{"".join(body_cells)}</tr>'
            f'</table>'
        )
        return table_html, provider_colors
