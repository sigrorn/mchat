# ------------------------------------------------------------------
# Component: test_dot_markdown_ext
# Responsibility: Unit tests for the Markdown extension that rewrites
#                 ```dot``` fenced blocks into <img> placeholders
#                 that later get resolved against rendered PNGs.
# Collaborators: mchat.ui.dot_markdown_ext, markdown
# ------------------------------------------------------------------
from __future__ import annotations

import hashlib

import markdown
import pytest

from mchat.ui import dot_markdown_ext


@pytest.fixture(autouse=True)
def _reset_source_map():
    dot_markdown_ext.DOT_SOURCE_MAP.clear()
    yield
    dot_markdown_ext.DOT_SOURCE_MAP.clear()


def _convert(source_md: str) -> str:
    md = markdown.Markdown(
        extensions=[
            "tables",
            "fenced_code",
            "sane_lists",
            dot_markdown_ext.DotExtension(),
        ]
    )
    return md.convert(source_md)


SIMPLE_DOT = "digraph { a -> b }"
SIMPLE_MD = f"Intro\n\n```dot\n{SIMPLE_DOT}\n```\n\nOutro"


class TestFenceRewrite:
    def test_dot_fence_becomes_img_and_details(self):
        html = _convert(SIMPLE_MD)
        assert 'class="mchat-dot"' in html
        assert 'src="mchat-graph://' in html
        assert "<details" in html
        assert "<summary" in html

    def test_source_is_preserved_in_details_block(self):
        html = _convert(SIMPLE_MD)
        # The DOT source needs to survive end-to-end so the user can
        # read what was requested even if the image fails. HTML
        # escaping rewrites `->` as `-&gt;`, so assert on the
        # escaped form — both directions must be usable in practice.
        import html as _h

        assert _h.escape(SIMPLE_DOT) in html

    def test_intro_and_outro_still_rendered(self):
        html = _convert(SIMPLE_MD)
        assert "Intro" in html
        assert "Outro" in html


class TestSourceMap:
    def test_convert_populates_source_map(self):
        _convert(SIMPLE_MD)
        assert dot_markdown_ext.DOT_SOURCE_MAP
        # The map's value for the hash is the original DOT source.
        digest = hashlib.sha256(SIMPLE_DOT.encode("utf-8")).hexdigest()
        assert dot_markdown_ext.DOT_SOURCE_MAP[digest] == SIMPLE_DOT

    def test_hash_is_stable_for_identical_source(self):
        html_a = _convert(SIMPLE_MD)
        dot_markdown_ext.DOT_SOURCE_MAP.clear()
        html_b = _convert(SIMPLE_MD)
        # Same hash → same URL substring in both renders.
        digest = hashlib.sha256(SIMPLE_DOT.encode("utf-8")).hexdigest()
        assert f"mchat-graph://{digest}.png" in html_a
        assert f"mchat-graph://{digest}.png" in html_b

    def test_different_sources_produce_different_urls(self):
        html_a = _convert("```dot\ndigraph { a -> b }\n```")
        html_b = _convert("```dot\ndigraph { c -> d }\n```")
        # Each hash appears in exactly one output.
        digest_a = hashlib.sha256(
            "digraph { a -> b }".encode("utf-8")
        ).hexdigest()
        digest_b = hashlib.sha256(
            "digraph { c -> d }".encode("utf-8")
        ).hexdigest()
        assert digest_a in html_a
        assert digest_b in html_b
        assert digest_a not in html_b
        assert digest_b not in html_a


class TestRegressionGuards:
    def test_python_fence_is_untouched(self):
        md_in = "```python\nprint('hi')\n```"
        html = _convert(md_in)
        # Still rendered as a normal code block.
        assert "mchat-dot" not in html
        assert "mchat-graph://" not in html
        assert "print" in html
        # fenced_code emits <code class="language-python">...</code>
        assert "language-python" in html or "<code>" in html

    def test_inline_dot_mention_is_untouched(self):
        md_in = "I ran `dot -Tpng` on the file."
        html = _convert(md_in)
        assert "mchat-dot" not in html
        assert "dot -Tpng" in html

    def test_unclosed_dot_fence_falls_through(self):
        """A dot fence with no closing backticks must not corrupt
        the output — it falls through to normal fenced_code handling,
        which either renders it as an unclosed block or leaves the
        literal text in place."""
        md_in = "```dot\ndigraph { a -> b }"
        # Should not raise and should not populate the source map —
        # an unclosed fence isn't a valid DOT block.
        html = _convert(md_in)
        assert "mchat-graph://" not in html
        assert not dot_markdown_ext.DOT_SOURCE_MAP

    def test_empty_dot_fence_skipped(self):
        md_in = "```dot\n```"
        html = _convert(md_in)
        # Empty source shouldn't produce an <img> — nothing to render.
        assert "mchat-graph://" not in html
        assert not dot_markdown_ext.DOT_SOURCE_MAP


class TestIntegrationWithOtherExtensions:
    def test_table_next_to_dot_block(self):
        md_in = (
            "| col1 | col2 |\n"
            "|------|------|\n"
            "| a    | b    |\n"
            "\n"
            f"```dot\n{SIMPLE_DOT}\n```\n"
        )
        html = _convert(md_in)
        assert "<table>" in html
        assert "mchat-graph://" in html
