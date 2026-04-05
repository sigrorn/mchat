# ------------------------------------------------------------------
# Component: context_builder
# Responsibility: Pure context-policy functions — decide which
#                 messages a given provider should see when a request
#                 is built, and which message indices fall outside
#                 that context (used by the UI for exclusion shading).
#                 Owns //limit semantics, pin-bypass, and visibility
#                 filtering. Must NOT depend on Qt or on MainWindow.
# Collaborators: models.message, models.conversation, config, db,
#                ui.visibility, router
# ------------------------------------------------------------------
from __future__ import annotations

from mchat.config import PROVIDER_META, Config
from mchat.db import Database
from mchat.models.conversation import Conversation
from mchat.models.message import Message, Provider, Role
from mchat.router import Router
from mchat.ui.visibility import filter_for_provider


def pin_matches(pin_target: str | None, provider_id: Provider) -> bool:
    """Return True if a pinned message targets the given provider."""
    if not pin_target:
        return False
    if pin_target == "all":
        return True
    targets = {t.strip().lower() for t in pin_target.split(",") if t.strip()}
    return provider_id.value in targets


def build_context(
    conv: Conversation,
    target: Provider,
    db: Database,
    config: Config,
) -> list[Message]:
    """Build the message list that will be sent to ``target`` for a new
    request on ``conv``.

    Order of operations:
      1. Prepend system messages (provider-specific + conversation-wide).
      2. Apply //limit to slice off earlier history.
      3. Rescue pinned messages from before the cut-off whose pin_target
         covers this provider.
      4. Apply visibility filtering (user-message addressing + per-observer
         matrix) to the limited slice. Pinned messages bypass this.
      5. Strip provider-routing prefixes from user messages.
    """
    context: list[Message] = []

    # --- 1. System prompts ---
    parts: list[str] = []
    provider_prompt = config.get(PROVIDER_META[target.value]["system_prompt_key"])
    if provider_prompt:
        parts.append(provider_prompt)
    if conv.system_prompt:
        parts.append(conv.system_prompt)
    if parts:
        context.append(Message(role=Role.SYSTEM, content="\n\n".join(parts)))

    # --- 2. //limit slice ---
    all_messages = conv.messages
    messages = all_messages
    cut_idx = 0
    if conv.limit_mark is not None:
        idx = db.get_mark(conv.id, conv.limit_mark)
        if idx is not None and idx < len(all_messages):
            cut_idx = idx
            messages = all_messages[idx:]

    # --- 3. Pinned rescue ---
    pinned_prefix: list[Message] = []
    if cut_idx > 0:
        pinned_prefix = [
            m for m in all_messages[:cut_idx]
            if m.pinned and pin_matches(m.pin_target, target)
        ]

    # --- 4. Visibility filter (pins bypass) ---
    matrix = conv.visibility_matrix or {}
    messages = pinned_prefix + filter_for_provider(list(messages), target, matrix)

    # --- 5. Strip routing prefixes from user messages ---
    for msg in messages:
        if msg.role == Role.USER:
            _, cleaned = Router._strip_prefix(msg.content)
            context.append(
                Message(
                    role=msg.role,
                    content=cleaned,
                    provider=msg.provider,
                    model=msg.model,
                    conversation_id=msg.conversation_id,
                    id=msg.id,
                )
            )
        else:
            context.append(msg)
    return context


def compute_excluded_indices(
    conv: Conversation,
    db: Database,
    configured_providers: set[Provider],
) -> set[int]:
    """Return message indices that would NOT be sent to providers.

    Pinned messages whose target includes any currently-configured
    provider are NOT excluded (they stay visually unshaded), giving a
    visual cue that they are still sent despite being before the
    //limit cut-off.

    This is the single source of truth for display-time shading. The
    UI layer consumes the returned set; it must not redefine these
    rules inline.
    """
    if conv is None or conv.limit_mark is None:
        return set()
    idx = db.get_mark(conv.id, conv.limit_mark)
    if idx is None or idx <= 0:
        return set()
    messages = conv.messages
    excluded: set[int] = set()
    for i in range(min(idx, len(messages))):
        m = messages[i]
        if m.pinned and any(pin_matches(m.pin_target, p) for p in configured_providers):
            continue
        excluded.add(i)
    return excluded
