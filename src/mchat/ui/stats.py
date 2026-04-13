# ------------------------------------------------------------------
# Component: stats
# Responsibility: Pure size-statistics helpers for a conversation —
#                 compute whole-chat and limited character counts per
#                 persona (via build_context) plus an "all visibility"
#                 baseline row. Consumed by the //stats command
#                 handler; no Qt dependency so the logic is easy to
#                 test in isolation.
# Collaborators: models.conversation, models.persona, ui.context_builder, ui.persona_target, db, config
# ------------------------------------------------------------------
from __future__ import annotations

from dataclasses import dataclass, field

from mchat.config import Config
from mchat.db import Database
from mchat.models.conversation import Conversation
from mchat.models.persona import Persona
from mchat.ui.context_builder import build_context
from mchat.ui.persona_target import PersonaTarget


# Rough chars → tokens heuristic used everywhere else in the app.
# Matches the order-of-magnitude estimate most tokenizers produce for
# English prose without needing a provider-specific tokenizer at stats
# time.
_CHARS_PER_TOKEN = 4


def estimate_tokens(chars: int) -> int:
    """Return an estimated token count for a character count."""
    if chars <= 0:
        return 0
    return chars // _CHARS_PER_TOKEN


@dataclass
class StatsRow:
    """One row in a stats section — a label plus its character count."""

    label: str
    chars: int

    @property
    def tokens(self) -> int:
        return estimate_tokens(self.chars)


@dataclass
class StatsSection:
    """A named block of rows: 'Whole chat' or 'Limit (#N)'."""

    heading: str
    rows: list[StatsRow] = field(default_factory=list)


@dataclass
class ChatStats:
    """Full stats report for a conversation. Always has at least one
    section (``whole``); ``limited`` is populated only when the
    conversation has an active ``limit_mark``.
    """

    whole: StatsSection
    limited: StatsSection | None = None


def _sum_messages_chars(messages: list) -> int:
    """Sum the character counts of a list of Messages."""
    return sum(len(m.content) for m in messages if m.content)


def _persona_rows(
    conv: Conversation,
    personas: list[Persona],
    db: Database,
    config: Config,
) -> list[StatsRow]:
    """Build one StatsRow per persona by running build_context() and
    summing the resulting messages' character counts.

    The rows include tombstoned personas (with a ``(removed)`` suffix)
    whenever the DB returns them — callers that want active-only
    rows should pre-filter the personas list.
    """
    rows: list[StatsRow] = []
    for p in personas:
        target = PersonaTarget(persona_id=p.id, provider=p.provider)
        ctx = build_context(conv, target, db, config)
        chars = _sum_messages_chars(ctx)
        label = f"{p.name} (removed)" if p.deleted_at is not None else p.name
        rows.append(StatsRow(label=label, chars=chars))
    return rows


def compute_chat_stats(
    conv: Conversation,
    db: Database,
    config: Config,
) -> ChatStats:
    """Return a ChatStats report for the given conversation.

    Two sections:
      * ``whole`` — stats as if ``//limit`` were not set:
          - one "all visibility" row (raw character sum of every
            message in ``conv.messages``)
          - one row per persona in the conversation, computed by
            running ``build_context`` with the conversation's
            ``limit_mark`` temporarily cleared.
      * ``limited`` — only present when ``conv.limit_mark`` is set:
          - "all visibility" row = raw character sum of messages
            at-or-after the limit cut (excludes pinned rescue)
          - per-persona rows = real outgoing context sizes with the
            current limit_mark, pin rescue, and visibility matrix.

    This function does not modify ``conv`` — it temporarily flips
    ``conv.limit_mark`` inside a try/finally to drive the whole-chat
    per-persona numbers, and restores the original value on exit.

    Performance note: runs ``build_context`` once per persona per
    section. For a 200+ message conversation with 4 personas, that's
    8 full context builds, which takes a couple of seconds. Caching
    is a possible future optimisation (#131).
    """
    personas = db.list_personas_including_deleted(conv.id)

    # --- Whole section ---
    whole_all_chars = _sum_messages_chars(conv.messages)
    whole_rows: list[StatsRow] = [
        StatsRow(label="all visibility", chars=whole_all_chars),
    ]
    original_limit = conv.limit_mark
    try:
        conv.limit_mark = None
        whole_rows.extend(_persona_rows(conv, personas, db, config))
    finally:
        conv.limit_mark = original_limit
    whole = StatsSection(heading="Whole chat", rows=whole_rows)

    # --- Limited section (optional) ---
    limited: StatsSection | None = None
    if conv.limit_mark is not None:
        # Raw "at-or-after limit" character sum
        cut_idx = db.get_mark(conv.id, conv.limit_mark)
        if cut_idx is None:
            cut_idx = 0
        limited_messages = (
            conv.messages[cut_idx:] if cut_idx < len(conv.messages) else []
        )
        limited_all_chars = _sum_messages_chars(limited_messages)

        limited_rows: list[StatsRow] = [
            StatsRow(label="all visibility", chars=limited_all_chars),
        ]
        limited_rows.extend(_persona_rows(conv, personas, db, config))
        limited = StatsSection(
            heading=f"Limit ({conv.limit_mark})", rows=limited_rows,
        )

    return ChatStats(whole=whole, limited=limited)


def format_stats(stats: ChatStats) -> list[str]:
    """Render a ChatStats report as a list of display lines for the
    chat note output. Each line is a single row; the caller prints
    them via ``chat.add_note`` or a cursor insertion loop.

    The format is space-padded so columns line up visually:

        Whole chat
          all visibility   12,345 chars  (~3,086 tokens)
          claudebot         8,210 chars  (~2,052 tokens)
          ...
    """
    lines: list[str] = []

    def _format_section(section: StatsSection) -> None:
        lines.append(section.heading)
        if not section.rows:
            lines.append("  (empty)")
            return
        # Two-column padding: longest label vs longest chars string
        max_label = max(len(row.label) for row in section.rows)
        max_chars = max(len(f"{row.chars:,}") for row in section.rows)
        for row in section.rows:
            label = row.label.ljust(max_label)
            chars_str = f"{row.chars:,}".rjust(max_chars)
            lines.append(
                f"  {label}  {chars_str} chars  (~{row.tokens:,} tokens)"
            )

    _format_section(stats.whole)
    if stats.limited is not None:
        lines.append("")
        _format_section(stats.limited)
    return lines
