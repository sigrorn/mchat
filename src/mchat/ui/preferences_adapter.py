# ------------------------------------------------------------------
# Component: PreferencesAdapter
# Responsibility: Apply window-level preferences and persist them —
#                 geometry restore/save, font-size (zoom), and
#                 settings-dialog round-trip including the subsequent
#                 re-application of colours, shading, provider combos,
#                 and input placeholder state.
# Collaborators: MainWindow (host), config, ui.settings_dialog
# ------------------------------------------------------------------
from __future__ import annotations

from typing import TYPE_CHECKING

from mchat.config import MAX_FONT_SIZE, MIN_FONT_SIZE, PROVIDER_META
from mchat.models.message import Provider
from mchat.ui.settings_dialog import SettingsDialog

if TYPE_CHECKING:
    from mchat.ui.main_window import MainWindow


class PreferencesAdapter:
    """Applies preferences onto the host MainWindow.

    Centralises the fan-out that happens when the user changes a
    preference: the host must update ChatWidget colours/shading,
    re-initialise providers, re-populate combos, refresh input
    colour, and so on. Keeping this in one place avoids the previous
    MainWindow sprawl.
    """

    def __init__(self, host: "MainWindow") -> None:
        self._host = host

    # ------------------------------------------------------------------
    # Geometry
    # ------------------------------------------------------------------

    def restore_geometry(self) -> None:
        host = self._host
        geo = host._config.get("window_geometry")
        if geo:
            try:
                x, y, w, h = (int(v) for v in geo.split(","))
                host.setGeometry(x, y, w, h)
                return
            except (ValueError, TypeError):
                pass
        host.resize(1100, 750)

    def save_geometry(self) -> None:
        host = self._host
        g = host.geometry()
        host._config.set(
            "window_geometry", f"{g.x()},{g.y()},{g.width()},{g.height()}"
        )
        host._config.save()

    # ------------------------------------------------------------------
    # Font size
    # ------------------------------------------------------------------

    def zoom_in(self) -> None:
        self.set_font_size(self._host._font_size + 1)

    def zoom_out(self) -> None:
        self.set_font_size(self._host._font_size - 1)

    def zoom_reset(self) -> None:
        self.set_font_size(14)

    def set_font_size(self, size: int) -> None:
        host = self._host
        size = max(MIN_FONT_SIZE, min(MAX_FONT_SIZE, size))
        if size == host._font_size:
            return
        host._font_size = size
        host._config.set("font_size", size)
        host._config.save()
        host._apply_font_size()

    # ------------------------------------------------------------------
    # Settings dialog
    # ------------------------------------------------------------------

    def open_settings(self) -> None:
        host = self._host
        providers = host._router._providers if host._router else {}
        # Harvest the model lists the combos already hold — MainWindow
        # fetches these asynchronously after startup, so they are usually
        # up-to-date and available without any extra API calls.
        models_cache: dict[Provider, list[str]] = {}
        for p, combo in host._combos.items():
            items = [combo.itemText(i) for i in range(combo.count())]
            if items:
                models_cache[p] = items
        dialog = SettingsDialog(
            host._config,
            providers=providers,
            models_cache=models_cache,
            parent=host,
        )
        if not dialog.exec():
            return

        # Re-apply everything that might have changed
        host._init_providers()
        host._populate_model_combos()
        host._apply_all_combo_styles()
        host._sync_matrix_panel()
        host._update_input_placeholder()
        host._update_input_color()
        new_size = int(host._config.get("font_size") or 14)
        if new_size != host._font_size:
            host._font_size = new_size
            host._apply_font_size()
        host._chat.update_colors(
            **{
                meta["color_key"]: host._config.get(meta["color_key"])
                for meta in PROVIDER_META.values()
            },
            color_user=host._config.get("color_user"),
        )
        host._chat.update_shading(
            mode=str(host._config.get("exclude_shade_mode") or "darken"),
            amount=int(host._config.get("exclude_shade_amount") or 20),
        )
