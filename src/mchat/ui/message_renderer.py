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
from mchat.models.persona import Persona
from mchat.ui.chat_widget import ChatWidget
from mchat.ui.context_builder import compute_excluded_indices
from mchat.ui.dot_markdown_ext import DotExtension
from mchat.ui.mermaid_markdown_ext import MermaidExtension

# Stable display order for multi-provider responses
PROVIDER_ORDER: list[Provider] = [
    Provider.CLAUDE,
    Provider.OPENAI,
    Provider.GEMINI,
    Provider.PERPLEXITY,
    Provider.MISTRAL,
]

PROVIDER_DISPLAY: dict[Provider, str] = {
    p: PROVIDER_META[p.value]["display"] for p in Provider
}


def message_grouping_key(msg: Message) -> str:
    """Return the key used to group consecutive assistant messages into
    multi-provider display groups.

    Two messages with distinct ``persona_id`` values form distinct
    group slots, even if they share the same backing provider — this
    is what lets the Italian-tutor scenario render "partner" and
    "evaluator" as two columns when both are backed by Claude.

    Legacy messages with ``persona_id=None`` fall back to
    ``provider.value`` so existing chats group exactly as before.
    """
    if msg.persona_id is not None:
        return msg.persona_id
    if msg.provider is not None:
        return msg.provider.value
    return ""  # SYSTEM or similar — shouldn't reach here in grouping paths


def resolve_message_label(
    msg: Message, personas_by_id: dict[str, Persona]
) -> str:
    """Return the display label for a message.

    If the message has a ``persona_id`` and a matching persona is
    provided (including tombstoned personas via
    ``list_personas_including_deleted``), return the persona's name.
    Otherwise fall back to the provider display name.
    """
    if msg.persona_id is not None:
        p = personas_by_id.get(msg.persona_id)
        if p is not None:
            return p.name
    if msg.provider is not None:
        return PROVIDER_DISPLAY.get(msg.provider, "Assistant")
    return "Assistant"

# Patterns the LLMs may echo at the start of their response.
_TAKE_ECHO_RE = re.compile(
    r"^\*{0,2}(?:Claude|GPT|Gemini|Perplexity|Mistral)(?:'s|'s)\s+take:?\*{0,2}\s*\n*",
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

        Detects consecutive assistant messages from distinct personas
        (the grouping key is ``msg.persona_id or msg.provider.value``,
        so two same-provider personas form distinct groups) and renders
        them either as a columned table or as stacked list items with
        persona-named headings. Tombstoned personas still resolve their
        historical labels via ``list_personas_including_deleted``.
        """
        self._chat.clear_messages()
        if conv is not None:
            excluded = compute_excluded_indices(conv, self._db, configured_providers)
            personas_by_id = {
                p.id: p for p in self._db.list_personas_including_deleted(conv.id)
            }
        else:
            excluded = set()
            personas_by_id = {}
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

                # Collect consecutive assistant messages from distinct
                # personas as (original_index, message) pairs. Grouping
                # key is (persona_id or provider.value), so two Claude
                # personas with different persona_ids form distinct
                # slots in the column table.
                group: list[tuple[int, Message]] = [(i, msg)]
                seen_keys = {message_grouping_key(msg)}
                group_mode = msg.display_mode
                j = i + 1
                while j < len(messages):
                    nxt = messages[j]
                    if nxt.role != Role.ASSISTANT:
                        break
                    nxt_key = message_grouping_key(nxt)
                    if nxt_key in seen_keys:
                        break
                    # Break on display_mode change (e.g. "seq" vs "lines"
                    # vs "cols") to separate send groups on reload
                    if nxt.display_mode != group_mode:
                        break
                    group.append((j, nxt))
                    seen_keys.add(nxt_key)
                    j += 1

                if len(group) > 1:
                    # Sort by persona sort_order, not PROVIDER_ORDER
                    sort_keys = {
                        p.id: p.sort_order for p in personas_by_id.values()
                    }
                    ordered_pairs = sorted(
                        group,
                        key=lambda pair: (
                            sort_keys.get(pair[1].persona_id, 99),
                            pair[1].persona_id or "",
                        ),
                    )
                    ordered = [m for _idx, m in ordered_pairs]
                    stored_mode = group[0][1].display_mode
                    use_cols = (
                        stored_mode == "cols" if stored_mode else column_mode
                    )
                    if use_cols:
                        group_indices = [idx for idx, _m in ordered_pairs]
                        self._render_column_group(
                            ordered, group_indices,
                            personas_by_id=personas_by_id,
                        )
                    else:
                        self._render_list_group(
                            ordered, personas_by_id=personas_by_id,
                        )
                else:
                    # Single assistant message — render as-is, label
                    # resolution still goes through the persona lookup
                    # so solo persona messages show the persona name.
                    self._chat._messages.append(msg)
                    self._chat._insert_rendered(msg)

                i = j
        finally:
            self._chat.setUpdatesEnabled(True)
        self._chat._scroll_to_bottom()

    # ------------------------------------------------------------------
    # Incremental rendering (used when a multi-provider request completes)
    # ------------------------------------------------------------------

    def _live_personas_by_id(self, responses: list[Message]) -> dict[str, Persona]:
        """Build a personas lookup from the DB for the conversation
        of the given response messages."""
        conv_id = next(
            (m.conversation_id for m in responses if m.conversation_id),
            None,
        )
        if conv_id is not None:
            return {
                p.id: p
                for p in self._db.list_personas_including_deleted(conv_id)
            }
        return {}

    def render_list_responses(self, responses: list[Message]) -> None:
        """Append already-persisted multi-provider responses as list items."""
        conv_id = next((m.conversation_id for m in responses if m.conversation_id), None)
        sort_keys = self._persona_sort_key(conv_id)
        ordered = self._stable_order(responses, sort_keys)
        personas_by_id = self._live_personas_by_id(ordered)
        for m in ordered:
            label = resolve_message_label(m, personas_by_id)
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
        conv_id = next((m.conversation_id for m in responses if m.conversation_id), None)
        sort_keys = self._persona_sort_key(conv_id)
        ordered = self._stable_order(responses, sort_keys)
        personas_by_id = self._live_personas_by_id(ordered)
        for m in ordered:
            self._chat._messages.append(m)
        table_html, provider_colors, base_colors = self._build_column_table(
            ordered, excluded=False, personas_by_id=personas_by_id,
        )
        self._chat._insert_column_table(
            table_html, provider_colors,
            group_size=len(ordered), base_colors=base_colors,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _persona_sort_key(self, conv_id: int | None = None) -> dict[str, int]:
        """Build a persona_id → sort_order map for ordering."""
        if conv_id is None:
            return {}
        return {
            p.id: p.sort_order
            for p in self._db.list_personas_including_deleted(conv_id)
        }

    def _stable_order(
        self, messages: list[Message], sort_keys: dict[str, int] | None = None,
    ) -> list[Message]:
        sort_keys = sort_keys or {}
        return sorted(
            messages,
            key=lambda m: (
                sort_keys.get(m.persona_id, 99) if m.persona_id else 99,
                m.persona_id or "",
            ),
        )

    def _provider_color(self, p: Provider) -> str:
        return self._config.get(PROVIDER_META[p.value]["color_key"])

    def _render_list_group(
        self,
        ordered: list[Message],
        *,
        personas_by_id: dict[str, Persona] | None = None,
    ) -> None:
        personas_by_id = personas_by_id or {}
        for m in ordered:
            label = resolve_message_label(m, personas_by_id)
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
        self,
        ordered: list[Message],
        group_indices: list[int],
        *,
        personas_by_id: dict[str, Persona] | None = None,
    ) -> None:
        personas_by_id = personas_by_id or {}
        excluded = any(idx in self._chat._excluded_indices for idx in group_indices)
        table_html, provider_colors, base_colors = self._build_column_table(
            ordered, excluded, personas_by_id=personas_by_id,
        )
        for m in ordered:
            self._chat._messages.append(m)
        self._chat._insert_column_table(
            table_html, provider_colors,
            group_size=len(ordered), base_colors=base_colors,
        )

    def _build_column_table(
        self,
        ordered: list[Message],
        excluded: bool,
        *,
        personas_by_id: dict[str, Persona] | None = None,
    ) -> tuple[str, list[str], list[str]]:
        """Return (table_html, effective_colors, base_colors).

        ``effective_colors`` are what the table is rendered with
        (shaded if ``excluded=True``). ``base_colors`` are the
        unshaded originals — ChatWidget stashes them so the partial
        exclusion-update path (#133) can re-derive shaded vs unshaded
        versions when the limit flips without having to re-render.
        """
        personas_by_id = personas_by_id or {}
        md = md_lib.Markdown(
            extensions=[
                "tables", "fenced_code", "sane_lists", DotExtension(), MermaidExtension(),
            ]
        )
        header_cells: list[str] = []
        body_cells: list[str] = []
        provider_colors: list[str] = []
        base_colors: list[str] = []
        for m in ordered:
            label = resolve_message_label(m, personas_by_id)
            # Use persona colour override if available, else provider colour
            persona = personas_by_id.get(m.persona_id) if m.persona_id else None
            if persona and persona.color_override:
                base_color = persona.color_override
            elif m.provider:
                base_color = self._provider_color(m.provider)
            else:
                base_color = "#d4d4d4"
            base_colors.append(base_color)
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
        return table_html, provider_colors, base_colors
