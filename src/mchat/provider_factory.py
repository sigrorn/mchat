# ------------------------------------------------------------------
# Component: provider_factory
# Responsibility: Build provider instances from config. Replaces the
#                 hand-written _init_providers block in MainWindow
#                 (#164). Iterates PROVIDER_META to construct each
#                 provider that has a non-empty API key.
# Collaborators: config, providers.*, models.message.Provider
# ------------------------------------------------------------------
from __future__ import annotations

from mchat.config import Config, PROVIDER_META
from mchat.models.message import Provider
from mchat.providers.base import BaseProvider

# Lazy import map: provider value → (module_path, class_name).
# Using lazy imports so the factory module doesn't eagerly pull in
# every SDK (Mistral, Anthropic, etc.) at import time.
_PROVIDER_CLASSES: dict[str, tuple[str, str]] = {
    "claude": ("mchat.providers.claude", "ClaudeProvider"),
    "openai": ("mchat.providers.openai_provider", "OpenAIProvider"),
    "gemini": ("mchat.providers.gemini_provider", "GeminiProvider"),
    "perplexity": ("mchat.providers.perplexity_provider", "PerplexityProvider"),
    "mistral": ("mchat.providers.mistral_provider", "MistralProvider"),
    "apertus": ("mchat.providers.apertus_provider", "ApertusProvider"),
}


def build_providers(config: Config) -> dict[Provider, BaseProvider]:
    """Build provider instances from config.

    For each provider in PROVIDER_META, reads the API key from config.
    If non-empty, constructs the provider class with api_key +
    default_model + any extra kwargs from the meta's extra_config_keys.
    """
    import importlib

    providers: dict[Provider, BaseProvider] = {}

    for pv, meta in PROVIDER_META.items():
        api_key = config.get(meta["api_key"])
        if not api_key:
            continue

        # Extra constructor kwargs (e.g. product_id for Apertus).
        extra_kwargs: dict[str, str] = {}
        for kwarg_name, config_key in meta.get("extra_config_keys", {}).items():
            val = config.get(config_key)
            if not val:
                # Required extra kwarg is empty — skip this provider.
                extra_kwargs = None
                break
            extra_kwargs[kwarg_name] = val

        if extra_kwargs is None:
            continue

        class_info = _PROVIDER_CLASSES.get(pv)
        if class_info is None:
            continue

        module_path, class_name = class_info
        mod = importlib.import_module(module_path)
        cls = getattr(mod, class_name)

        provider_enum = Provider(pv)
        default_model = config.get(meta["model_key"])
        providers[provider_enum] = cls(
            api_key=api_key,
            default_model=default_model,
            **extra_kwargs,
        )

    return providers
