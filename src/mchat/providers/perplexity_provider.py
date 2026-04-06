# ------------------------------------------------------------------
# Component: PerplexityProvider
# Responsibility: Perplexity Sonar API integration (OpenAI-compatible endpoint)
# Collaborators: providers.openai_compat
# ------------------------------------------------------------------
from __future__ import annotations

from mchat.models.message import Provider
from mchat.providers.openai_compat import OpenAICompatibleProvider


class PerplexityProvider(OpenAICompatibleProvider):
    _base_url = "https://api.perplexity.ai"
    _fallback_models = [
        "sonar-deep-research",
        "sonar-reasoning-pro",
        "sonar-pro",
        "sonar",
    ]

    def __init__(self, api_key: str, default_model: str = "sonar") -> None:
        super().__init__(api_key, default_model)

    @property
    def provider_id(self) -> Provider:
        return Provider.PERPLEXITY

    @property
    def display_name(self) -> str:
        return "Perplexity"

    def list_models(self) -> list[str]:
        # Perplexity does not have a models.list() endpoint
        return list(self._fallback_models)
