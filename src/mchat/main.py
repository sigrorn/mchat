# ------------------------------------------------------------------
# Component: main
# Responsibility: Application entry point
# Collaborators: PySide6, config, db, ui.main_window
# ------------------------------------------------------------------
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from mchat.config import Config
from mchat.db import Database
from mchat.ui.main_window import MainWindow

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
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
