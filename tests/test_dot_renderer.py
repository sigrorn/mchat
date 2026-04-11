# ------------------------------------------------------------------
# Component: test_dot_renderer
# Responsibility: Unit tests for mchat.dot_renderer — graphviz
#                 shell-out with two-tier (memory + disk) cache,
#                 failure handling, and graceful degradation when
#                 the `dot` binary is missing.
# Collaborators: mchat.dot_renderer, subprocess, shutil
# ------------------------------------------------------------------
from __future__ import annotations

import subprocess

import pytest

from mchat import dot_renderer

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
DUMMY_PNG = PNG_MAGIC + b"fake-png-payload"

VALID_DOT = "digraph { a -> b }"


@pytest.fixture(autouse=True)
def _reset_state(tmp_path, monkeypatch):
    """Redirect the disk cache at a tmp dir and wipe in-memory state
    before and after every test so tests can't see each other's caches."""
    monkeypatch.setattr(
        dot_renderer, "cache_dir", lambda: tmp_path / "graph_cache"
    )
    dot_renderer._MEMORY_CACHE.clear()
    dot_renderer.is_graphviz_available.cache_clear()
    yield
    dot_renderer._MEMORY_CACHE.clear()
    dot_renderer.is_graphviz_available.cache_clear()


def _fake_dot_present(monkeypatch):
    """Pretend `dot` is installed at a fake path."""
    monkeypatch.setattr(
        dot_renderer.shutil, "which",
        lambda name: "/usr/bin/dot" if name == "dot" else None,
    )
    dot_renderer.is_graphviz_available.cache_clear()


def _fake_subprocess_success(monkeypatch):
    """Replace subprocess.run with a stub that returns DUMMY_PNG.
    Returns a dict with 'n' — the number of times run() was called."""
    counter = {"n": 0}

    def fake_run(cmd, **kwargs):
        counter["n"] += 1
        return subprocess.CompletedProcess(
            cmd, returncode=0, stdout=DUMMY_PNG, stderr=b""
        )

    monkeypatch.setattr(dot_renderer.subprocess, "run", fake_run)
    return counter


class TestRenderDotHappyPath:
    def test_returns_png_bytes(self, monkeypatch):
        _fake_dot_present(monkeypatch)
        _fake_subprocess_success(monkeypatch)
        result = dot_renderer.render_dot(VALID_DOT)
        assert result is not None
        assert result.startswith(PNG_MAGIC)


class TestInputGuards:
    def test_empty_source_returns_none(self, monkeypatch):
        _fake_dot_present(monkeypatch)
        counter = _fake_subprocess_success(monkeypatch)
        assert dot_renderer.render_dot("") is None
        assert counter["n"] == 0

    def test_whitespace_only_source_returns_none(self, monkeypatch):
        _fake_dot_present(monkeypatch)
        counter = _fake_subprocess_success(monkeypatch)
        assert dot_renderer.render_dot("   \n\t ") is None
        assert counter["n"] == 0

    def test_oversized_source_skips_subprocess(self, monkeypatch):
        """Pathological-input guard: sources over 64 KiB must be
        rejected without spawning dot at all."""
        _fake_dot_present(monkeypatch)
        counter = _fake_subprocess_success(monkeypatch)
        huge = "x" * 65537  # one byte past the 64 KiB cap
        assert dot_renderer.render_dot(huge) is None
        assert counter["n"] == 0


class TestSubprocessFailures:
    def test_nonzero_exit_returns_none(self, monkeypatch):
        _fake_dot_present(monkeypatch)
        call = {"n": 0}

        def fake_run(cmd, **kwargs):
            call["n"] += 1
            return subprocess.CompletedProcess(
                cmd, returncode=1, stdout=b"", stderr=b"syntax error"
            )

        monkeypatch.setattr(dot_renderer.subprocess, "run", fake_run)
        assert dot_renderer.render_dot("this is not DOT") is None
        assert call["n"] == 1  # subprocess ran, graceful failure

    def test_timeout_returns_none(self, monkeypatch):
        _fake_dot_present(monkeypatch)

        def fake_run(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=5.0)

        monkeypatch.setattr(dot_renderer.subprocess, "run", fake_run)
        assert dot_renderer.render_dot(VALID_DOT) is None

    def test_oserror_returns_none(self, monkeypatch):
        _fake_dot_present(monkeypatch)

        def fake_run(cmd, **kwargs):
            raise OSError("process spawn failed")

        monkeypatch.setattr(dot_renderer.subprocess, "run", fake_run)
        assert dot_renderer.render_dot(VALID_DOT) is None


class TestGraphvizMissing:
    def test_render_returns_none_when_dot_missing(self, monkeypatch):
        monkeypatch.setattr(dot_renderer.shutil, "which", lambda name: None)
        dot_renderer.is_graphviz_available.cache_clear()
        assert dot_renderer.render_dot(VALID_DOT) is None

    def test_availability_reports_false_when_dot_missing(self, monkeypatch):
        monkeypatch.setattr(dot_renderer.shutil, "which", lambda name: None)
        dot_renderer.is_graphviz_available.cache_clear()
        assert dot_renderer.is_graphviz_available() is False

    def test_is_graphviz_available_is_memoised(self, monkeypatch):
        calls = {"n": 0}

        def fake_which(name):
            calls["n"] += 1
            return "/usr/bin/dot" if name == "dot" else None

        monkeypatch.setattr(dot_renderer.shutil, "which", fake_which)
        dot_renderer.is_graphviz_available.cache_clear()
        assert dot_renderer.is_graphviz_available() is True
        assert dot_renderer.is_graphviz_available() is True
        assert dot_renderer.is_graphviz_available() is True
        # shutil.which invoked exactly once thanks to the lru_cache
        assert calls["n"] == 1


class TestCaching:
    def test_in_memory_cache_skips_subprocess_on_repeat(self, monkeypatch):
        _fake_dot_present(monkeypatch)
        counter = _fake_subprocess_success(monkeypatch)
        dot_renderer.render_dot(VALID_DOT)
        dot_renderer.render_dot(VALID_DOT)
        dot_renderer.render_dot(VALID_DOT)
        assert counter["n"] == 1

    def test_disk_cache_serves_after_memory_clear(self, monkeypatch):
        """Simulate a same-session memory-cache purge: the disk cache
        should still serve the render without spawning `dot` again."""
        _fake_dot_present(monkeypatch)
        counter = _fake_subprocess_success(monkeypatch)
        first = dot_renderer.render_dot(VALID_DOT)
        assert counter["n"] == 1
        dot_renderer._MEMORY_CACHE.clear()
        second = dot_renderer.render_dot(VALID_DOT)
        assert counter["n"] == 1  # still 1 — disk served it
        assert second == first

    def test_cross_session_persistence(self, monkeypatch):
        """Simulate an app restart: both in-memory caches and the
        availability lru_cache are wiped, but the disk cache at
        ~/.mchat/graph_cache/<hash>.png is still there, and
        render_dot returns the same bytes with no fresh subprocess."""
        _fake_dot_present(monkeypatch)
        counter = _fake_subprocess_success(monkeypatch)
        first = dot_renderer.render_dot(VALID_DOT)
        assert counter["n"] == 1
        # Wipe everything except the disk cache, then re-enter.
        dot_renderer._MEMORY_CACHE.clear()
        dot_renderer.is_graphviz_available.cache_clear()
        _fake_dot_present(monkeypatch)  # re-install the which() stub
        second = dot_renderer.render_dot(VALID_DOT)
        assert counter["n"] == 1
        assert second == first

    def test_different_sources_produce_different_cache_entries(
        self, monkeypatch
    ):
        _fake_dot_present(monkeypatch)
        counter = _fake_subprocess_success(monkeypatch)
        dot_renderer.render_dot("digraph { a -> b }")
        dot_renderer.render_dot("digraph { c -> d }")
        assert counter["n"] == 2

    def test_clear_cache_wipes_both_tiers(self, monkeypatch, tmp_path):
        _fake_dot_present(monkeypatch)
        counter = _fake_subprocess_success(monkeypatch)
        dot_renderer.render_dot(VALID_DOT)
        assert counter["n"] == 1
        assert dot_renderer._MEMORY_CACHE  # something in-memory
        cache_files = list((tmp_path / "graph_cache").glob("*.png"))
        assert len(cache_files) == 1  # something on disk

        dot_renderer.clear_cache()
        assert dot_renderer._MEMORY_CACHE == {}
        assert list((tmp_path / "graph_cache").glob("*.png")) == []
        # Next render re-runs the subprocess.
        dot_renderer.render_dot(VALID_DOT)
        assert counter["n"] == 2

    def test_disk_cache_hash_is_sha256_of_source(self, monkeypatch, tmp_path):
        import hashlib

        _fake_dot_present(monkeypatch)
        _fake_subprocess_success(monkeypatch)
        dot_renderer.render_dot(VALID_DOT)
        expected = hashlib.sha256(VALID_DOT.encode("utf-8")).hexdigest()
        assert (tmp_path / "graph_cache" / f"{expected}.png").exists()
