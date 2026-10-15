"""Turn a plain dictionary's definition markup into one sanitized glossary row.

StarDict ``h``/``g``/``x`` fields and CSV definitions arrive as untrusted text.
Nothing here emits HTML itself: the markup is parsed into a Yomitan
structured-content tree and rendered by ``render_glossary_entry``, so the
renderer's tag, attribute and style allowlists are the only sanitizer and the
row has the same ``<li class="gloss-item">`` shape as a Yomitan row.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any

from anki_miner.services.dictionary.yomitan_renderer import render_glossary_entry

# Kept as themselves; the renderer allowlists them too. Anything else (font, a,
# sup, xdxf's k/dtrn/ex, ...) becomes a plain span so its text survives.
_KEPT_TAGS = frozenset(
    {
        "b",
        "i",
        "em",
        "strong",
        "div",
        "span",
        "p",
        "ul",
        "ol",
        "li",
        "dl",
        "dt",
        "dd",
        "table",
        "thead",
        "tbody",
        "tfoot",
        "tr",
        "td",
        "th",
        "ruby",
        "rt",
        "rp",
        "rb",
    }
)
# Dropped with everything inside them.
_SKIPPED_TAGS = frozenset({"script", "style", "head", "title", "object", "iframe", "svg", "math"})
# Never take content; ``br`` is the only one kept.
_VOID_TAGS = frozenset({"br", "img", "hr", "meta", "link", "input", "wbr", "area", "base", "col", "source"})
# Below the renderer's _MAX_DEPTH (100), which would drop a deeper subtree.
_MAX_DEPTH = 40
_WHITESPACE = re.compile(r"[ \t\r\n\f]+")

Node = dict[str, Any]


class _TreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root: Node = {"tag": "span", "content": []}
        self._stack: list[tuple[str, Node]] = [("", self.root)]
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._skip_depth:
            if tag in _SKIPPED_TAGS:
                self._skip_depth += 1
            return
        if tag in _SKIPPED_TAGS:
            self._skip_depth = 1
            return
        if tag in _VOID_TAGS:
            if tag == "br":
                self._stack[-1][1]["content"].append({"tag": "br"})
            return
        if len(self._stack) > _MAX_DEPTH:
            return
        node: Node = {"tag": tag if tag in _KEPT_TAGS else "span", "content": []}
        self._stack[-1][1]["content"].append(node)
        self._stack.append((tag, node))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if not self._skip_depth and tag == "br":
            self._stack[-1][1]["content"].append({"tag": "br"})

    def handle_endtag(self, tag: str) -> None:
        if self._skip_depth:
            if tag in _SKIPPED_TAGS:
                self._skip_depth -= 1
            return
        for depth in range(len(self._stack) - 1, 0, -1):
            if self._stack[depth][0] == tag:
                del self._stack[depth:]
                return

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        text = _WHITESPACE.sub(" ", data)
        if text:
            self._stack[-1][1]["content"].append(text)


def _has_text(node: Any) -> bool:
    if isinstance(node, str):
        return bool(node.strip())
    if isinstance(node, dict):
        return any(_has_text(child) for child in node.get("content", []))
    return False


def markup_nodes(markup: str) -> list[Any]:
    """Structured-content nodes for one HTML-like field; [] when it has no text."""
    builder = _TreeBuilder()
    builder.feed(markup)
    builder.close()
    content: list[Any] = builder.root["content"]
    return content if any(_has_text(node) for node in content) else []


def text_nodes(text: str) -> list[Any]:
    """Structured-content nodes for one plain-text field; [] when it is blank."""
    return [text] if text.strip() else []


def glossary_row(fields: list[list[Any]]) -> str:
    """One rendered row from the node lists of an entry's fields; "" when all are empty.

    Fields are joined with a line break, in order, into one definition.
    """
    content: list[Any] = []
    for nodes in fields:
        if not nodes:
            continue
        if content:
            content.append({"tag": "br"})
        content.extend(nodes)
    if not content:
        return ""
    return render_glossary_entry([{"type": "structured-content", "content": content}])
