# ------------------------------------------------------------------
# Component: conftest (test root)
# Responsibility: Shared pytest fixtures and early setup for the
#                 entire test suite.
# Collaborators: (external: PySide6)
# ------------------------------------------------------------------
import os

# Use the "minimal" Qt platform so GUI tests don't flash windows on
# screen.  "offscreen" segfaults on Windows/PySide6 around 35% of
# tests; "minimal" is stable and still headless.
# Must be set before any Qt import.
os.environ.setdefault("QT_QPA_PLATFORM", "minimal")
