# ------------------------------------------------------------------
# Component: test_mermaid_renderer
# Responsibility: Unit tests for mchat.mermaid_renderer — mmdc
#                 shell-out with two-tier (memory + disk) cache,
#                 failure handling, and graceful degradation when
#                 the `mmdc` binary is missing.
#                 #152: renders to SVG for scalable diagrams.
# Collaborators: mchat.mermaid_renderer, subprocess, shutil
# ------------------------------------------------------------------
from __future__ import annotations

import subprocess

import pytest

from mchat import mermaid_renderer

DUMMY_SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><text>test</text></svg>'

VALID_MERMAID = "graph TD\n  A --> B"


@pytest.fixture(autouse=True)
def _reset_state(tmp_path, monkeypatch):
    """Redirect the disk cache at a tmp dir and wipe in-memory state
    before and after every test so tests can't see each other's caches."""
    monkeypatch.setattr(
        mermaid_renderer, "cache_dir", lambda: tmp_path / "mermaid_cache"
    )
    mermaid_renderer._MEMORY_CACHE.clear()
    mermaid_renderer.is_mmdc_available.cache_clear()
    yield
    mermaid_renderer._MEMORY_CACHE.clear()
    mermaid_renderer.is_mmdc_available.cache_clear()


def _fake_mmdc_present(monkeypatch):
    """Pretend `mmdc` is installed at a fake path."""
    monkeypatch.setattr(
        mermaid_renderer.shutil, "which",
        lambda name: "/usr/local/bin/mmdc" if name == "mmdc" else None,
    )
    mermaid_renderer.is_mmdc_available.cache_clear()


def _fake_subprocess_success(monkeypatch, tmp_path):
    """Replace subprocess.run with a stub that writes DUMMY_SVG to the
    output file path extracted from the command args.
    Returns a dict with 'n' — the number of times run() was called."""
    counter = {"n": 0}

    def fake_run(cmd, **kwargs):
        counter["n"] += 1
        # Find the -o flag and write SVG to that path
        for i, arg in enumerate(cmd):
            if arg == "-o" and i + 1 < len(cmd):
                from pathlib import Path
                Path(cmd[i + 1]).write_bytes(DUMMY_SVG)
                break
        return subprocess.CompletedProcess(
            cmd, returncode=0, stdout=b"", stderr=b""
        )

    monkeypatch.setattr(mermaid_renderer.subprocess, "run", fake_run)
    return counter


class TestRenderMermaidHappyPath:
    def test_returns_svg_bytes(self, monkeypatch, tmp_path):
        _fake_mmdc_present(monkeypatch)
        counter = _fake_subprocess_success(monkeypatch, tmp_path)
        result = mermaid_renderer.render_mermaid(VALID_MERMAID)
        assert result is not None
        assert b"<svg" in result


class TestInputGuards:
    def test_empty_source_returns_none(self, monkeypatch, tmp_path):
        _fake_mmdc_present(monkeypatch)
        counter = _fake_subprocess_success(monkeypatch, tmp_path)
        assert mermaid_renderer.render_mermaid("") is None
        assert counter["n"] == 0

    def test_whitespace_only_source_returns_none(self, monkeypatch, tmp_path):
        _fake_mmdc_present(monkeypatch)
        counter = _fake_subprocess_success(monkeypatch, tmp_path)
        assert mermaid_renderer.render_mermaid("   \n\t ") is None
        assert counter["n"] == 0

    def test_oversized_source_skips_subprocess(self, monkeypatch, tmp_path):
        """Pathological-input guard: sources over 64 KiB must be
        rejected without spawning mmdc at all."""
        _fake_mmdc_present(monkeypatch)
        counter = _fake_subprocess_success(monkeypatch, tmp_path)
        huge = "x" * 65537
        assert mermaid_renderer.render_mermaid(huge) is None
        assert counter["n"] == 0


class TestSubprocessFailures:
    def test_nonzero_exit_returns_none(self, monkeypatch):
        _fake_mmdc_present(monkeypatch)

        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(
                cmd, returncode=1, stdout=b"", stderr=b"parse error"
            )

        monkeypatch.setattr(mermaid_renderer.subprocess, "run", fake_run)
        assert mermaid_renderer.render_mermaid(VALID_MERMAID) is None

    def test_timeout_returns_none(self, monkeypatch):
        _fake_mmdc_present(monkeypatch)

        def fake_run(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=15.0)

        monkeypatch.setattr(mermaid_renderer.subprocess, "run", fake_run)
        assert mermaid_renderer.render_mermaid(VALID_MERMAID) is None

    def test_oserror_returns_none(self, monkeypatch):
        _fake_mmdc_present(monkeypatch)

        def fake_run(cmd, **kwargs):
            raise OSError("process spawn failed")

        monkeypatch.setattr(mermaid_renderer.subprocess, "run", fake_run)
        assert mermaid_renderer.render_mermaid(VALID_MERMAID) is None


class TestMmdcMissing:
    def test_render_returns_none_when_mmdc_missing(self, monkeypatch):
        monkeypatch.setattr(mermaid_renderer.shutil, "which", lambda name: None)
        mermaid_renderer.is_mmdc_available.cache_clear()
        assert mermaid_renderer.render_mermaid(VALID_MERMAID) is None

    def test_availability_reports_false_when_mmdc_missing(self, monkeypatch):
        monkeypatch.setattr(mermaid_renderer.shutil, "which", lambda name: None)
        mermaid_renderer.is_mmdc_available.cache_clear()
        assert mermaid_renderer.is_mmdc_available() is False

    def test_is_mmdc_available_is_memoised(self, monkeypatch):
        calls = {"n": 0}

        def fake_which(name):
            calls["n"] += 1
            return "/usr/local/bin/mmdc" if name == "mmdc" else None

        monkeypatch.setattr(mermaid_renderer.shutil, "which", fake_which)
        mermaid_renderer.is_mmdc_available.cache_clear()
        assert mermaid_renderer.is_mmdc_available() is True
        assert mermaid_renderer.is_mmdc_available() is True
        assert mermaid_renderer.is_mmdc_available() is True
        assert calls["n"] == 1


class TestCaching:
    def test_in_memory_cache_skips_subprocess_on_repeat(self, monkeypatch, tmp_path):
        _fake_mmdc_present(monkeypatch)
        counter = _fake_subprocess_success(monkeypatch, tmp_path)
        mermaid_renderer.render_mermaid(VALID_MERMAID)
        mermaid_renderer.render_mermaid(VALID_MERMAID)
        mermaid_renderer.render_mermaid(VALID_MERMAID)
        assert counter["n"] == 1

    def test_disk_cache_serves_after_memory_clear(self, monkeypatch, tmp_path):
        _fake_mmdc_present(monkeypatch)
        counter = _fake_subprocess_success(monkeypatch, tmp_path)
        first = mermaid_renderer.render_mermaid(VALID_MERMAID)
        assert counter["n"] == 1
        mermaid_renderer._MEMORY_CACHE.clear()
        second = mermaid_renderer.render_mermaid(VALID_MERMAID)
        assert counter["n"] == 1
        assert second == first

    def test_cross_session_persistence(self, monkeypatch, tmp_path):
        _fake_mmdc_present(monkeypatch)
        counter = _fake_subprocess_success(monkeypatch, tmp_path)
        first = mermaid_renderer.render_mermaid(VALID_MERMAID)
        assert counter["n"] == 1
        mermaid_renderer._MEMORY_CACHE.clear()
        mermaid_renderer.is_mmdc_available.cache_clear()
        _fake_mmdc_present(monkeypatch)
        second = mermaid_renderer.render_mermaid(VALID_MERMAID)
        assert counter["n"] == 1
        assert second == first

    def test_different_sources_produce_different_cache_entries(
        self, monkeypatch, tmp_path
    ):
        _fake_mmdc_present(monkeypatch)
        counter = _fake_subprocess_success(monkeypatch, tmp_path)
        mermaid_renderer.render_mermaid("graph TD\n  A --> B")
        mermaid_renderer.render_mermaid("graph TD\n  C --> D")
        assert counter["n"] == 2

    def test_clear_cache_wipes_both_tiers(self, monkeypatch, tmp_path):
        _fake_mmdc_present(monkeypatch)
        counter = _fake_subprocess_success(monkeypatch, tmp_path)
        mermaid_renderer.render_mermaid(VALID_MERMAID)
        assert counter["n"] == 1
        assert mermaid_renderer._MEMORY_CACHE
        cache_files = list((tmp_path / "mermaid_cache").glob("*.svg"))
        assert len(cache_files) == 1

        mermaid_renderer.clear_cache()
        assert mermaid_renderer._MEMORY_CACHE == {}
        assert list((tmp_path / "mermaid_cache").glob("*.svg")) == []
        mermaid_renderer.render_mermaid(VALID_MERMAID)
        assert counter["n"] == 2

    def test_disk_cache_hash_is_sha256_of_source(self, monkeypatch, tmp_path):
        import hashlib

        _fake_mmdc_present(monkeypatch)
        _fake_subprocess_success(monkeypatch, tmp_path)
        mermaid_renderer.render_mermaid(VALID_MERMAID)
        expected = hashlib.sha256(VALID_MERMAID.encode("utf-8")).hexdigest()
        assert (tmp_path / "mermaid_cache" / f"{expected}.svg").exists()
