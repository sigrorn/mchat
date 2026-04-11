# ------------------------------------------------------------------
# Component: Persona
# Responsibility: Per-conversation persona identity — a named role
#                 backed by a Provider with its own system prompt,
#                 model, colour, and history scope. See
#                 docs/plans/personas.md for the full design.
# Collaborators: models.message, db
# ------------------------------------------------------------------
from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from datetime import datetime

from mchat.models.message import Provider


@dataclass
class Persona:
    """A named role in one conversation, backed by a Provider.

    Override fields (``system_prompt_override``, ``model_override``,
    ``color_override``) use null-means-inherit semantics: ``None``
    means "fall back to the provider-level default from config".
    See docs/plans/personas.md § D6.

    ``created_at_message_index`` is NOT an override field — it's a
    history-scope marker. ``None`` = the persona sees full history;
    an integer = the persona only sees messages with index ≥ that
    value (the "new" mode from ``//addpersona``).
    """

    conversation_id: int
    id: str                                    # opaque, stable forever
    provider: Provider                         # backing provider for API calls
    name: str                                  # display name ("Evaluator")
    name_slug: str                             # lowercased slug for prefix matching
    system_prompt_override: str | None = None  # None = inherit global provider prompt
    model_override: str | None = None          # None = inherit global provider model
    color_override: str | None = None          # None = inherit provider colour
    created_at_message_index: int | None = None  # None = full history
    sort_order: int = 0
    deleted_at: datetime | None = None         # tombstone marker (D3)


def generate_persona_id() -> str:
    """Return a fresh opaque persona id of the form ``p_<8 base36 chars>``.

    Opaque ids (as opposed to name-derived slugs) let the user rename
    a persona without breaking message linkage — the id stays stable
    forever, only ``name`` and ``name_slug`` change on rename. See
    docs/plans/personas.md § D2.
    """
    # 8 characters of base36 = 36**8 ≈ 2.8 trillion combinations, more
    # than enough to avoid collisions within a conversation without
    # needing a database lookup for uniqueness.
    alphabet = "0123456789abcdefghijklmnopqrstuvwxyz"
    return "p_" + "".join(secrets.choice(alphabet) for _ in range(8))


_SLUG_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def slugify_persona_name(name: str) -> str:
    """Return a prefix-matching slug for a persona name.

    Lowercased, non-alphanumeric runs collapsed to a single underscore,
    leading/trailing underscores stripped. Used for user-input prefix
    matching (``@partner`` → slug ``partner``).

    Raises ValueError if the resulting slug is empty, which the
    command layer should surface as an error to the user.

    #140: this helper stays back-compat — it still accepts names
    with whitespace or punctuation so grandfathered personas
    continue to produce usable slugs at read time. The write-path
    guard is ``validate_persona_name`` below, called explicitly
    before create/edit operations.
    """
    slug = _SLUG_NON_ALNUM.sub("_", name.strip().lower()).strip("_")
    if not slug:
        raise ValueError(f"persona name {name!r} produces an empty slug")
    return slug


# #140: allowed-character alphabet for new persona names. No
# whitespace, no punctuation other than '-' and '_', no '@'
# sigil. Matched as a complete string (^...$).
_VALID_NAME_RE = re.compile(r"^[A-Za-z0-9_\-]+$")


def validate_persona_name(name: str) -> None:
    """Validate a new persona name against the #140 rules.

    Raises ``ValueError`` with a human-readable message when:
      * the name is empty or whitespace-only
      * the name contains whitespace
      * the name contains characters other than ``[A-Za-z0-9_-]``
      * the name (lowercased) collides with a reserved token —
        a provider shorthand (``claude``, ``gpt``, ...) or one
        of the special keywords ``all`` / ``others``.

    Called only on **new write paths** (create, edit with name
    change, import pre-flight). Grandfathered personas in the DB
    are NOT re-validated — they bypass this function entirely,
    matching the grandfathering decision from the #140 plan.
    """
    if not name or not name.strip():
        raise ValueError("persona name cannot be empty")
    if name != name.strip():
        raise ValueError(
            f"persona name {name!r} has leading or trailing whitespace"
        )
    if any(ch.isspace() for ch in name):
        raise ValueError(
            f"persona name {name!r} contains whitespace — use '-' or "
            f"'_' as separators instead"
        )
    if "@" in name:
        raise ValueError(
            f"persona name {name!r} contains '@' — reserved for targeting"
        )
    if not _VALID_NAME_RE.fullmatch(name):
        raise ValueError(
            f"persona name {name!r} contains disallowed characters. "
            f"Allowed: letters, digits, '-', '_'."
        )
    # Reserved-name check — import here to avoid a circular import
    # with persona_resolver (which imports persona in turn via
    # PersonaTarget).
    from mchat.ui.persona_resolver import RESERVED_NAMES
    if name.lower() in RESERVED_NAMES:
        raise ValueError(
            f"persona name {name!r} is a reserved token. Reserved: "
            f"{', '.join(sorted(RESERVED_NAMES))}."
        )
