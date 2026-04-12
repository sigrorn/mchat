# ------------------------------------------------------------------
# Component: ApertusProvider
# Responsibility: Apertus (swiss-ai) integration via Infomaniak's
#                 OpenAI-compatible endpoint. The base URL includes a
#                 user-specific product_id.
# Collaborators: providers.openai_compat
# ------------------------------------------------------------------
from __future__ import annotations

from mchat.models.message import Provider
from mchat.providers.openai_compat import OpenAICompatibleProvider


class ApertusProvider(OpenAICompatibleProvider):
    _fallback_models = [
        "swiss-ai/Apertus-70B-Instruct-2509",
    ]

    def __init__(
        self,
        api_key: str,
        product_id: str,
        default_model: str = "swiss-ai/Apertus-70B-Instruct-2509",
    ) -> None:
        super().__init__(api_key, default_model)
        self._base_url = (
            f"https://api.infomaniak.com/2/ai/{product_id}/openai/v1/"
        )

    @property
    def provider_id(self) -> Provider:
        return Provider.APERTUS

    @property
    def display_name(self) -> str:
        return "Apertus"
