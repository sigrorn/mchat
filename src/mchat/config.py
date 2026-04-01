# ------------------------------------------------------------------
# Component: Config
# Responsibility: Application configuration management (API keys, settings)
# Collaborators: json, pathlib
# ------------------------------------------------------------------
from __future__ import annotations

import json
from pathlib import Path

DEFAULT_CONFIG_DIR = Path.home() / ".mchat"
DEFAULT_CONFIG_FILE = DEFAULT_CONFIG_DIR / "config.json"

DEFAULTS = {
    "anthropic_api_key": "",
    "openai_api_key": "",
    "gemini_api_key": "",
    "perplexity_api_key": "",
    "default_provider": "claude",
    "claude_model": "claude-sonnet-4-20250514",
    "openai_model": "gpt-4.1",
    "gemini_model": "gemini-2.5-flash",
    "perplexity_model": "sonar",
    "font_size": 14,
    "color_user": "#d4d4d4",
    "color_claude": "#b0b0b0",
    "color_openai": "#e8e8e8",
    "color_gemini": "#c8d8e8",
    "color_perplexity": "#d8c8e8",
    "system_prompt": "Be ruthless and direct in your responses. I value clarity, but I also want explained reasoning.",
}

# Per-provider metadata: (api_key_config, model_config, color_config, display_name)
PROVIDER_META: dict[str, dict] = {
    "claude": {
        "api_key": "anthropic_api_key",
        "model_key": "claude_model",
        "color_key": "color_claude",
        "display": "Claude",
    },
    "openai": {
        "api_key": "openai_api_key",
        "model_key": "openai_model",
        "color_key": "color_openai",
        "display": "GPT",
    },
    "gemini": {
        "api_key": "gemini_api_key",
        "model_key": "gemini_model",
        "color_key": "color_gemini",
        "display": "Gemini",
    },
    "perplexity": {
        "api_key": "perplexity_api_key",
        "model_key": "perplexity_model",
        "color_key": "color_perplexity",
        "display": "Perplexity",
    },
}

MIN_FONT_SIZE = 8
MAX_FONT_SIZE = 32


class Config:
    def __init__(self, config_path: Path | None = None) -> None:
        self._path = config_path or DEFAULT_CONFIG_FILE
        self._data: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            self._data = json.loads(self._path.read_text(encoding="utf-8"))
        else:
            self._data = {}

    def get(self, key: str) -> str:
        return self._data.get(key, DEFAULTS.get(key, ""))

    def set(self, key: str, value: str) -> None:
        self._data[key] = value

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(self._data, indent=2), encoding="utf-8"
        )

    @property
    def anthropic_api_key(self) -> str:
        return self.get("anthropic_api_key")

    @property
    def openai_api_key(self) -> str:
        return self.get("openai_api_key")
