# ------------------------------------------------------------------
# Component: main
# Responsibility: Application entry point
# Collaborators: PySide6, config, db, ui.main_window
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
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "mchat.mchat.app.1"
        )

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

    exit_code = app.exec()
    db.close()
    debug_logger.close_all()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
