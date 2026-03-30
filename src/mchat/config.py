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
    "default_provider": "claude",
    "claude_model": "claude-sonnet-4-20250514",
    "openai_model": "gpt-4.1",
    "font_size": 14,
    "system_prompt": "Be ruthless and direct in your responses. I value clarity, but I also want explained reasoning.",
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
