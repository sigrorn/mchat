# ------------------------------------------------------------------
# Component: SettingsApplier
# Responsibility: Run the Settings dialog and apply its result —
#                 re-initialise providers, refresh the provider panel,
#                 reapply chat colours and shading, and refresh
#                 dependent UI state (input colour, matrix panel,
#                 font size). Separated from PreferencesAdapter so
#                 the post-dialog fan-out is isolated from the
#                 small preference-persistence concerns.
# Collaborators: services.ServicesContext, SettingsHost (Protocol),
#                ui.settings_dialog
# ------------------------------------------------------------------
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from mchat.config import PROVIDER_META
from mchat.models.message import Provider
from mchat.ui.services import ServicesContext
from mchat.ui.settings_dialog import SettingsDialog

if TYPE_CHECKING:
    from mchat.ui.main_window import MainWindow  # noqa: F401


class SettingsHost(Protocol):
    """Presentational surface SettingsApplier is allowed to touch.

    Listed explicitly so new refresh calls added in MainWindow don't
    silently grow the coupling — the Protocol is the audit trail.
    """

    _chat: Any
    _font_size: int

    # Callbacks the applier invokes after a successful dialog save.
    def _init_providers(self) -> None: ...
    def _rebuild_services(self) -> None: ...
    def _populate_model_combos(self) -> None: ...
    def _apply_all_combo_styles(self) -> None: ...
    def _sync_matrix_panel(self) -> None: ...
    def _update_input_placeholder(self) -> None: ...
    def _update_input_color(self) -> None: ...
    def _apply_font_size(self) -> None: ...


class SettingsApplier:
    """Runs the Settings dialog and fans out the resulting updates."""

    def __init__(self, host: SettingsHost, services: ServicesContext) -> None:
        self._host = host
        self._services = services

    def open(self) -> None:
        host = self._host
        svc = self._services
        providers = svc.router._providers if svc.router else {}

        # ModelCatalog is the source of truth for cached model lists —
        # SettingsDialog never needs to harvest combo contents or call
        # provider.list_models() synchronously during _build_ui.
        models_cache: dict[Provider, list[str]] = svc.model_catalog.all()
        dialog = SettingsDialog(
            svc.config,
            providers=providers,
            models_cache=models_cache,
            parent=host,  # type: ignore[arg-type]  # host is a QWidget at runtime
        )
        if not dialog.exec():
            return

        self._apply_result()

    def _apply_result(self) -> None:
        """Post-dialog fan-out. Called when the user clicked Save."""
        host = self._host
        svc = self._services

        host._init_providers()
        host._rebuild_services()
        host._populate_model_combos()
        host._apply_all_combo_styles()
        host._sync_matrix_panel()
        host._update_input_placeholder()
        host._update_input_color()

        new_size = int(svc.config.get("font_size") or 14)
        if new_size != host._font_size:
            host._font_size = new_size
            host._apply_font_size()

        host._chat.update_colors(
            **{
                meta["color_key"]: svc.config.get(meta["color_key"])
                for meta in PROVIDER_META.values()
            },
            color_user=svc.config.get("color_user"),
        )
        host._chat.update_shading(
            mode=str(svc.config.get("exclude_shade_mode") or "darken"),
            amount=int(svc.config.get("exclude_shade_amount") or 20),
        )
