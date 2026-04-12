# ------------------------------------------------------------------
# Component: dot_markdown_ext
# Responsibility: A python-markdown Extension that intercepts
#                 ```dot ... ``` fenced code blocks and rewrites
#                 them into a <div class="mchat-dot"> containing an
#                 <img src="mchat-graph://<hash>.png"> placeholder
#                 plus a <details> source-fallback block. The
#                 rendered PNG bytes are resolved later by the
#                 consumer — ChatWidget via document.addResource,
#                 HtmlExporter via base64 data URI replacement.
#
#                 The extension also records the hash→source pair
#                 in a module-level DOT_SOURCE_MAP so consumers can
#                 look up the original source without having to
#                 re-parse the markdown.
# Collaborators: markdown.Extension, markdown.preprocessors,
#                html (for escaping the source in the <details>),
#                hashlib.
# ------------------------------------------------------------------
from __future__ import annotations

import hashlib
import html as _html
import re

from markdown import Extension
from markdown.preprocessors import Preprocessor

# Module-level source map: sha256 hex digest → original DOT source.
# Populated by the preprocessor on every convert() call. Consumers
# (ChatWidget.loadResource, HtmlExporter._render) look up the source
# by digest when they need to render or re-render. The map grows
# unbounded within a process, but entries are tiny (a few KiB each)
# and users rarely generate thousands of unique graphs per session.
DOT_SOURCE_MAP: dict[str, str] = {}


# Priority must be higher than fenced_code_block (25) so we run first
# and pluck the ```dot``` fences before the generic fence handler
# converts them into <pre><code class="language-dot">.
_PRIORITY = 27


_FENCE_OPEN_RE = re.compile(r"^```dot\s*$")
_FENCE_CLOSE_RE = re.compile(r"^```\s*$")


def _build_placeholder_html(source: str, digest: str) -> str:
    """Return the HTML we stash in md.htmlStash for one dot block.

    The <img>'s src is an app-internal URL — the ChatWidget
    resolves `mchat-graph://<hash>.png` via loadResource(), and the
    HtmlExporter rewrites it to a base64 data: URI before writing
    the file. The <details> block is always emitted so the raw
    source is recoverable even when no renderer is available.
    """
    escaped = _html.escape(source)
    return (
        f'<div class="mchat-dot">'
        f'<img src="mchat-graph://{digest}.svg" alt="dot graph"/>'
        f'<details><summary>dot source</summary>'
        f'<pre><code class="language-dot">{escaped}</code></pre>'
        f'</details>'
        f'</div>'
    )


class DotFencePreprocessor(Preprocessor):
    """Scan the raw markdown lines for ```dot ... ``` fences.

    A matched fence is replaced with a single line containing a raw-
    HTML stash placeholder, so the core markdown parser leaves our
    HTML alone on the way through. The source is hashed and stored
    in DOT_SOURCE_MAP before stashing the placeholder.
    """

    def run(self, lines: list[str]) -> list[str]:
        out: list[str] = []
        i = 0
        n = len(lines)
        while i < n:
            if _FENCE_OPEN_RE.match(lines[i]):
                # Look for the matching close fence.
                j = i + 1
                while j < n and not _FENCE_CLOSE_RE.match(lines[j]):
                    j += 1
                if j >= n:
                    # Unclosed — fall through; leave the text alone
                    # so fenced_code can handle it (or not).
                    out.append(lines[i])
                    i += 1
                    continue
                # Matched pair: [i+1 .. j-1] are the source lines.
                source = "\n".join(lines[i + 1 : j])
                if not source.strip():
                    # Empty body — skip the whole fence block
                    # without producing a placeholder.
                    i = j + 1
                    continue
                digest = hashlib.sha256(
                    source.encode("utf-8")
                ).hexdigest()
                DOT_SOURCE_MAP[digest] = source
                placeholder = self.md.htmlStash.store(
                    _build_placeholder_html(source, digest)
                )
                out.append(placeholder)
                i = j + 1
                continue
            out.append(lines[i])
            i += 1
        return out


class DotExtension(Extension):
    """python-markdown Extension registering the dot fence preprocessor."""

    def extendMarkdown(self, md) -> None:
        md.preprocessors.register(
            DotFencePreprocessor(md), "mchat_dot_fence", _PRIORITY
        )


def makeExtension(**kwargs) -> DotExtension:
    """python-markdown extension factory."""
    return DotExtension(**kwargs)
