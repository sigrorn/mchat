# ------------------------------------------------------------------
# Component: mermaid_renderer
# Responsibility: Shell out to mermaid-cli's `mmdc` binary to turn
#                 Mermaid source into PNG bytes, with a two-tier cache
#                 (in-memory LRU + on-disk content-addressable) so
#                 repeat renders are cheap, persisted across app
#                 restarts, and safe to call on every insertHtml.
#                 Returns None on every failure mode so callers can
#                 fall back to a source-only display.
# Collaborators: subprocess, shutil, hashlib, tempfile,
#                mchat.config (for the default cache directory).
# ------------------------------------------------------------------
from __future__ import annotations

import functools
import hashlib
import shutil
import subprocess
import tempfile
from pathlib import Path

from mchat.config import DEFAULT_CONFIG_DIR

# -- Tunables ------------------------------------------------------

_MAX_SOURCE_BYTES = 65536
_MEMORY_CACHE_MAX = 64
_MEMORY_CACHE: dict[str, bytes] = {}


# -- Cache location -----------------------------------------------

def cache_dir() -> Path:
    """Return the on-disk cache directory. Tests monkeypatch this to
    redirect caching at a tmp path."""
    return DEFAULT_CONFIG_DIR / "mermaid_cache"


# -- Availability check -------------------------------------------

@functools.lru_cache(maxsize=1)
def is_mmdc_available() -> bool:
    """Return True iff the `mmdc` binary is resolvable on PATH.
    Memoised: the result is cached for the lifetime of the process.
    Tests call `is_mmdc_available.cache_clear()` to reset."""
    return shutil.which("mmdc") is not None


# -- Public renderer ----------------------------------------------

def render_mermaid(source: str, *, timeout_s: float = 15.0) -> bytes | None:
    """Turn Mermaid source into PNG bytes. Returns None on any failure.

    Lookup order: in-memory cache -> on-disk cache -> shell out to
    `mmdc`. Uses temp files for I/O because mmdc doesn't reliably
    support stdin/stdout pipes.

    #153: mermaid must render to PNG (not SVG) because Qt's
    QSvgRenderer doesn't support <foreignObject>, which mermaid uses
    for all node text. Rendered at 2400px wide so complex diagrams
    stay legible. DOT/graphviz stays SVG since it uses native <text>.

    Timeout is 15s (vs 5s for dot) because mmdc spins up headless
    Chromium on each invocation.
    """
    if not source or not source.strip():
        return None
    encoded = source.encode("utf-8")
    if len(encoded) > _MAX_SOURCE_BYTES:
        return None

    digest = hashlib.sha256(encoded).hexdigest()

    # 1) memory cache
    cached = _memory_get(digest)
    if cached is not None:
        return cached

    # 2) disk cache
    disk_path = cache_dir() / f"{digest}.png"
    if disk_path.exists():
        try:
            data = disk_path.read_bytes()
        except OSError:
            data = None
        if data:
            _memory_put(digest, data)
            return data

    # 3) shell out
    if not is_mmdc_available():
        return None
    mmdc_path = shutil.which("mmdc")
    if mmdc_path is None:
        return None

    try:
        # mmdc requires file-based I/O — use temp files
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            input_file = td_path / "input.mmd"
            output_file = td_path / "output.png"
            input_file.write_text(source, encoding="utf-8")

            result = subprocess.run(
                [
                    mmdc_path,
                    "-i", str(input_file),
                    "-o", str(output_file),
                    "-w", "2400",
                    "--quiet",
                ],
                capture_output=True,
                timeout=timeout_s,
            )

            if result.returncode != 0:
                return None
            if not output_file.exists():
                return None
            svg = output_file.read_bytes()
    except subprocess.TimeoutExpired:
        return None
    except (OSError, subprocess.SubprocessError):
        return None

    if not svg:
        return None

    # Disk first, memory second
    try:
        cache_dir().mkdir(parents=True, exist_ok=True)
        disk_path.write_bytes(svg)
    except OSError:
        pass
    _memory_put(digest, svg)
    return svg


def clear_cache() -> None:
    """Wipe both cache tiers."""
    _MEMORY_CACHE.clear()
    try:
        for p in cache_dir().glob("*.png"):
            try:
                p.unlink()
            except OSError:
                pass
    except OSError:
        pass


# -- In-memory LRU helpers ----------------------------------------

def _memory_get(digest: str) -> bytes | None:
    data = _MEMORY_CACHE.get(digest)
    if data is None:
        return None
    _MEMORY_CACHE.pop(digest)
    _MEMORY_CACHE[digest] = data
    return data


def _memory_put(digest: str, data: bytes) -> None:
    _MEMORY_CACHE[digest] = data
    while len(_MEMORY_CACHE) > _MEMORY_CACHE_MAX:
        oldest = next(iter(_MEMORY_CACHE))
        _MEMORY_CACHE.pop(oldest, None)
