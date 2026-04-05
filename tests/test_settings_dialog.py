# ------------------------------------------------------------------
# Component: test_settings_dialog
# Responsibility: Smoke tests for the slimmed SettingsDialog (general
#                 settings only) and the new tabbed ProvidersDialog.
# Collaborators: ui.settings_dialog, ui.providers_dialog, config
# ------------------------------------------------------------------
from __future__ import annotations

import pytest

from mchat.config import Config
from mchat.models.message import Provider


@pytest.fixture
def config(tmp_path):
    cfg = Config(config_path=tmp_path / "cfg.json")
    cfg.set("font_size", 14)
    cfg.set("color_user", "#d4d4d4")
    cfg.set("system_prompt", "global default prompt")
    cfg.set("exclude_shade_mode", "darken")
    cfg.set("exclude_shade_amount", 20)
    cfg.set("default_provider", "claude")
    # Provider-specific fields stay in the providers dialog
    cfg.set("anthropic_api_key", "ant-key-xyz")
    cfg.set("claude_model", "claude-sonnet-4-20250514")
    cfg.set("color_claude", "#b0b0b0")
    cfg.set("system_prompt_claude", "claude baseline")
    cfg.save()
    return cfg


class TestSettingsDialogGeneralOnly:
    """The slimmed SettingsDialog should own ONLY the general fields.
    Per-provider fields live in ProvidersDialog."""

    def test_loads_general_fields_from_config(self, qtbot, config):
        from mchat.ui.settings_dialog import SettingsDialog
        d = SettingsDialog(config)
        qtbot.addWidget(d)

        assert d._font_size.value() == 14
        assert d._color_user_btn.property("hex_color") == "#d4d4d4"
        assert d._system_prompt.toPlainText() == "global default prompt"
        assert d._exclude_shade_mode.currentText() == "darken"
        assert d._exclude_shade_amount.value() == 20
        assert d._default_provider.currentText() == "claude"

    def test_does_not_expose_provider_widgets(self, qtbot, config):
        """API keys, model combos, provider colours, and provider
        prompts all belong to ProvidersDialog — the slimmed
        SettingsDialog must not instantiate them."""
        from mchat.ui.settings_dialog import SettingsDialog
        d = SettingsDialog(config)
        qtbot.addWidget(d)
        assert not hasattr(d, "_api_key_edits") or d._api_key_edits == {}
        assert not hasattr(d, "_model_combos") or d._model_combos == {}
        assert not hasattr(d, "_color_btns") or d._color_btns == {}
        assert not hasattr(d, "_system_prompt_edits") or d._system_prompt_edits == {}

    def test_save_writes_general_fields_to_config(self, qtbot, config):
        from mchat.ui.settings_dialog import SettingsDialog
        d = SettingsDialog(config)
        qtbot.addWidget(d)
        d._font_size.setValue(18)
        d._system_prompt.setPlainText("updated prompt")
        d._exclude_shade_amount.setValue(42)
        d._save()
        assert int(config.get("font_size")) == 18
        assert config.get("system_prompt") == "updated prompt"
        assert int(config.get("exclude_shade_amount")) == 42

    def test_save_does_not_touch_provider_fields(self, qtbot, config):
        """The dialog must not clobber provider-specific config values
        (empty API key fields would wipe the real ones)."""
        from mchat.ui.settings_dialog import SettingsDialog
        d = SettingsDialog(config)
        qtbot.addWidget(d)
        d._save()
        assert config.get("anthropic_api_key") == "ant-key-xyz"
        assert config.get("claude_model") == "claude-sonnet-4-20250514"
        assert config.get("color_claude") == "#b0b0b0"
        assert config.get("system_prompt_claude") == "claude baseline"


class TestProvidersDialog:
    """The new ProvidersDialog is tabbed — one tab per provider —
    and owns every provider-specific configuration field."""

    def test_has_one_tab_per_provider(self, qtbot, config):
        from mchat.ui.providers_dialog import ProvidersDialog
        d = ProvidersDialog(config)
        qtbot.addWidget(d)
        # One tab for each provider in the enum
        assert d._tabs.count() == len(list(Provider))

    def test_loads_existing_provider_config(self, qtbot, config):
        from mchat.ui.providers_dialog import ProvidersDialog
        d = ProvidersDialog(config)
        qtbot.addWidget(d)
        assert d._api_key_edits["claude"].text() == "ant-key-xyz"
        assert d._color_btns["claude"].property("hex_color") == "#b0b0b0"
        assert d._system_prompt_edits["claude"].toPlainText() == "claude baseline"

    def test_save_writes_provider_fields(self, qtbot, config):
        from mchat.ui.providers_dialog import ProvidersDialog
        d = ProvidersDialog(config)
        qtbot.addWidget(d)
        d._api_key_edits["claude"].setText("new-key-xyz")
        d._system_prompt_edits["claude"].setPlainText("updated claude baseline")
        d._save()
        assert config.get("anthropic_api_key") == "new-key-xyz"
        assert config.get("system_prompt_claude") == "updated claude baseline"

    def test_save_does_not_touch_general_fields(self, qtbot, config):
        """General settings (font, shading, global prompt) belong to
        SettingsDialog — ProvidersDialog must not modify them."""
        from mchat.ui.providers_dialog import ProvidersDialog
        d = ProvidersDialog(config)
        qtbot.addWidget(d)
        d._save()
        assert int(config.get("font_size")) == 14
        assert config.get("system_prompt") == "global default prompt"
        assert config.get("color_user") == "#d4d4d4"

    def test_model_combo_uses_cache_over_network(self, qtbot, config):
        """When a models_cache is supplied, ProvidersDialog must not
        call provider.list_models() — prevents blocking the UI."""
        from mchat.ui.providers_dialog import ProvidersDialog
        cache = {Provider.CLAUDE: ["claude-haiku-4-5", "claude-sonnet-4"]}
        d = ProvidersDialog(config, models_cache=cache)
        qtbot.addWidget(d)
        items = [
            d._model_combos["claude"].itemText(i)
            for i in range(d._model_combos["claude"].count())
        ]
        assert "claude-haiku-4-5" in items
        assert "claude-sonnet-4" in items

    def test_reset_colors_restores_provider_defaults(self, qtbot, config):
        from mchat.ui.providers_dialog import ProvidersDialog
        from mchat.config import DEFAULTS
        d = ProvidersDialog(config)
        qtbot.addWidget(d)
        # Change a colour, then reset
        d._color_btns["claude"].setProperty("hex_color", "#ffffff")
        d._reset_colors()
        assert d._color_btns["claude"].property("hex_color") == DEFAULTS["color_claude"]
