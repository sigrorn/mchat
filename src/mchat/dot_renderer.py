# ------------------------------------------------------------------
# Component: dot_renderer
# Responsibility: Shell out to graphviz's `dot` binary to turn DOT
#                 source into PNG bytes, with a two-tier cache
#                 (in-memory LRU + on-disk content-addressable) so
#                 repeat renders are cheap, persisted across app
#                 restarts, and safe to call on every insertHtml.
#                 Returns None on every failure mode so callers can
#                 fall back to a source-only display.
# Collaborators: subprocess, shutil, hashlib, mchat.config (for the
#                default cache directory under ~/.mchat/).
# ------------------------------------------------------------------
from __future__ import annotations

import functools
import hashlib
import shutil
import subprocess
from pathlib import Path

from mchat.config import DEFAULT_CONFIG_DIR

# -- Tunables ------------------------------------------------------

# Reject DOT sources larger than this many bytes outright, without
# spawning `dot`. Protects against pathological model output (a
# runaway graph that would take minutes to render or megabytes of
# RAM). 64 KiB is ~1000 lines of DOT which is more than any
# sensible diagram would ever need.
_MAX_SOURCE_BYTES = 65536

# In-memory cache size. Plain dict with manual LRU eviction; cheap
# enough that we don't need functools.lru_cache. 64 entries covers
# a typical chat session without wasting process memory.
_MEMORY_CACHE_MAX = 64

_MEMORY_CACHE: dict[str, bytes] = {}


# -- Cache location -----------------------------------------------

def cache_dir() -> Path:
    """Return the on-disk cache directory. Tests monkeypatch this to
    redirect caching at a tmp path."""
    return DEFAULT_CONFIG_DIR / "graph_cache"


# -- Availability check -------------------------------------------

@functools.lru_cache(maxsize=1)
def is_graphviz_available() -> bool:
    """Return True iff the `dot` binary is resolvable on PATH.
    Memoised: the result is cached for the lifetime of the process
    (graphviz doesn't get installed mid-session in practice). Tests
    call `is_graphviz_available.cache_clear()` to reset."""
    return shutil.which("dot") is not None


# -- Public renderer ----------------------------------------------

def render_dot(source: str, *, timeout_s: float = 5.0) -> bytes | None:
    """Turn DOT source into PNG bytes. Returns None on any failure.

    Lookup order: in-memory cache → on-disk cache → shell out to
    `dot -Tpng`. Successful renders are written to disk first (so a
    crash doesn't lose the work) and then to the in-memory LRU.

    Failure modes that return None:
      * empty or whitespace-only source
      * source larger than _MAX_SOURCE_BYTES
      * graphviz not installed
      * `dot` returned non-zero or empty stdout
      * subprocess timeout or OSError
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
    if not is_graphviz_available():
        return None
    dot_path = shutil.which("dot")
    if dot_path is None:
        return None

    try:
        result = subprocess.run(
            [
                dot_path,
                "-Tpng",
                "-Gdpi=72",
                # -Gsize caps the rendered dimensions at 12" x 12"
                # at 72 DPI (~864 x 864 px), so a runaway graph
                # can't produce a 50-megapixel image.
                "-Gsize=12,12",
            ],
            input=encoded,
            capture_output=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return None
    except (OSError, subprocess.SubprocessError):
        return None

    if result.returncode != 0 or not result.stdout:
        return None

    png = result.stdout
    # Disk first, memory second — so a crash between the two still
    # leaves the render recoverable next session.
    try:
        cache_dir().mkdir(parents=True, exist_ok=True)
        disk_path.write_bytes(png)
    except OSError:
        pass
    _memory_put(digest, png)
    return png


def clear_cache() -> None:
    """Wipe both cache tiers. Intended for tests and the future
    //vacuum-style maintenance path."""
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
    # Move-to-end for LRU semantics on the plain dict.
    _MEMORY_CACHE.pop(digest)
    _MEMORY_CACHE[digest] = data
    return data


def _memory_put(digest: str, data: bytes) -> None:
    _MEMORY_CACHE[digest] = data
    while len(_MEMORY_CACHE) > _MEMORY_CACHE_MAX:
        # Drop the oldest-inserted entry (iteration order is
        # insertion order on modern Python dicts).
        oldest = next(iter(_MEMORY_CACHE))
        _MEMORY_CACHE.pop(oldest, None)
