# ------------------------------------------------------------------
# Component: PerplexityProvider
# Responsibility: Perplexity Sonar API integration (OpenAI-compatible endpoint)
# Collaborators: providers.base, openai SDK
# ------------------------------------------------------------------
from __future__ import annotations

from collections.abc import Iterator

from mchat.models.message import Message, Provider, Role
from mchat.providers.base import BaseProvider

FALLBACK_MODELS = [
    "sonar-deep-research",
    "sonar-reasoning-pro",
    "sonar-pro",
    "sonar",
]

_PERPLEXITY_BASE_URL = "https://api.perplexity.ai"


class PerplexityProvider(BaseProvider):
    def __init__(self, api_key: str, default_model: str = "sonar") -> None:
        super().__init__()
        self._api_key = api_key
        self._default_model = default_model
        self._client = None

    def _get_client(self):
        if self._client is None:
            import openai
            self._client = openai.OpenAI(
                api_key=self._api_key,
                base_url=_PERPLEXITY_BASE_URL,
            )
        return self._client

    @property
    def provider_id(self) -> Provider:
        return Provider.PERPLEXITY

    @property
    def display_name(self) -> str:
        return "Perplexity"

    def stream(self, messages: list[Message], model: str | None = None) -> Iterator[str]:
        self.last_usage = None
        api_messages = self._format_messages(messages)
        try:
            response = self._get_client().chat.completions.create(
                model=model or self._default_model,
                messages=api_messages,
                stream=True,
                stream_options={"include_usage": True},
            )
        except TypeError:
            response = self._get_client().chat.completions.create(
                model=model or self._default_model,
                messages=api_messages,
                stream=True,
            )
        for chunk in response:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content
            try:
                usage = chunk.usage
                if usage is not None:
                    self.last_usage = (
                        usage.prompt_tokens or 0,
                        usage.completion_tokens or 0,
                    )
            except AttributeError:
                pass

    def list_models(self) -> list[str]:
        # Perplexity does not have a models.list() endpoint
        return list(FALLBACK_MODELS)

    def _format_messages(self, messages: list[Message]) -> list[dict]:
        return self.format_messages_openai(messages, Provider.PERPLEXITY)
