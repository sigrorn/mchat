# ------------------------------------------------------------------
# Component: persona_target
# Responsibility: PersonaTarget frozen dataclass + synthetic_default
#                 helper (D1/D4). Every routing, selection, and
#                 send-flow code path uses PersonaTarget as its
#                 identity primitive instead of bare Provider enum
#                 members so same-provider personas can coexist.
# Collaborators: models.message (Provider)
# ------------------------------------------------------------------
from __future__ import annotations

from dataclasses import dataclass

from mchat.models.message import Provider


@dataclass(frozen=True)
class PersonaTarget:
    """Immutable routing target: a persona id plus its backing provider.

    ``persona_id`` is the identity used for selection state, visibility
    matrix keying, context building, and message storage. ``provider``
    is the concrete backing provider used for the actual API call.
    Frozen so instances can live in sets and dict keys (needed by the
    resolver and visibility filter).

    For synthetic default personas (D1), ``persona_id`` equals
    ``provider.value`` as a deliberate exception to the opaque-id
    convention — this lets legacy messages with ``persona_id=None``
    reach the same downstream code paths by resolving through the
    synthetic default for their provider.
    """

    persona_id: str
    provider: Provider


def synthetic_default(provider: Provider) -> PersonaTarget:
    """Return the PersonaTarget for a provider's synthetic default persona.

    Synthetic defaults are not stored in the personas table — they
    exist virtually as the target that provider-shorthand prefixes
    (``claude,``, ``gpt,``, ...) resolve to. Every conversation has
    one synthetic default per provider, even chats that also have
    explicit same-provider personas; D1 guarantees that
    ``claude,`` always resolves to the synthetic default and never
    becomes ambiguous.
    """
    return PersonaTarget(persona_id=provider.value, provider=provider)
