# ------------------------------------------------------------------
# Component: main
# Responsibility: Application entry point
# Collaborators: PySide6, config, db, ui.main_window
# ------------------------------------------------------------------
from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from mchat.config import Config
from mchat.db import Database
from mchat.ui.main_window import MainWindow


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("mchat")
    app.setStyle("Fusion")

    config = Config()
    db = Database()

    window = MainWindow(config, db)
    window.show()

    exit_code = app.exec()
    db.close()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
