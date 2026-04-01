# ------------------------------------------------------------------
# Component: Router
# Responsibility: Parse user input prefixes and route to the correct provider
# Collaborators: providers.base, models.message
# ------------------------------------------------------------------
from __future__ import annotations

import re

from mchat.models.message import Provider
from mchat.providers.base import BaseProvider

PREFIX_PATTERN = re.compile(
    r"^(claude|gpt|gemini|perplexity|pplx)\s*[,:]\s*",
    re.IGNORECASE,
)

PREFIX_TO_PROVIDER = {
    "claude": Provider.CLAUDE,
    "gpt": Provider.OPENAI,
    "gemini": Provider.GEMINI,
    "perplexity": Provider.PERPLEXITY,
    "pplx": Provider.PERPLEXITY,
}


class Router:
    def __init__(self, providers: dict[Provider, BaseProvider], default: Provider = Provider.CLAUDE) -> None:
        self._providers = providers
        self._selection: list[Provider] = [default]

    def parse(self, user_input: str) -> tuple[list[Provider], str]:
        """Parse user input, returning (target provider list, cleaned message).

        A provider prefix like ``claude,`` switches selection to that single
        provider (sticky).  Without a prefix the current selection is used.
        """
        match = PREFIX_PATTERN.match(user_input)
        if match:
            prefix = match.group(1).lower()
            provider = PREFIX_TO_PROVIDER[prefix]
            cleaned = user_input[match.end():].strip()
            self._selection = [provider]
            return list(self._selection), cleaned
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
