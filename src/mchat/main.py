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

_ICON_PATH = Path(__file__).parent / "resources" / "icon.png"


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

    icon = QIcon(str(_ICON_PATH)) if _ICON_PATH.exists() else QIcon()
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
