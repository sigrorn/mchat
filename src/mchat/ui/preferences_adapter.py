# ------------------------------------------------------------------
# Component: PreferencesAdapter
# Responsibility: Persist window-level preferences that live outside
#                 the Settings dialog: geometry (restore/save on
#                 open/close) and font size (zoom in/out/reset).
#                 The Settings-dialog round-trip with its post-save
#                 fan-out lives in SettingsApplier; this class is
#                 intentionally narrow.
# Collaborators: services.ServicesContext, PreferencesHost (Protocol)
# ------------------------------------------------------------------
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from mchat.config import MAX_FONT_SIZE, MIN_FONT_SIZE
from mchat.ui.services import ServicesContext

if TYPE_CHECKING:
    from mchat.ui.main_window import MainWindow  # noqa: F401


class PreferencesHost(Protocol):
    """Narrow host surface: PreferencesAdapter only needs to read the
    window geometry, set it back on restore, poke the font size, and
    trigger a font-size re-apply fan-out on the host."""

    _font_size: int

    def geometry(self): ...  # QRect
    def setGeometry(self, x: int, y: int, w: int, h: int) -> None: ...
    def resize(self, w: int, h: int) -> None: ...
    def _apply_font_size(self) -> None: ...


class PreferencesAdapter:
    """Window geometry + zoom. Nothing else."""

    def __init__(self, host: PreferencesHost, services: ServicesContext) -> None:
        self._host = host
        self._services = services

    # ------------------------------------------------------------------
    # Geometry
    # ------------------------------------------------------------------

    def restore_geometry(self) -> None:
        host = self._host
        geo = self._services.config.get("window_geometry")
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
        self._services.config.set(
            "window_geometry", f"{g.x()},{g.y()},{g.width()},{g.height()}"
        )
        self._services.config.save()

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
        self._services.config.set("font_size", size)
        self._services.config.save()
        host._apply_font_size()
