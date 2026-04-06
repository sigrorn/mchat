# ------------------------------------------------------------------
# Component: GeminiProvider
# Responsibility: Google Gemini API integration (OpenAI-compatible endpoint)
# Collaborators: providers.openai_compat
# ------------------------------------------------------------------
from __future__ import annotations

from mchat.models.message import Provider
from mchat.providers.openai_compat import OpenAICompatibleProvider


class GeminiProvider(OpenAICompatibleProvider):
    _base_url = "https://generativelanguage.googleapis.com/v1beta/openai/"
    _fallback_models = [
        "gemini-2.5-pro",
        "gemini-2.5-flash",
        "gemini-2.0-flash",
    ]

    def __init__(self, api_key: str, default_model: str = "gemini-2.5-flash") -> None:
        super().__init__(api_key, default_model)

    @property
    def provider_id(self) -> Provider:
        return Provider.GEMINI

    @property
    def display_name(self) -> str:
        return "Gemini"

    def _filter_model(self, model_id: str) -> bool:
        return "gemini" in model_id.lower()

    def _on_stream_done(self, api_messages: list[dict], full_text: str) -> None:
        """Gemini's OpenAI-compat endpoint may not return usage data.
        Fall back to a rough estimate (~4 chars per token)."""
        if self.last_usage is None:
            input_chars = sum(len(m["content"]) for m in api_messages)
            self.last_usage = (input_chars // 4, len(full_text) // 4)
            self.last_usage_estimated = True
