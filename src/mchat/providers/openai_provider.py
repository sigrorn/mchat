# ------------------------------------------------------------------
# Component: OpenAIProvider
# Responsibility: OpenAI ChatGPT API integration
# Collaborators: providers.openai_compat
# ------------------------------------------------------------------
from __future__ import annotations

import re

from mchat.models.message import Provider
from mchat.providers.openai_compat import OpenAICompatibleProvider

_CHAT_PREFIXES = re.compile(r"^(gpt-|o\d|chatgpt-)")


class OpenAIProvider(OpenAICompatibleProvider):
    _fallback_models = [
        "o3",
        "o3-mini",
        "gpt-4.1",
        "gpt-4.1-mini",
        "gpt-4.1-nano",
        "gpt-4o",
        "gpt-4o-mini",
    ]

    def __init__(self, api_key: str, default_model: str = "gpt-4.1") -> None:
        super().__init__(api_key, default_model)

    @property
    def provider_id(self) -> Provider:
        return Provider.OPENAI

    @property
    def display_name(self) -> str:
        return "ChatGPT"

    def _filter_model(self, model_id: str) -> bool:
        return bool(_CHAT_PREFIXES.match(model_id))
