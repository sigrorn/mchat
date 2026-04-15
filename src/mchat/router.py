# ------------------------------------------------------------------
# Component: Router
# Responsibility: Parse user input prefixes and route to the correct provider
# Collaborators: providers.base, models.message
# ------------------------------------------------------------------
from __future__ import annotations

from mchat.models.message import Provider
from mchat.providers.base import BaseProvider

# #140: grammar switched from '<word>,<ws>' to '@<word> <ws>'.
# Router.parse and _strip_prefix both tokenise on whitespace and
# accept tokens starting with '@'.
ALL = "all"
OTHERS = "others"  # #140: renamed from 'flipped' — reads more naturally

PREFIX_TO_PROVIDER = {
    "claude": Provider.CLAUDE,
    "gpt": Provider.OPENAI,
    "openai": Provider.OPENAI,
    "gemini": Provider.GEMINI,
    "perplexity": Provider.PERPLEXITY,
    "pplx": Provider.PERPLEXITY,
    "mistral": Provider.MISTRAL,
    "apertus": Provider.APERTUS,
}

# Special prefixes that are not combinable with others
_SPECIAL_PREFIXES = {ALL, OTHERS}

# #140: legacy alias map used only by the context_builder strip path
# and by the one-shot migration. 'flipped' maps to 'others' for
# historical messages that still use the old keyword.
_LEGACY_ALIAS = {"flipped": OTHERS}


class Router:
    def __init__(
        self,
        providers: dict[Provider, BaseProvider],
        default: Provider = Provider.CLAUDE,
        selection_state=None,
    ) -> None:
        self._providers = providers
        # If the caller supplies a SelectionState (formerly
        # ProviderSelectionState), the selection lives there and
        # Router becomes a thin view. The state holds PersonaTargets
        # as of Stage 2.4; Router wraps providers as synthetic
        # defaults on write and unwraps via providers_only() on read,
        # so Router's public API stays list[Provider]-flavoured.
        # If no state is supplied (unit tests that only exercise
        # parsing), we fall back to a local list.
        self._selection_state = selection_state
        if selection_state is None:
            self._local_selection: list[Provider] = [default]
        # Stage 3A.4: an empty SelectionState is valid — new chats start
        # with zero providers selected (persona-first UX). We no longer
        # force-seed a synthetic default here.

    # ------------------------------------------------------------------
    # Selection access — delegates to the injected state object when
    # available, otherwise uses the local fallback. External callers
    # see a uniform list[Provider] interface either way.
    # ------------------------------------------------------------------

    @property
    def _selection(self) -> list[Provider]:
        if self._selection_state is not None:
            # SelectionState holds PersonaTargets as of Stage 2.4; we
            # unwrap to providers here so Router's public surface stays
            # list[Provider] and every existing caller keeps working.
            return self._selection_state.providers_only()
        return list(self._local_selection)

    def _store_selection(self, providers: list[Provider]) -> None:
        if self._selection_state is not None:
            # Wrap each provider as its synthetic-default PersonaTarget
            # before writing to the shared state. The state object is
            # the source of truth for PersonaResolver and SendController;
            # Router only ever writes through synthetic defaults, so
            # anything that needs real personas goes through the resolver.
            from mchat.ui.persona_target import synthetic_default
            targets = [synthetic_default(p) for p in providers]
            self._selection_state.set(targets)
        else:
            self._local_selection = list(providers)

    def parse(self, user_input: str) -> tuple[list[Provider], str]:
        """Parse user input, returning (target provider list, cleaned message).

        #140 grammar:
            @<provider> [<@provider> ...] <message>

        Walks whitespace-separated tokens from the start; every token
        beginning with '@' is a provider target. The first token not
        starting with '@' begins the message. Unknown '@' tokens are
        treated as plain text here (PersonaResolver raises for them
        at command level — Router.parse is lower-level and only
        recognises provider shorthands + @all/@others).

        ``@all`` and ``@others`` are special — not combinable with
        other @-prefixes. If either appears after a regular provider
        prefix, it's treated as message text starting at that token.
        """
        collected: list[Provider] = []
        offset = 0
        special: str | None = None

        while offset < len(user_input):
            # Skip whitespace at offset
            while offset < len(user_input) and user_input[offset].isspace():
                offset += 1
            if offset >= len(user_input):
                break

            tok_start = offset
            while offset < len(user_input) and not user_input[offset].isspace():
                offset += 1
            tok_end = offset
            token_full = user_input[tok_start:tok_end]

            if not token_full.startswith("@"):
                # First non-@ token — rewind to its start so the
                # message begins here with its original whitespace
                # trimmed later.
                offset = tok_start
                break

            token = token_full[1:].lower()
            if not token:
                # Lone '@' — treat as unknown at this level; message
                # starts here.
                offset = tok_start
                break

            # Special keywords (@all / @others) aren't combinable.
            if token in _SPECIAL_PREFIXES:
                if collected:
                    # Already collected provider prefixes — stop and
                    # treat the special keyword as message text.
                    offset = tok_start
                    break
                special = token
                # offset already past the keyword; break out.
                break

            # Regular provider shorthand?
            provider = PREFIX_TO_PROVIDER.get(token)
            if provider is None:
                # Unknown @ token — Router.parse is a lower-level
                # parser that doesn't know about personas, so it
                # treats unknown prefixes as plain text. PersonaResolver
                # raises ResolveError for this case at command level.
                offset = tok_start
                break

            if provider not in collected:
                collected.append(provider)

        message = user_input[offset:].strip()

        # Handle special @all / @others expansion
        if special is not None:
            if special == ALL:
                configured = [p for p in Provider if p in self._providers]
                if configured:
                    self._store_selection(configured)
            elif special == OTHERS:
                configured = set(p for p in Provider if p in self._providers)
                current = set(self._selection)
                others = [
                    p for p in Provider
                    if p in configured and p not in current
                ]
                if others and current != configured:
                    self._store_selection(others)
            return list(self._selection), message

        if collected:
            self._store_selection(collected)
            return list(self._selection), message

        # No prefix matched — return current selection + full input
        return list(self._selection), user_input

    def set_selection(self, providers: list[Provider]) -> None:
        self._store_selection(providers)

    @property
    def selection(self) -> list[Provider]:
        return list(self._selection)

    def get_provider(self, provider_id: Provider) -> BaseProvider:
        return self._providers[provider_id]

    @staticmethod
    def _strip_prefix(text: str) -> tuple[list[str], str]:
        """Strip @-prefix targets from text without changing any state.

        Returns ``(list of prefix names found, cleaned text)``. Used
        by context_builder to clean stored user messages before
        sending them to providers as context. #140: switched from
        the old ``<word>,<ws>`` grammar to ``@<word> <ws>``.

        Personas are addressed by name in the new grammar, but
        ``_strip_prefix`` doesn't have conversation context to
        resolve names — it only strips provider shorthands and
        ``@all`` / ``@others``. Tokens that don't match those
        are left in place as part of the cleaned text, which
        matches the behaviour needed by context_builder: a user
        message like ``@partner hi`` with an unknown-at-this-
        level ``partner`` gets stripped of its leading whitespace
        but not of ``@partner`` — safe, because context_builder
        uses this purely for display hygiene, not for routing.

        Also strips legacy ``flipped`` tokens (maps to ``others``
        in the returned list) for back-compat on messages that
        survived the migration without rewriting.
        """
        found: list[str] = []
        offset = 0
        consumed_any = False

        while offset < len(text):
            while offset < len(text) and text[offset].isspace():
                offset += 1
            if offset >= len(text):
                break

            tok_start = offset
            while offset < len(text) and not text[offset].isspace():
                offset += 1
            token_full = text[tok_start:offset]

            if not token_full.startswith("@"):
                offset = tok_start
                break

            token = token_full[1:].lower()
            if not token:
                offset = tok_start
                break

            # Legacy alias — 'flipped' only exists on old unmigrated
            # messages (if any); normalise to 'others' in the output.
            token = _LEGACY_ALIAS.get(token, token)

            if token in _SPECIAL_PREFIXES:
                if found:
                    # Special keywords not combinable — stop here and
                    # treat the keyword as text (rewind).
                    offset = tok_start
                    break
                found.append(token)
                consumed_any = True
                # offset already past it; break.
                break

            if token in PREFIX_TO_PROVIDER:
                found.append(token)
                consumed_any = True
                continue

            # Unknown @ token at strip level — leave it alone, stop.
            offset = tok_start
            break

        if not consumed_any:
            return [], text
        return found, text[offset:].strip()

    @property
    def last_used(self) -> Provider | list[Provider]:
        """For backward compat — returns single provider or list."""
        if len(self._selection) == 1:
            return self._selection[0]
        return list(self._selection)
