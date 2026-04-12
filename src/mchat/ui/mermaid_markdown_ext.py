# ------------------------------------------------------------------
# Component: mermaid_markdown_ext
# Responsibility: A python-markdown Extension that intercepts
#                 ```mermaid ... ``` fenced code blocks and rewrites
#                 them into a <div class="mchat-mermaid"> containing
#                 an <img src="mchat-mermaid://<hash>.png"> placeholder
#                 plus a <details> source-fallback block. The rendered
#                 PNG bytes are resolved later by the consumer —
#                 ChatWidget via document.addResource, HtmlExporter
#                 via base64 data URI replacement.
#
#                 The extension also records the hash->source pair
#                 in a module-level MERMAID_SOURCE_MAP so consumers
#                 can look up the original source without re-parsing.
# Collaborators: markdown.Extension, markdown.preprocessors,
#                html (for escaping), hashlib.
# ------------------------------------------------------------------
from __future__ import annotations

import hashlib
import html as _html
import re

from markdown import Extension
from markdown.preprocessors import Preprocessor

# Module-level source map: sha256 hex digest -> original Mermaid source.
MERMAID_SOURCE_MAP: dict[str, str] = {}

# Priority must be higher than fenced_code_block (25) so we run first.
_PRIORITY = 28

_FENCE_OPEN_RE = re.compile(r"^```mermaid\s*$")
_FENCE_CLOSE_RE = re.compile(r"^```\s*$")


def _build_placeholder_html(source: str, digest: str) -> str:
    """Return the HTML we stash in md.htmlStash for one mermaid block."""
    escaped = _html.escape(source)
    return (
        f'<div class="mchat-mermaid">'
        f'<img src="mchat-mermaid://{digest}.svg" alt="mermaid diagram"/>'
        f'<details><summary>mermaid source</summary>'
        f'<pre><code class="language-mermaid">{escaped}</code></pre>'
        f'</details>'
        f'</div>'
    )


class MermaidFencePreprocessor(Preprocessor):
    """Scan the raw markdown lines for ```mermaid ... ``` fences."""

    def run(self, lines: list[str]) -> list[str]:
        out: list[str] = []
        i = 0
        n = len(lines)
        while i < n:
            if _FENCE_OPEN_RE.match(lines[i]):
                j = i + 1
                while j < n and not _FENCE_CLOSE_RE.match(lines[j]):
                    j += 1
                if j >= n:
                    out.append(lines[i])
                    i += 1
                    continue
                source = "\n".join(lines[i + 1 : j])
                if not source.strip():
                    i = j + 1
                    continue
                digest = hashlib.sha256(
                    source.encode("utf-8")
                ).hexdigest()
                MERMAID_SOURCE_MAP[digest] = source
                placeholder = self.md.htmlStash.store(
                    _build_placeholder_html(source, digest)
                )
                out.append(placeholder)
                i = j + 1
                continue
            out.append(lines[i])
            i += 1
        return out


class MermaidExtension(Extension):
    """python-markdown Extension registering the mermaid fence preprocessor."""

    def extendMarkdown(self, md) -> None:
        md.preprocessors.register(
            MermaidFencePreprocessor(md), "mchat_mermaid_fence", _PRIORITY
        )


def makeExtension(**kwargs) -> MermaidExtension:
    """python-markdown extension factory."""
    return MermaidExtension(**kwargs)
