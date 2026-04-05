# ------------------------------------------------------------------
# Component: Router
# Responsibility: Parse user input prefixes and route to the correct provider
# Collaborators: providers.base, models.message
# ------------------------------------------------------------------
from __future__ import annotations

import re

from mchat.models.message import Provider
from mchat.providers.base import BaseProvider

# Single-word prefix pattern (used for iterative parsing)
_WORD_PREFIX = re.compile(
    r"^(claude|gpt|gemini|perplexity|pplx|all|flipped)\s*[,:]\s*",
    re.IGNORECASE,
)

ALL = "all"
FLIPPED = "flipped"

PREFIX_TO_PROVIDER = {
    "claude": Provider.CLAUDE,
    "gpt": Provider.OPENAI,
    "gemini": Provider.GEMINI,
    "perplexity": Provider.PERPLEXITY,
    "pplx": Provider.PERPLEXITY,
}

# Special prefixes that are not combinable with others
_SPECIAL_PREFIXES = {ALL, FLIPPED}


class Router:
    def __init__(
        self,
        providers: dict[Provider, BaseProvider],
        default: Provider = Provider.CLAUDE,
        selection_state=None,
    ) -> None:
        self._providers = providers
        # If the caller supplies a ProviderSelectionState, selection
        # lives there and Router becomes a thin view over it. If no
        # state is supplied (e.g. unit tests that only exercise parsing),
        # we fall back to a local list so Router stays self-contained.
        self._selection_state = selection_state
        if selection_state is None:
            self._local_selection: list[Provider] = [default]
        else:
            if not selection_state.selection:
                selection_state.set([default])

    # ------------------------------------------------------------------
    # Selection access — delegates to the injected state object when
    # available, otherwise uses the local fallback. External callers
    # see a uniform list[Provider] interface either way.
    # ------------------------------------------------------------------

    @property
    def _selection(self) -> list[Provider]:
        if self._selection_state is not None:
            return self._selection_state.selection
        return list(self._local_selection)

    def _store_selection(self, providers: list[Provider]) -> None:
        if not providers:
            return
        if self._selection_state is not None:
            self._selection_state.set(providers)
        else:
            self._local_selection = list(providers)

    def parse(self, user_input: str) -> tuple[list[Provider], str]:
        """Parse user input, returning (target provider list, cleaned message).

        Supports multiple provider prefixes:
            ``claude, gemini, what's your take?``
        Parses provider names from the start until a non-provider word,
        then everything after is the message.

        ``all,`` and ``flipped,`` are special — not combinable with others.
        """
        remaining = user_input
        collected: list[Provider] = []

        # Try to match one or more provider prefixes
        while True:
            match = _WORD_PREFIX.match(remaining)
            if not match:
                break
            prefix = match.group(1).lower()

            # Special prefixes: handle alone, stop parsing
            if prefix in _SPECIAL_PREFIXES:
                if collected:
                    # Hit 'all'/'flipped' after real providers — stop,
                    # treat 'all'/'flipped' as part of the message
                    break
                cleaned = remaining[match.end():].strip()
                if prefix == ALL:
                    configured = [p for p in Provider if p in self._providers]
                    if configured:
                        self._store_selection(configured)
                elif prefix == FLIPPED:
                    configured = set(p for p in Provider if p in self._providers)
                    current = set(self._selection)
                    flipped = [p for p in Provider if p in configured and p not in current]
                    if flipped and current != configured:
                        self._store_selection(flipped)
                return list(self._selection), cleaned

            # Regular provider prefix
            provider = PREFIX_TO_PROVIDER[prefix]
            if provider not in collected:
                collected.append(provider)
            remaining = remaining[match.end():]

        if collected:
            message = remaining.strip()
            self._store_selection(collected)
            return list(self._selection), message

        # No prefix matched — use current selection
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
        """Strip provider prefixes from text without changing any state.

        Returns (list of prefix names found, cleaned text).
        """
        remaining = text
        found: list[str] = []
        while True:
            match = _WORD_PREFIX.match(remaining)
            if not match:
                break
            prefix = match.group(1).lower()
            if prefix in _SPECIAL_PREFIXES:
                if found:
                    break
                found.append(prefix)
                remaining = remaining[match.end():]
                break
            prov = PREFIX_TO_PROVIDER.get(prefix)
            if prov:
                found.append(prefix)
            remaining = remaining[match.end():]
        return found, remaining.strip() if found else text

    @property
    def last_used(self) -> Provider | list[Provider]:
        """For backward compat — returns single provider or list."""
        if len(self._selection) == 1:
            return self._selection[0]
        return list(self._selection)
