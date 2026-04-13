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

from mchat.db import Database
from mchat.models.message import Provider
from mchat.router import PREFIX_TO_PROVIDER, Router
from mchat.ui.persona_target import PersonaTarget, synthetic_default

# Reserved keywords that cannot be used as persona names. Populated from
# the router's provider shorthands plus the two special keywords.
# #140: 'flipped' renamed to 'others' — reads more naturally ('at the
# others' vs 'at flipped').
ALL = "all"
OTHERS = "others"
RESERVED_NAMES: frozenset[str] = frozenset(
    {ALL, OTHERS} | set(PREFIX_TO_PROVIDER.keys())
)


from dataclasses import dataclass
from enum import Enum


class ResolveMode(Enum):
    EXPLICIT = "explicit"           # @name or @provider prefix(es)
    ALL = "all"                     # @all
    OTHERS = "others"              # @others
    IMPLICIT_SELECTION = "implicit" # no @ prefix, uses checkbox selection
    RETRY = "retry"                # //retry — parallel, no DAG, no run_id change


@dataclass
class ResolveResult:
    targets: list
    cleaned_text: str
    mode: ResolveMode


class ResolveError(ValueError):
    """Raised when the resolver cannot resolve an @-prefix token —
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

    #140: grammar switched from '<word>,<whitespace>' to
    '@<word> <whitespace>' — explicit sigil eliminates false
    positives on natural English ('ok, but...' used to error).
    """

    def __init__(self, router: Router) -> None:
        self._router = router
        self.last_resolve_mode: ResolveMode = ResolveMode.IMPLICIT_SELECTION

    def resolve(
        self,
        text: str,
        conv_id: int,
        db: Database,
    ) -> tuple[list[PersonaTarget], str]:
        """Parse @-prefix targets from ``text`` and return
        ``(targets, cleaned_text)``.

        Grammar:
            <input>  ::= <target>* <prompt>
            <target> ::= '@' <token> <whitespace>+
            <token>  ::= persona-name | provider-shorthand | 'all' | 'others'
            <prompt> ::= everything from the first whitespace-
                         separated word that does NOT start with '@'

        Resolution rules (D1, preserved from the old grammar):
          1. Explicit persona name (via slug lookup, case-insensitive)
             → PersonaTarget for that persona.
          2. Provider shorthand (``@claude``, ``@gpt``, ...) →
             synthetic default for that provider, regardless of how
             many explicit same-provider personas exist.
          3. ``@all`` alone → every active persona plus synthetic
             defaults for providers that have no explicit personas.
          4. ``@others`` alone → the complement of the current
             selection over the same universe.
          5. No ``@`` prefix → the current selection.

        Grandfathered behaviour: if a pre-existing persona has a
        reserved name (e.g. ``claude``), rule 1 still wins — the
        slug_map lookup happens before the provider-shorthand
        fallback. New personas can't be created with reserved
        names (Phase 2 validator), but existing rows are kept
        intact (no migration rename).

        Unknown @-prefix tokens raise ``ResolveError``. ``@all`` /
        ``@others`` are not combinable with other @-prefixes — if
        mixed, the parser stops at the special keyword and the rest
        (including that keyword) becomes message text.
        """
        # Build a slug → persona map for this conversation. Only
        # active personas participate in name-prefix matching.
        slug_map = {
            p.name_slug: p for p in db.list_personas(conv_id)
        }

        # Tokenise on whitespace while preserving the original
        # whitespace between tokens in the unparsed tail. We walk
        # the input with a running offset so we can recover the
        # exact remaining text after the last consumed @-target.
        stripped_text = text.lstrip()
        leading_ws_len = len(text) - len(stripped_text)
        offset = leading_ws_len  # absolute position in the original text

        collected: list[PersonaTarget] = []
        seen_special: str | None = None

        while offset < len(text):
            # Find the next token starting at offset.
            # Skip any leading whitespace at the current position.
            while offset < len(text) and text[offset].isspace():
                offset += 1
            if offset >= len(text):
                break

            # Measure this token's length (up to next whitespace).
            tok_start = offset
            while offset < len(text) and not text[offset].isspace():
                offset += 1
            tok_end = offset
            token_full = text[tok_start:tok_end]

            # If the token doesn't start with '@', we've hit the
            # prompt. Rewind offset so the prompt starts at tok_start
            # (with its original leading whitespace) rather than
            # swallowing the word.
            if not token_full.startswith("@"):
                offset = tok_start
                break

            # Strip the '@' sigil and lowercase for matching.
            token_raw = token_full[1:]
            if not token_raw:
                # Lone '@' — treat as unknown token so the user
                # gets a clear error (vs silently falling through
                # as text).
                raise ResolveError(
                    "Empty @ target. Use @<persona>, @<provider>, "
                    "@all, or @others."
                )
            token = token_raw.lower()

            # Special keywords (@all / @others) are not combinable
            # with other prefixes. If we hit one, stop — and if we've
            # already collected other targets, treat the keyword as
            # message text (rewind to tok_start).
            if token in (ALL, OTHERS):
                if collected:
                    offset = tok_start
                    break
                seen_special = token
                # offset is already past the keyword; break so we
                # pick up the rest as message text.
                break

            # Explicit persona name? (Grandfathering entry point —
            # a persona with a reserved name is still resolved here
            # before the provider-shorthand fallback.)
            if token in slug_map:
                persona = slug_map[token]
                target = PersonaTarget(
                    persona_id=persona.id,
                    provider=persona.provider,
                )
                if target not in collected:
                    collected.append(target)
                continue

            # Provider shorthand — always resolves to the synthetic default.
            # Personas are addressed by name, not by provider shorthand.
            if token in PREFIX_TO_PROVIDER:
                provider = PREFIX_TO_PROVIDER[token]
                target = synthetic_default(provider)
                if target not in collected:
                    collected.append(target)
                continue

            # Unknown token → the entire resolve fails.
            raise ResolveError(
                f"Unknown @ target: {token_full!r}. "
                f"Known provider shorthands: "
                f"{', '.join('@' + k for k in sorted(PREFIX_TO_PROVIDER.keys()))}. "
                f"Special keywords: @all, @others. "
                f"Active personas: "
                f"{', '.join('@' + k for k in sorted(slug_map.keys())) or '(none)'}."
            )

        # `offset` now points at the start of the prompt (or past the
        # end of the input). Extract the tail.
        remaining = text[offset:].strip()

        # Handle @all / @others expansion (if no collected yet)
        if seen_special is not None:
            targets = self._expand_special(seen_special, conv_id, db)
            self._write_selection(targets)
            self.last_resolve_mode = (
                ResolveMode.ALL if seen_special == ALL else ResolveMode.OTHERS
            )
            return targets, remaining

        if collected:
            self._write_selection(collected)
            self.last_resolve_mode = ResolveMode.EXPLICIT
            return collected, remaining

        # No @-prefix at all — use current selection directly. The
        # SelectionState holds the real PersonaTargets (explicit personas
        # or synthetic defaults), not the provider-collapsed view.
        if self._router._selection_state is not None:
            targets = list(self._router._selection_state.selection)
        else:
            targets = [synthetic_default(p) for p in self._router.selection]
        self.last_resolve_mode = ResolveMode.IMPLICIT_SELECTION
        return targets, text

    def _expand_special(
        self, special: str, conv_id: int, db: Database,
    ) -> list[PersonaTarget]:
        """Expand an ``@all`` or ``@others`` prefix into a list of
        PersonaTargets. If the conversation has explicit personas, use
        only those. If it has none, fall back to synthetic defaults for
        all configured providers (legacy compat, #107).
        """
        active = db.list_personas(conv_id)

        universe: list[PersonaTarget] = []
        if active:
            # Explicit personas only, ordered by Provider enum for stability
            for provider in Provider:
                for p in active:
                    if p.provider == provider:
                        universe.append(
                            PersonaTarget(persona_id=p.id, provider=p.provider)
                        )
        else:
            # No personas — fall back to synthetic defaults for configured providers
            configured = set(self._router._providers.keys()) if self._router else set()
            for provider in Provider:
                if provider in configured:
                    universe.append(synthetic_default(provider))

        if special == ALL:
            return universe

        # @others: complement of current selection at persona level
        current_targets = set(self._router._selection_state.selection)
        return [t for t in universe if t not in current_targets]

    def _write_selection(self, targets: list[PersonaTarget]) -> None:
        """Write the full PersonaTarget list to the selection state.

        Stage 4.2: we no longer collapse to providers — the selection
        preserves persona-level granularity so @others, +/-, and the
        visibility matrix all work at persona level.
        """
        if targets:
            self._router._selection_state.set(targets)
