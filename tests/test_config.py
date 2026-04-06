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

    def test_malformed_json(self, tmp_path):
        path = tmp_path / "config.json"
        path.write_text("not valid json {{{", encoding="utf-8")
        config = Config(config_path=path)
        # Should fall back to defaults, not crash
        assert config.get("default_provider") == "claude"

    def test_non_dict_json(self, tmp_path):
        path = tmp_path / "config.json"
        path.write_text('"just a string"', encoding="utf-8")
        config = Config(config_path=path)
        assert config.get("default_provider") == "claude"

    def test_empty_file(self, tmp_path):
        path = tmp_path / "config.json"
        path.write_text("", encoding="utf-8")
        config = Config(config_path=path)
        assert config.get("default_provider") == "claude"


class TestMistralConfig:
    """#80 — Mistral config keys must exist in DEFAULTS and PROVIDER_META."""

    def test_mistral_defaults_exist(self, config):
        assert config.get("mistral_api_key") == ""
        assert config.get("mistral_model") != ""  # has a default model
        assert config.get("color_mistral") != ""
        assert config.get("system_prompt_mistral") == ""

    def test_mistral_provider_meta(self):
        from mchat.config import PROVIDER_META
        assert "mistral" in PROVIDER_META
        meta = PROVIDER_META["mistral"]
        assert "api_key" in meta
        assert "model_key" in meta
        assert "color_key" in meta
        assert "system_prompt_key" in meta
        assert "display" in meta
