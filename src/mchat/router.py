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
    r"^(claude|gpt)\s*[,:]\s*",
    re.IGNORECASE,
)

PREFIX_TO_PROVIDER = {
    "claude": Provider.CLAUDE,
    "gpt": Provider.OPENAI,
}


class Router:
    def __init__(self, providers: dict[Provider, BaseProvider], default: Provider = Provider.CLAUDE) -> None:
        self._providers = providers
        self._last_used = default

    def parse(self, user_input: str) -> tuple[Provider, str]:
        """Parse user input, returning (target provider, cleaned message)."""
        match = PREFIX_PATTERN.match(user_input)
        if match:
            prefix = match.group(1).lower()
            provider = PREFIX_TO_PROVIDER[prefix]
            cleaned = user_input[match.end():].strip()
            self._last_used = provider
            return provider, cleaned
        return self._last_used, user_input

    def get_provider(self, provider_id: Provider) -> BaseProvider:
        return self._providers[provider_id]

    @property
    def last_used(self) -> Provider:
        return self._last_used
