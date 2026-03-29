# ------------------------------------------------------------------
# Component: test_config
# Responsibility: Tests for configuration management
# Collaborators: config
# ------------------------------------------------------------------
from __future__ import annotations

import pytest

from mchat.config import Config


@pytest.fixture
def config(tmp_path):
    return Config(config_path=tmp_path / "config.json")


class TestConfig:
    def test_defaults(self, config):
        assert config.get("default_provider") == "claude"
        assert config.get("claude_model") == "claude-sonnet-4-20250514"

    def test_set_and_get(self, config):
        config.set("anthropic_api_key", "sk-test-123")
        assert config.get("anthropic_api_key") == "sk-test-123"

    def test_save_and_reload(self, tmp_path):
        path = tmp_path / "config.json"
        config = Config(config_path=path)
        config.set("openai_api_key", "sk-openai-test")
        config.save()

        config2 = Config(config_path=path)
        assert config2.get("openai_api_key") == "sk-openai-test"

    def test_properties(self, config):
        config.set("anthropic_api_key", "ant-key")
        config.set("openai_api_key", "oai-key")
        assert config.anthropic_api_key == "ant-key"
        assert config.openai_api_key == "oai-key"
