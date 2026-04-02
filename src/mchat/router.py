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
    def __init__(self, providers: dict[Provider, BaseProvider], default: Provider = Provider.CLAUDE) -> None:
        self._providers = providers
        self._selection: list[Provider] = [default]

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
                        self._selection = configured
                elif prefix == FLIPPED:
                    configured = set(p for p in Provider if p in self._providers)
                    current = set(self._selection)
                    flipped = [p for p in Provider if p in configured and p not in current]
                    if flipped and current != configured:
                        self._selection = flipped
                return list(self._selection), cleaned

            # Regular provider prefix
            provider = PREFIX_TO_PROVIDER[prefix]
            if provider not in collected:
                collected.append(provider)
            remaining = remaining[match.end():]

        if collected:
            message = remaining.strip()
            self._selection = collected
            return list(self._selection), message

        # No prefix matched — use current selection
        return list(self._selection), user_input

    def set_selection(self, providers: list[Provider]) -> None:
        if providers:
            self._selection = list(providers)

    @property
    def selection(self) -> list[Provider]:
        return list(self._selection)

    def get_provider(self, provider_id: Provider) -> BaseProvider:
        return self._providers[provider_id]

    @property
    def last_used(self) -> Provider | list[Provider]:
        """For backward compat — returns single provider or list."""
        if len(self._selection) == 1:
            return self._selection[0]
        return list(self._selection)
