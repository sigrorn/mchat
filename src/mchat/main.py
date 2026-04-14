# ------------------------------------------------------------------
# Component: main
# Responsibility: Application entry point
# Collaborators: config, db, ui.main_window  (external: PySide6)
# ------------------------------------------------------------------
from __future__ import annotations

import sys
import traceback
from datetime import datetime
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from mchat.config import DEFAULT_CONFIG_DIR, Config
from mchat.db import Database
from mchat.ui.main_window import MainWindow


def _install_crash_logger() -> None:
    """Install a global sys.excepthook that logs uncaught exceptions
    to ~/.mchat/crash.log with a timestamp + full traceback (#129).

    Without this, crashes in Qt signal handlers (e.g. a background
    worker firing after its dependencies have been torn down) print
    nothing to the terminal when mchat runs as a windowed build and
    the user is left with no diagnostic trail.
    """
    crash_log = DEFAULT_CONFIG_DIR / "crash.log"

    def _hook(exc_type, exc_value, exc_tb):
        # Still print to stderr for terminal runs
        try:
            sys.__excepthook__(exc_type, exc_value, exc_tb)
        except Exception:
            pass
        try:
            crash_log.parent.mkdir(parents=True, exist_ok=True)
            with open(crash_log, "a", encoding="utf-8") as f:
                f.write(f"\n===== {datetime.now().isoformat()} =====\n")
                traceback.print_exception(exc_type, exc_value, exc_tb, file=f)
        except Exception:
            pass

    sys.excepthook = _hook

def _find_icon() -> Path:
    """Find the icon file, checking multiple bundle/source locations."""
    candidates: list[Path] = []
    # PyInstaller stores data files under sys._MEIPASS at runtime
    if hasattr(sys, "_MEIPASS"):
        base = Path(sys._MEIPASS)
        candidates.append(base / "mchat" / "resources")
        candidates.append(base / "resources")
        candidates.append(base)
    # When running as a PyInstaller folder build, the exe dir has _internal
    if hasattr(sys, "frozen"):
        exe_dir = Path(sys.executable).parent
        candidates.append(exe_dir / "_internal" / "mchat" / "resources")
        candidates.append(exe_dir / "mchat" / "resources")
        candidates.append(exe_dir)
    # Source/installed package layout
    candidates.append(Path(__file__).parent / "resources")

    for d in candidates:
        for name in ("icon.ico", "icon.png"):
            p = d / name
            if p.exists():
                return p
    return Path(__file__).parent / "resources" / "icon.png"


def _set_windows_app_user_model_id() -> None:
    """Set the Windows taskbar identity before Qt creates windows."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "mchat.mchat.app.1"
        )
    except Exception:
        # Best effort only: failure should not block startup.
        pass


def _apply_windows_window_icon(window, icon_path: Path) -> None:
    """Force the native Windows small/big window icons from the app .ico.

    PyInstaller's ``icon=...`` sets the executable resource that Explorer
    shows, while Qt's ``setWindowIcon`` normally sets the runtime window icon.
    Some Windows taskbar paths still use the native HWND icon handles, so set
    those explicitly as a fallback.
    """
    if sys.platform != "win32" or not icon_path.exists():
        return

    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        hwnd = int(window.winId())
        if not hwnd:
            return

        image_icon = 1
        lr_load_from_file = 0x0010
        wm_seticon = 0x0080
        icon_small = 0
        icon_big = 1
        icon_small2 = 2
        sm_cxicon = 11
        sm_cyicon = 12
        sm_cxsmicon = 49
        sm_cysmicon = 50

        load_image = user32.LoadImageW
        load_image.argtypes = [
            wintypes.HINSTANCE,
            wintypes.LPCWSTR,
            wintypes.UINT,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.UINT,
        ]
        load_image.restype = wintypes.HANDLE

        send_message = user32.SendMessageW
        send_message.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        send_message.restype = wintypes.LPARAM

        def _load_icon(width: int, height: int):
            return load_image(
                None, str(icon_path), image_icon, width, height, lr_load_from_file
            )

        big_icon = _load_icon(
            user32.GetSystemMetrics(sm_cxicon),
            user32.GetSystemMetrics(sm_cyicon),
        )
        small_icon = _load_icon(
            user32.GetSystemMetrics(sm_cxsmicon),
            user32.GetSystemMetrics(sm_cysmicon),
        )

        if big_icon:
            send_message(hwnd, wm_seticon, icon_big, big_icon)
        if small_icon:
            send_message(hwnd, wm_seticon, icon_small, small_icon)
            send_message(hwnd, wm_seticon, icon_small2, small_icon)

        if big_icon or small_icon:
            # Keep handles alive for the process lifetime; Windows reclaims
            # them on exit and this avoids dangling HWND icon handles.
            window._mchat_windows_icon_handles = (big_icon, small_icon)
    except Exception:
        # Best effort only; Qt's icon path remains the portable baseline.
        pass


def main() -> None:
    # Install crash logger before anything else so we capture failures
    # that happen during startup too.
    _install_crash_logger()

    # Parse -debug / --debug flag before Qt consumes sys.argv
    import mchat.debug_logger as debug_logger
    if "-debug" in sys.argv or "--debug" in sys.argv:
        debug_logger.enabled = True
        debug_logger.configure()
        # Remove the flag so Qt doesn't try to interpret it
        sys.argv = [a for a in sys.argv if a not in ("-debug", "--debug")]

    # Set AppUserModelID so Windows taskbar shows our icon, not Python's
    _set_windows_app_user_model_id()

    # #146/#150: log diagram tool availability once at startup so a
    # missing binary shows up in crash.log rather than failing silently.
    try:
        from mchat import dot_renderer, mermaid_renderer
        _graphviz = dot_renderer.is_graphviz_available()
        _mmdc = mermaid_renderer.is_mmdc_available()
        crash_log = DEFAULT_CONFIG_DIR / "crash.log"
        crash_log.parent.mkdir(parents=True, exist_ok=True)
        with open(crash_log, "a", encoding="utf-8") as _f:
            _f.write(
                f"\n===== {datetime.now().isoformat()} startup =====\n"
                f"graphviz available: {_graphviz}\n"
                f"mmdc available: {_mmdc}\n"
            )
    except Exception:
        # Best-effort logging — never let it crash startup.
        pass

    app = QApplication(sys.argv)
    app.setApplicationName("mchat")
    app.setStyle("Fusion")

    # Resolve icon at runtime (lazily) so PyInstaller's _MEIPASS is set
    icon_path = _find_icon()
    icon = QIcon(str(icon_path)) if icon_path.exists() else QIcon()
    if not icon.isNull():
        app.setWindowIcon(icon)

    config = Config()
    db = Database()

    window = MainWindow(config, db)
    if not icon.isNull():
        window.setWindowIcon(icon)
    window.show()
    _apply_windows_window_icon(window, icon_path)

    exit_code = app.exec()
    db.close()
    debug_logger.close_all()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
