# ------------------------------------------------------------------
# Component: test_mermaid_markdown_ext
# Responsibility: Unit tests for the Markdown extension that rewrites
#                 ```mermaid``` fenced blocks into <img> placeholders
#                 that later get resolved against rendered PNGs.
# Collaborators: mchat.ui.mermaid_markdown_ext, markdown
# ------------------------------------------------------------------
from __future__ import annotations

import hashlib

import markdown
import pytest

from mchat.ui import mermaid_markdown_ext


@pytest.fixture(autouse=True)
def _reset_source_map():
    mermaid_markdown_ext.MERMAID_SOURCE_MAP.clear()
    yield
    mermaid_markdown_ext.MERMAID_SOURCE_MAP.clear()


def _convert(source_md: str) -> str:
    md = markdown.Markdown(
        extensions=[
            "tables",
            "fenced_code",
            "sane_lists",
            mermaid_markdown_ext.MermaidExtension(),
        ]
    )
    return md.convert(source_md)


SIMPLE_MERMAID = "graph TD\n  A --> B"
SIMPLE_MD = f"Intro\n\n```mermaid\n{SIMPLE_MERMAID}\n```\n\nOutro"


class TestFenceRewrite:
    def test_mermaid_fence_becomes_img_and_details(self):
        html = _convert(SIMPLE_MD)
        assert 'class="mchat-mermaid"' in html
        assert 'src="mchat-mermaid://' in html
        assert "<details" in html
        assert "<summary" in html

    def test_source_is_preserved_in_details_block(self):
        html = _convert(SIMPLE_MD)
        import html as _h

        assert _h.escape(SIMPLE_MERMAID) in html

    def test_intro_and_outro_still_rendered(self):
        html = _convert(SIMPLE_MD)
        assert "Intro" in html
        assert "Outro" in html


class TestSourceMap:
    def test_convert_populates_source_map(self):
        _convert(SIMPLE_MD)
        assert mermaid_markdown_ext.MERMAID_SOURCE_MAP
        digest = hashlib.sha256(SIMPLE_MERMAID.encode("utf-8")).hexdigest()
        assert mermaid_markdown_ext.MERMAID_SOURCE_MAP[digest] == SIMPLE_MERMAID

    def test_hash_is_stable_for_identical_source(self):
        html_a = _convert(SIMPLE_MD)
        mermaid_markdown_ext.MERMAID_SOURCE_MAP.clear()
        html_b = _convert(SIMPLE_MD)
        digest = hashlib.sha256(SIMPLE_MERMAID.encode("utf-8")).hexdigest()
        assert f"mchat-mermaid://{digest}.png" in html_a
        assert f"mchat-mermaid://{digest}.png" in html_b

    def test_different_sources_produce_different_urls(self):
        html_a = _convert("```mermaid\ngraph TD\n  A --> B\n```")
        html_b = _convert("```mermaid\ngraph TD\n  C --> D\n```")
        digest_a = hashlib.sha256(
            "graph TD\n  A --> B".encode("utf-8")
        ).hexdigest()
        digest_b = hashlib.sha256(
            "graph TD\n  C --> D".encode("utf-8")
        ).hexdigest()
        assert digest_a in html_a
        assert digest_b in html_b
        assert digest_a not in html_b
        assert digest_b not in html_a


class TestRegressionGuards:
    def test_python_fence_is_untouched(self):
        md_in = "```python\nprint('hi')\n```"
        html = _convert(md_in)
        assert "mchat-mermaid" not in html
        assert "print" in html

    def test_inline_mermaid_mention_is_untouched(self):
        md_in = "I used `mermaid` to draw a diagram."
        html = _convert(md_in)
        assert "mchat-mermaid" not in html

    def test_unclosed_mermaid_fence_falls_through(self):
        md_in = "```mermaid\ngraph TD\n  A --> B"
        html = _convert(md_in)
        assert "mchat-mermaid://" not in html
        assert not mermaid_markdown_ext.MERMAID_SOURCE_MAP

    def test_empty_mermaid_fence_skipped(self):
        md_in = "```mermaid\n```"
        html = _convert(md_in)
        assert "mchat-mermaid://" not in html
        assert not mermaid_markdown_ext.MERMAID_SOURCE_MAP


class TestIntegrationWithOtherExtensions:
    def test_table_next_to_mermaid_block(self):
        md_in = (
            "| col1 | col2 |\n"
            "|------|------|\n"
            "| a    | b    |\n"
            "\n"
            f"```mermaid\n{SIMPLE_MERMAID}\n```\n"
        )
        html = _convert(md_in)
        assert "<table>" in html
        assert "mchat-mermaid://" in html
