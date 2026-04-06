# ------------------------------------------------------------------
# Component: PersonaResolver
# Responsibility: Map user input text to a list of PersonaTargets
#                 for the current conversation. Runs downstream of
#                 Router — Router stays pure (no conversation
#                 awareness); PersonaResolver handles the
#                 conversation-scoped custom names and the synthetic
#                 default rule (D1). The resolver is a pure function
#                 of (text, conv_id, db) — no Qt, no persistent state.
# Collaborators: router, db, models.persona, ui.persona_target
# ------------------------------------------------------------------
from __future__ import annotations

import re

from mchat.db import Database
from mchat.models.message import Provider
from mchat.router import PREFIX_TO_PROVIDER, Router
from mchat.ui.persona_target import PersonaTarget, synthetic_default

# Reserved keywords that cannot be used as persona names. Populated from
# the router's provider shorthands plus the two special keywords.
ALL = "all"
FLIPPED = "flipped"
RESERVED_NAMES: frozenset[str] = frozenset(
    {ALL, FLIPPED} | set(PREFIX_TO_PROVIDER.keys())
)

# One token of a prefix: a word followed by optional whitespace + "," or ":".
_PREFIX_TOKEN = re.compile(
    r"^\s*([A-Za-z][A-Za-z0-9_]*)\s*[,:]\s*",
)


class ResolveError(ValueError):
    """Raised when the resolver cannot resolve a prefix token —
    either the token is unknown (not a persona name, not a reserved
    keyword, not a provider shorthand) or the input is otherwise
    malformed. Commands surface the message to the user."""


class PersonaResolver:
    """Maps user input text to PersonaTargets using the current
    conversation's active persona list plus the global provider
    shorthand map.

    Held on MainWindow (or equivalent) and constructed once per
    Router. ``resolve(text, conv_id, db)`` is pure in the sense
    that it doesn't maintain any persistent state of its own —
    it reads the conversation's personas from the DB and writes
    any selection update through the router's selection state.
    """

    def __init__(self, router: Router) -> None:
        self._router = router

    def resolve(
        self,
        text: str,
        conv_id: int,
        db: Database,
    ) -> tuple[list[PersonaTarget], str]:
        """Parse prefixes from ``text`` and return
        ``(targets, cleaned_text)``.

        Resolution follows D1:
          1. Explicit persona name prefix → PersonaTarget for that
             persona (the persona's own id and provider).
          2. Provider shorthand prefix (``claude,`` etc.) → the
             synthetic default PersonaTarget for that provider,
             regardless of how many explicit same-provider personas
             exist in the chat.
          3. ``all,`` alone → every active persona plus synthetic
             defaults for providers that have no explicit personas.
          4. ``flipped,`` alone → the complement of the current
             selection over the same universe.
          5. No prefix → the current selection, expanded through
             synthetic defaults for provider-only selections.

        Unknown prefix tokens raise ``ResolveError`` — unlike the
        old Router.parse which silently let them fall through into
        the message text.
        """
        # Build a slug → persona map for this conversation. Only
        # active personas participate in name-prefix matching.
        slug_map = {
            p.name_slug: p for p in db.list_personas(conv_id)
        }

        remaining = text
        collected: list[PersonaTarget] = []
        seen_special: str | None = None

        while True:
            match = _PREFIX_TOKEN.match(remaining)
            if not match:
                break

            token_raw = match.group(1)
            token = token_raw.lower()

            # Special keywords (all/flipped) are not combinable with
            # other prefixes — if we hit one, it's the only thing.
            if token in (ALL, FLIPPED):
                if collected:
                    # Mixed with other prefixes — treat as message text,
                    # stop parsing. This mirrors the old Router.parse
                    # behaviour.
                    break
                seen_special = token
                remaining = remaining[match.end():]
                break

            # Explicit persona name?
            if token in slug_map:
                persona = slug_map[token]
                target = PersonaTarget(
                    persona_id=persona.id,
                    provider=persona.provider,
                )
                if target not in collected:
                    collected.append(target)
                remaining = remaining[match.end():]
                continue

            # Provider shorthand? If the conversation has explicit personas
            # on this provider, resolve to all of them; otherwise fall back
            # to the synthetic default (Stage 4.3).
            if token in PREFIX_TO_PROVIDER:
                provider = PREFIX_TO_PROVIDER[token]
                provider_personas = [
                    p for p in slug_map.values() if p.provider == provider
                ]
                if provider_personas:
                    for p in provider_personas:
                        t = PersonaTarget(persona_id=p.id, provider=p.provider)
                        if t not in collected:
                            collected.append(t)
                else:
                    target = synthetic_default(provider)
                    if target not in collected:
                        collected.append(target)
                remaining = remaining[match.end():]
                continue

            # Unknown token → the entire resolve fails.
            raise ResolveError(
                f"Unknown persona or provider: {token_raw!r}. "
                f"Known provider shorthands: "
                f"{', '.join(sorted(PREFIX_TO_PROVIDER.keys()))}. "
                f"Active personas: "
                f"{', '.join(sorted(slug_map.keys())) or '(none)'}."
            )

        # Handle all, / flipped, expansion (if no collected yet)
        if seen_special is not None:
            targets = self._expand_special(seen_special, conv_id, db)
            self._write_selection(targets)
            return targets, remaining.strip()

        if collected:
            self._write_selection(collected)
            return collected, remaining.strip()

        # No prefix at all — use current selection directly. The
        # SelectionState holds the real PersonaTargets (explicit personas
        # or synthetic defaults), not the provider-collapsed view.
        if self._router._selection_state is not None:
            targets = list(self._router._selection_state.selection)
        else:
            targets = [synthetic_default(p) for p in self._router.selection]
        return targets, text

    def _expand_special(
        self, special: str, conv_id: int, db: Database,
    ) -> list[PersonaTarget]:
        """Expand an ``all,`` or ``flipped,`` prefix into a list of
        PersonaTargets over the conversation's active personas only.
        No synthetic defaults for providers without explicit personas.
        """
        active = db.list_personas(conv_id)

        # Build the universe from explicit personas only, ordered by
        # Provider enum declaration order for stability.
        universe: list[PersonaTarget] = []
        for provider in Provider:
            for p in active:
                if p.provider == provider:
                    universe.append(
                        PersonaTarget(persona_id=p.id, provider=p.provider)
                    )

        if special == ALL:
            return universe

        # flipped: complement of current selection at persona level
        current_targets = set(self._router._selection_state.selection)
        return [t for t in universe if t not in current_targets]

    def _write_selection(self, targets: list[PersonaTarget]) -> None:
        """Write the full PersonaTarget list to the selection state.

        Stage 4.2: we no longer collapse to providers — the selection
        preserves persona-level granularity so flipped, +/-, and the
        visibility matrix all work at persona level.
        """
        if targets:
            self._router._selection_state.set(targets)
