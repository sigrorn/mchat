# ------------------------------------------------------------------
# Component: visibility
# Responsibility: Pure filter that decides which messages a given
#                 target provider is allowed to see when context is
#                 built for a new request.
# Collaborators: models.message
# ------------------------------------------------------------------
from __future__ import annotations

from mchat.models.message import Message, Provider, Role


def filter_for_provider(
    messages: list[Message],
    target: Provider,
    matrix: dict[str, list[str]],
) -> list[Message]:
    """Return the subset of ``messages`` that ``target`` is allowed to see.

    Rules:
      * SYSTEM messages always pass through.
      * USER messages are filtered by ``addressed_to``:
          - ``None`` (legacy) or ``"all"`` → visible to all providers
          - comma-separated provider values → visible only to those listed
      * ASSISTANT messages are filtered by the visibility ``matrix``:
          - If ``target`` has no entry in the matrix → full visibility
            (this is the default for new conversations).
          - Otherwise the entry is an allowlist of source provider values.
            ``target`` always sees its own responses regardless of the
            allowlist.
    """
    matrix_row = matrix.get(target.value)  # None = full visibility
    out: list[Message] = []
    for msg in messages:
        if msg.role == Role.USER:
            addressed = msg.addressed_to
            if addressed is None or addressed == "all":
                out.append(msg)
                continue
            targets = {t.strip() for t in addressed.split(",") if t.strip()}
            if target.value in targets:
                out.append(msg)
        elif msg.role == Role.ASSISTANT:
            if matrix_row is None:
                out.append(msg)
                continue
            if msg.provider is None:
                out.append(msg)
                continue
            if msg.provider == target or msg.provider.value in matrix_row:
                out.append(msg)
        else:  # SYSTEM and anything else
            out.append(msg)
    return out
