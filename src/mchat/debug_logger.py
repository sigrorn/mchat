# ------------------------------------------------------------------
# Component: debug_logger
# Responsibility: Per-persona I/O logging when -debug flag is active.
#                 Creates <persona-name>.txt files with timestamped
#                 lines: O for outgoing (to provider), I for incoming.
# Collaborators: main (flag parsing), send_controller, stream_worker
# ------------------------------------------------------------------
from __future__ import annotations

import os
import threading
from datetime import datetime
from pathlib import Path

# Global flag — set from main.py when -debug is on the command line.
enabled: bool = False
_output_dir: Path = Path(".")
_lock = threading.Lock()
_files: dict[str, object] = {}


def configure(output_dir: Path | None = None) -> None:
    global _output_dir
    if output_dir:
        _output_dir = output_dir
        _output_dir.mkdir(parents=True, exist_ok=True)


def _get_file(persona_name: str):
    """Get or open the log file for a persona."""
    if persona_name not in _files:
        safe_name = persona_name.replace(" ", "_").replace("/", "_")
        path = _output_dir / f"{safe_name}.txt"
        _files[persona_name] = open(path, "a", encoding="utf-8")
    return _files[persona_name]


def _timestamp() -> str:
    now = datetime.now()
    return now.strftime("%H%M%S.") + f"{now.microsecond // 1000:03d}"


def log_outgoing(persona_name: str, text: str) -> None:
    """Log text sent TO the provider (O = outgoing)."""
    if not enabled:
        return
    ts = _timestamp()
    with _lock:
        f = _get_file(persona_name)
        for line in text.splitlines():
            f.write(f"{ts} O {line}\n")
        f.flush()


def log_incoming(persona_name: str, text: str) -> None:
    """Log text received FROM the provider (I = incoming)."""
    if not enabled:
        return
    ts = _timestamp()
    with _lock:
        f = _get_file(persona_name)
        for line in text.splitlines():
            f.write(f"{ts} I {line}\n")
        f.flush()


def close_all() -> None:
    """Close all open log files."""
    with _lock:
        for f in _files.values():
            f.close()
        _files.clear()
