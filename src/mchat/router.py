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
    r"^(claude|gpt|both)\s*[,:]\s*",
    re.IGNORECASE,
)

BOTH = "both"

PREFIX_TO_PROVIDER = {
    "claude": Provider.CLAUDE,
    "gpt": Provider.OPENAI,
    "both": BOTH,
}


class Router:
    def __init__(self, providers: dict[Provider, BaseProvider], default: Provider = Provider.CLAUDE) -> None:
        self._providers = providers
        self._last_used = default

    def parse(self, user_input: str) -> tuple[Provider | str, str]:
        """Parse user input, returning (target provider or 'both', cleaned message)."""
        match = PREFIX_PATTERN.match(user_input)
        if match:
            prefix = match.group(1).lower()
            target = PREFIX_TO_PROVIDER[prefix]
            cleaned = user_input[match.end():].strip()
            if target != BOTH:
                self._last_used = target
            return target, cleaned
        return self._last_used, user_input

    def get_provider(self, provider_id: Provider) -> BaseProvider:
        return self._providers[provider_id]

    @property
    def last_used(self) -> Provider:
        return self._last_used
