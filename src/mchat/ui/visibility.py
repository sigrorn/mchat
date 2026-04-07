# ------------------------------------------------------------------
# Component: visibility
# Responsibility: Pure filter that decides which messages a given
#                 target (persona or provider) is allowed to see when
#                 context is built for a new request.
#
#                 Stage 2.7 generalised this to accept PersonaTargets
#                 and key the matrix by persona_id. Legacy matrices
#                 (keyed by provider values) naturally apply to
#                 synthetic default personas via D1 — the synthetic
#                 default's persona_id equals provider.value. Explicit
#                 same-provider personas start with full visibility
#                 (D5), falling through to the "no entry → full
#                 visibility" branch.
# Collaborators: models.message, ui.persona_target
# ------------------------------------------------------------------
from __future__ import annotations

from mchat.models.message import Message, Provider, Role
from mchat.ui.persona_target import PersonaTarget, synthetic_default


def filter_for_provider(
    messages: list[Message],
    target: Provider | PersonaTarget,
    matrix: dict[str, list[str]],
) -> list[Message]:
    """Return the subset of ``messages`` that ``target`` is allowed to see.

    Rules:
      * SYSTEM messages always pass through.
      * USER messages are filtered by ``addressed_to``:
          - ``None`` (legacy) or ``"all"`` → visible to all targets.
          - Otherwise a comma-separated list of tokens. A token
            matches if it equals the target's ``persona_id`` or
            the target's ``provider.value`` — the second condition
            covers the case where the synthetic default receives a
            legacy-addressed message and also where an explicit
            persona sees a message explicitly addressed to it by id.
      * ASSISTANT messages are filtered by the visibility ``matrix``:
          - Matrix is keyed by observer ``persona_id``. If the target's
            persona_id has no entry, the observer has full visibility
            (the default for new personas, D5).
          - Otherwise the entry is an allowlist of source identifiers.
            A message's source identifier is its ``persona_id`` (if
            set) or its ``provider.value`` (legacy). A source is
            visible if it appears in the allowlist or if it equals
            the target's own persona_id (an observer always sees
            its own responses).

    The ``target`` parameter accepts either a ``Provider`` enum
    member (legacy callers) or a ``PersonaTarget``. Providers are
    wrapped as their synthetic default PersonaTarget at the
    boundary, so every downstream branch operates on a uniform
    PersonaTarget shape.
    """
    # Normalise target → PersonaTarget
    if isinstance(target, Provider):
        target_pt = synthetic_default(target)
    else:
        target_pt = target

    target_persona_id = target_pt.persona_id
    target_provider_value = target_pt.provider.value

    matrix_row = matrix.get(target_persona_id)  # None = full visibility

    out: list[Message] = []
    for msg in messages:
        if msg.role == Role.USER:
            # Pinned messages with a pin_target must only be visible
            # to the targeted provider(s), not to everyone.
            if msg.pinned and msg.pin_target and msg.pin_target != "all":
                pin_targets = {
                    t.strip() for t in msg.pin_target.split(",") if t.strip()
                }
                # Match by persona_id first, then by provider.value
                # (back-compat for pins created before persona_id targeting)
                if (target_persona_id not in pin_targets
                        and target_provider_value not in pin_targets):
                    continue  # skip: this pin isn't for us

            addressed = msg.addressed_to
            if addressed is None or addressed == "all":
                out.append(msg)
                continue
            tokens = {t.strip() for t in addressed.split(",") if t.strip()}
            # Token matches if it's either the persona_id or the
            # provider.value. The synthetic default case collapses
            # both to provider.value; for explicit personas, only
            # the opaque id matches (D5 — legacy provider-value
            # tokens don't reach explicit personas).
            if target_persona_id in tokens:
                out.append(msg)
            elif (
                target_persona_id == target_provider_value
                and target_provider_value in tokens
            ):
                # Synthetic default: also accept provider-value tokens
                # (this is the same check as above since persona_id ==
                # provider.value, but kept explicit for clarity).
                out.append(msg)
        elif msg.role == Role.ASSISTANT:
            if matrix_row is None:
                out.append(msg)
                continue
            # Resolve the source identifier for this assistant message.
            msg_source = msg.persona_id or (
                msg.provider.value if msg.provider else None
            )
            if msg_source is None:
                out.append(msg)
                continue
            # Observer always sees its own responses
            if msg_source == target_persona_id:
                out.append(msg)
                continue
            if msg_source in matrix_row:
                out.append(msg)
        else:  # SYSTEM and anything else
            out.append(msg)
    return out
