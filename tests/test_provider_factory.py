# ------------------------------------------------------------------
# Component: test_provider_factory
# Responsibility: Tests for the provider factory that builds provider
#                 instances from config.
# Collaborators: provider_factory, config, providers
# ------------------------------------------------------------------
from __future__ import annotations

import pytest

from mchat.config import Config
from mchat.models.message import Provider


@pytest.fixture
def config(tmp_path):
    cfg = Config(config_path=tmp_path / "cfg.json")
    cfg.save()
    return cfg


class TestProviderFactory:
    """#164 — provider factory replaces hand-written _init_providers."""

    def test_module_importable(self):
        from mchat.provider_factory import build_providers
        assert callable(build_providers)

    def test_no_providers_when_no_keys(self, config):
        from mchat.provider_factory import build_providers
        providers = build_providers(config)
        assert providers == {}

    def test_claude_built_when_key_present(self, config):
        from mchat.provider_factory import build_providers
        from mchat.providers.claude import ClaudeProvider
        config.set("anthropic_api_key", "fake-key")
        providers = build_providers(config)
        assert Provider.CLAUDE in providers
        assert isinstance(providers[Provider.CLAUDE], ClaudeProvider)

    def test_all_standard_providers_built(self, config):
        from mchat.provider_factory import build_providers
        for key in ("anthropic_api_key", "openai_api_key", "gemini_api_key",
                     "perplexity_api_key", "mistral_api_key"):
            config.set(key, "fake-key")
        providers = build_providers(config)
        for p in (Provider.CLAUDE, Provider.OPENAI, Provider.GEMINI,
                  Provider.PERPLEXITY, Provider.MISTRAL):
            assert p in providers, f"missing {p}"

    def test_apertus_requires_both_key_and_product_id(self, config):
        from mchat.provider_factory import build_providers
        # Key only — no provider
        config.set("apertus_api_key", "fake-key")
        providers = build_providers(config)
        assert Provider.APERTUS not in providers
        # Add product_id — now it builds
        config.set("apertus_product_id", "12345")
        providers = build_providers(config)
        assert Provider.APERTUS in providers

    def test_apertus_product_id_in_base_url(self, config):
        from mchat.provider_factory import build_providers
        config.set("apertus_api_key", "fake-key")
        config.set("apertus_product_id", "99999")
        providers = build_providers(config)
        assert "99999" in providers[Provider.APERTUS]._base_url

    def test_returns_only_configured_providers(self, config):
        """Only providers with non-empty API keys are returned."""
        from mchat.provider_factory import build_providers
        config.set("anthropic_api_key", "key1")
        config.set("openai_api_key", "")  # empty
        providers = build_providers(config)
        assert Provider.CLAUDE in providers
        assert Provider.OPENAI not in providers
