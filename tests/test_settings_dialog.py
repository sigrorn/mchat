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

    def test_no_default_provider_control(self, qtbot, config):
        """Stage 3A.4 — default_provider UI control removed from
        SettingsDialog (kept in config as fallback for all,/flipped,
        only)."""
        from mchat.ui.settings_dialog import SettingsDialog
        d = SettingsDialog(config)
        qtbot.addWidget(d)
        assert not hasattr(d, "_default_provider")

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

    def test_diagram_format_combo_exists(self, qtbot, config):
        """#151 — SettingsDialog exposes diagram_format as a combo box."""
        from mchat.ui.settings_dialog import SettingsDialog
        d = SettingsDialog(config)
        qtbot.addWidget(d)
        assert hasattr(d, "_diagram_format")
        items = [
            d._diagram_format.itemText(i)
            for i in range(d._diagram_format.count())
        ]
        assert "auto" in items
        assert "mermaid" in items
        assert "graphviz" in items
        assert "none" in items

    def test_diagram_format_loads_from_config(self, qtbot, config):
        config.set("diagram_format", "graphviz")
        config.save()
        from mchat.ui.settings_dialog import SettingsDialog
        d = SettingsDialog(config)
        qtbot.addWidget(d)
        assert d._diagram_format.currentText() == "graphviz"

    def test_diagram_format_saved_to_config(self, qtbot, config):
        from mchat.ui.settings_dialog import SettingsDialog
        d = SettingsDialog(config)
        qtbot.addWidget(d)
        idx = d._diagram_format.findText("mermaid")
        d._diagram_format.setCurrentIndex(idx)
        d._save()
        assert config.get("diagram_format") == "mermaid"

    def test_work_directory_field_exists(self, qtbot, config):
        """#154 — SettingsDialog exposes work_directory."""
        from mchat.ui.settings_dialog import SettingsDialog
        d = SettingsDialog(config)
        qtbot.addWidget(d)
        assert hasattr(d, "_work_directory")

    def test_work_directory_loads_from_config(self, qtbot, config):
        config.set("work_directory", "C:/some/path")
        config.save()
        from mchat.ui.settings_dialog import SettingsDialog
        d = SettingsDialog(config)
        qtbot.addWidget(d)
        assert d._work_directory.text() == "C:/some/path"

    def test_work_directory_saved_to_config(self, qtbot, config):
        from mchat.ui.settings_dialog import SettingsDialog
        d = SettingsDialog(config)
        qtbot.addWidget(d)
        d._work_directory.setText("D:/exports")
        d._save()
        assert config.get("work_directory") == "D:/exports"

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

    def test_export_produces_parseable_md(self, qtbot, config):
        """#101 — export produces .md with all provider fields."""
        from mchat.ui.providers_dialog import ProvidersDialog
        d = ProvidersDialog(config)
        qtbot.addWidget(d)
        md = d.export_providers_md()
        assert "## Claude" in md
        assert "API key: ant-key-xyz" in md
        assert "Model: claude-sonnet-4-20250514" in md
        assert "Color: #b0b0b0" in md
        assert "claude baseline" in md

    def test_import_writes_config(self, qtbot, config):
        """#101 — import parses .md and writes to config."""
        from mchat.ui.providers_dialog import ProvidersDialog
        d = ProvidersDialog(config)
        qtbot.addWidget(d)
        md = """# Provider Settings

## Claude
- API key: new-claude-key
- Model: claude-opus-4
- Color: #ffffff
- System prompt:

Be very concise
"""
        d.import_providers_md(md)
        assert config.get("anthropic_api_key") == "new-claude-key"
        assert config.get("claude_model") == "claude-opus-4"
        assert config.get("color_claude") == "#ffffff"
        assert config.get("system_prompt_claude") == "Be very concise"

    def test_roundtrip_preserves_fields(self, qtbot, config):
        """#101 — export then import preserves all fields."""
        from mchat.ui.providers_dialog import ProvidersDialog
        d = ProvidersDialog(config)
        qtbot.addWidget(d)
        md = d.export_providers_md()
        # Change a value, then import the original export
        config.set("anthropic_api_key", "changed")
        d2 = ProvidersDialog(config)
        qtbot.addWidget(d2)
        d2.import_providers_md(md)
        assert config.get("anthropic_api_key") == "ant-key-xyz"

    def test_unconfigured_tab_has_red_title(self, qtbot, config):
        """#110 — tabs with empty API keys should have red title text."""
        from mchat.ui.providers_dialog import ProvidersDialog
        # config has anthropic_api_key set, but gemini is empty
        config.set("gemini_api_key", "")
        config.save()
        d = ProvidersDialog(config)
        qtbot.addWidget(d)
        # Find the Gemini tab index
        gemini_idx = None
        for i in range(d._tabs.count()):
            if d._tabs.tabText(i) == "Gemini":
                gemini_idx = i
                break
        assert gemini_idx is not None
        color = d._tabs.tabBar().tabTextColor(gemini_idx)
        assert color.red() > 200  # should be reddish

    def test_configured_tab_not_red(self, qtbot, config):
        """Tabs with API keys should NOT have red title text."""
        from mchat.ui.providers_dialog import ProvidersDialog
        d = ProvidersDialog(config)
        qtbot.addWidget(d)
        claude_idx = None
        for i in range(d._tabs.count()):
            if d._tabs.tabText(i) == "Claude":
                claude_idx = i
                break
        assert claude_idx is not None
        color = d._tabs.tabBar().tabTextColor(claude_idx)
        # Default colour or non-red
        assert color.red() < 200 or color.green() > 100

    def test_mistral_tab_exists(self, qtbot, config):
        """#80 — ProvidersDialog must have a Mistral tab."""
        from mchat.ui.providers_dialog import ProvidersDialog
        d = ProvidersDialog(config)
        qtbot.addWidget(d)
        assert "mistral" in d._api_key_edits
        assert "mistral" in d._color_btns
        assert "mistral" in d._system_prompt_edits

    def test_apertus_tab_exists(self, qtbot, config):
        """#156 — ProvidersDialog must have an Apertus tab."""
        from mchat.ui.providers_dialog import ProvidersDialog
        d = ProvidersDialog(config)
        qtbot.addWidget(d)
        assert "apertus" in d._api_key_edits
        assert "apertus" in d._color_btns
        assert "apertus" in d._system_prompt_edits

    def test_apertus_product_id_field_exists(self, qtbot, config):
        """#156 — Apertus tab must have a Product ID field."""
        from mchat.ui.providers_dialog import ProvidersDialog
        d = ProvidersDialog(config)
        qtbot.addWidget(d)
        assert "apertus" in d._product_id_edits

    def test_apertus_product_id_saved(self, qtbot, config):
        """#156 — Product ID must be saved to config."""
        from mchat.ui.providers_dialog import ProvidersDialog
        config.set("apertus_product_id", "12345")
        config.save()
        d = ProvidersDialog(config)
        qtbot.addWidget(d)
        d._product_id_edits["apertus"].setText("99999")
        d._save()
        assert config.get("apertus_product_id") == "99999"

    def test_apertus_product_id_loads(self, qtbot, config):
        """#156 — Product ID must be loaded from config."""
        from mchat.ui.providers_dialog import ProvidersDialog
        config.set("apertus_product_id", "107927")
        config.save()
        d = ProvidersDialog(config)
        qtbot.addWidget(d)
        assert d._product_id_edits["apertus"].text() == "107927"

    def test_apertus_export_includes_product_id(self, qtbot, config):
        """#156 — Provider export must include product_id for Apertus."""
        from mchat.ui.providers_dialog import ProvidersDialog
        config.set("apertus_product_id", "107927")
        config.set("apertus_api_key", "test-key")
        config.save()
        d = ProvidersDialog(config)
        qtbot.addWidget(d)
        md = d.export_providers_md()
        assert "Product ID: 107927" in md

    def test_apertus_import_reads_product_id(self, qtbot, config):
        """#156 — Provider import must parse product_id for Apertus."""
        from mchat.ui.providers_dialog import ProvidersDialog
        d = ProvidersDialog(config)
        qtbot.addWidget(d)
        md = """# Provider Settings

## Apertus
- API key: apertus-key-123
- Product ID: 55555
- Model: swiss-ai/Apertus-70B-Instruct-2509
- Color: #a0c8e8
- System prompt:

Be helpful
"""
        d.import_providers_md(md)
        assert config.get("apertus_product_id") == "55555"
        assert config.get("apertus_api_key") == "apertus-key-123"
