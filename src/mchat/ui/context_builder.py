# ------------------------------------------------------------------
# Component: context_builder
# Responsibility: Pure context-policy functions — decide which
#                 messages a given persona should see when a request
#                 is built, and which message indices fall outside
#                 that context (used by the UI for exclusion shading).
#                 Owns //limit semantics, pin-bypass, visibility
#                 filtering, and the per-persona history cutoff.
#                 Must NOT depend on Qt or on MainWindow.
# Collaborators: models.message, models.conversation, models.persona,
#                config, db, ui.visibility, ui.persona_resolution,
#                ui.persona_target, router
# ------------------------------------------------------------------
from __future__ import annotations

from mchat.config import PROVIDER_META, Config
from mchat.db import Database
from mchat.models.conversation import Conversation
from mchat.models.message import Message, Provider, Role
from mchat.models.persona import Persona
from mchat.router import Router
from mchat.ui.persona_resolution import resolve_persona_prompt
from mchat.ui.persona_target import PersonaTarget, synthetic_default
from mchat.ui.visibility import filter_for_provider


def pin_matches(
    pin_target: str | None,
    provider_id: Provider,
    persona_id: str | None = None,
) -> bool:
    """Return True if a pinned message targets the given persona/provider."""
    if not pin_target:
        return False
    if pin_target == "all":
        return True
    targets = {t.strip() for t in pin_target.split(",") if t.strip()}
    if persona_id and persona_id in targets:
        return True
    return provider_id.value in targets


def build_context(
    conv: Conversation,
    target: Provider | PersonaTarget,
    db: Database,
    config: Config,
) -> list[Message]:
    """Build the message list that will be sent to ``target`` for a new
    request on ``conv``.

    ``target`` may be either a ``Provider`` (legacy callers — back-compat
    shim until Stage 2.6 updates send_controller) or a ``PersonaTarget``
    (Stage 2.5+ callers). A bare Provider is treated as the synthetic
    default PersonaTarget for that provider (D1).

    Order of operations:
      1. Prepend system messages (persona override or provider default,
         plus conversation-wide).
      2. Apply //limit to slice off earlier history.
      3. Rescue pinned messages from before the cut-off whose pin_target
         covers this provider.
      4. Apply per-persona history cutoff (D6: if the persona has a
         non-null ``created_at_message_index``, drop messages before
         that index). Runs after //limit, so the two cutoffs stack.
      5. Apply visibility filtering (user-message addressing + per-
         observer matrix) to the limited slice. Pinned messages bypass.
      6. Strip provider-routing prefixes from user messages.
    """
    # Normalise target → (PersonaTarget, Persona|None). Persona is None
    # for synthetic defaults; the resolver helpers gracefully handle a
    # synthetic-shaped Persona so we build one for the shared path.
    if isinstance(target, Provider):
        persona_target = synthetic_default(target)
    else:
        persona_target = target

    provider = persona_target.provider
    persona = _load_persona(conv, persona_target, db)

    context: list[Message] = []

    # --- 1. System prompts ---
    parts: list[str] = []
    provider_prompt = resolve_persona_prompt(persona, config)
    if provider_prompt:
        parts.append(provider_prompt)
    # For explicit personas (persona_id != provider.value), skip the
    # global system prompt's multi-provider framing — the persona's
    # own prompt defines its role. Instead add a persona identity line.
    # For synthetic defaults, include the global prompt (legacy compat).
    is_explicit = persona_target.persona_id != persona_target.provider.value
    if is_explicit:
        parts.append(
            f"You are {persona.name}. Only respond as yourself — "
            f"do not include or generate responses for other personas."
        )
    elif conv.system_prompt:
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
            if m.pinned and pin_matches(m.pin_target, provider, persona_target.persona_id)
        ]

    # --- 4. Persona history cutoff (D6) ---
    # If the persona was added mid-chat with `new` mode, it should
    # only see messages from its join point onwards. This applies to
    # the post-limit slice — we drop any messages whose original index
    # in conv.messages is below created_at_message_index. Pinned
    # messages are NOT rescued across this cutoff: joining fresh means
    # fresh, pins from before the join aren't retroactively visible.
    if persona.created_at_message_index is not None:
        cutoff = persona.created_at_message_index
        # Rebuild the messages list keeping only entries whose original
        # position in conv.messages is >= cutoff.
        original_indices = {id(m): i for i, m in enumerate(conv.messages)}
        messages = [
            m for m in messages
            if original_indices.get(id(m), -1) >= cutoff
        ]
        # Pinned prefix is also filtered — a persona joining at index
        # 3 doesn't see a pin that was created at index 0.
        pinned_prefix = [
            m for m in pinned_prefix
            if original_indices.get(id(m), -1) >= cutoff
        ]

    # --- 5. Visibility filter (pins bypass) ---
    # Pass the PersonaTarget itself (not just the provider) so the
    # filter can key the matrix by persona_id. Synthetic defaults
    # collapse to provider.value naturally via D1.
    matrix = conv.visibility_matrix or {}
    messages = pinned_prefix + filter_for_provider(
        list(messages), persona_target, matrix,
    )

    # --- 6. Strip routing prefixes + relabel cross-persona messages ---
    # For explicit personas, assistant messages from other personas
    # (even on the same provider) are relabeled as user-role context
    # so the provider sees them as "someone else said this", not as
    # its own prior turns.
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
        elif msg.role == Role.ASSISTANT and is_explicit:
            msg_persona = msg.persona_id
            if msg_persona is None:
                # Legacy message with no persona_id — treat as own if
                # same provider (back-compat for pre-persona messages)
                is_own = (msg.provider == persona_target.provider)
            else:
                is_own = (msg_persona == persona_target.persona_id)
            if not is_own:
                # Cross-persona: relabel as user context with persona name
                label = msg_persona or "assistant"
                context.append(
                    Message(
                        role=Role.USER,
                        content=f"[{label} responded]: {msg.content}",
                        provider=msg.provider,
                        conversation_id=msg.conversation_id,
                        id=msg.id,
                    )
                )
            else:
                context.append(msg)
        else:
            context.append(msg)
    return context


def load_persona_for_target(
    conv: Conversation,
    target: PersonaTarget,
    db: Database,
) -> Persona:
    """Fetch the Persona row for a target, or synthesise one for the
    default case.

    For synthetic-default targets (persona_id == provider.value with no
    explicit row in the personas table), we construct a virtual Persona
    with all override fields None so the shared resolution helpers
    (D6b) can treat synthetic defaults and explicit inherit-everything
    personas identically.

    Used by context_builder and send_controller to load the persona
    row they need to call the D6b resolve_persona_* helpers.
    """
    # Try to find an explicit row first
    for p in db.list_personas_including_deleted(conv.id):
        if p.id == target.persona_id:
            return p
    # Fall through: synthetic default
    return Persona(
        conversation_id=conv.id if conv is not None else 0,
        id=target.persona_id,
        provider=target.provider,
        name=PROVIDER_META.get(target.provider.value, {}).get(
            "display", target.provider.value
        ),
        name_slug=target.provider.value,
    )


# Private alias for the build_context caller — kept to avoid touching
# the existing in-module caller while the public symbol is the new name.
_load_persona = load_persona_for_target


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
