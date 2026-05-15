"""HTML renderer for Yomitan structured-content nodes.

Walks the Yomitan term-bank glossary tree and emits sanitized HTML.
Output is stored as-is in the `content` column at import time; the
runtime path is a literal SELECT.
"""

from __future__ import annotations

from html import escape
from typing import Any

_ALLOWED_TAGS = frozenset(
    {
        "div",
        "span",
        "ul",
        "ol",
        "li",
        "a",
        "img",
        "table",
        "td",
        "th",
        "tr",
        "br",
        "b",
        "i",
        "em",
        "strong",
    }
)
_VOID_TAGS = frozenset({"br", "img"})


def structured_content_to_html(node: Any) -> str:
    """Render a Yomitan structured-content node to HTML.

    Args:
        node: A string, list, or dict per Yomitan's term-bank schema.

    Returns:
        HTML string. Unknown tags become <span>; plain strings are escaped.
    """
    if isinstance(node, str):
        return escape(node)

    if isinstance(node, list):
        return "".join(structured_content_to_html(child) for child in node)

    if not isinstance(node, dict):
        return ""

    # Yomitan wraps top-level entries in {"type": "structured-content", "content": ...}
    if node.get("type") == "structured-content":
        return structured_content_to_html(node.get("content", ""))

    tag = node.get("tag", "span")
    if tag not in _ALLOWED_TAGS:
        tag = "span"

    attrs = _render_attrs(node)

    if tag in _VOID_TAGS:
        return f"<{tag}{attrs}>"

    inner = structured_content_to_html(node.get("content", ""))
    return f"<{tag}{attrs}>{inner}</{tag}>"


def _render_attrs(node: dict[str, Any]) -> str:
    parts: list[str] = []

    classes: list[str] = []
    data = node.get("data")
    if isinstance(data, dict):
        for key, value in data.items():
            classes.append(f"data-{key}-{value}")
    if classes:
        parts.append(f'class="{escape(" ".join(classes))}"')

    href = node.get("href")
    if isinstance(href, str) and node.get("tag") == "a":
        parts.append(f'href="{escape(href, quote=True)}"')

    path = node.get("path")
    if isinstance(path, str) and node.get("tag") == "img":
        parts.append(f'src="{escape(path, quote=True)}"')

    return (" " + " ".join(parts)) if parts else ""


def render_glossary_entry(
    glossary: list[Any],
    term_tags: list[str] | None = None,
    tag_bank: dict[str, dict[str, Any]] | None = None,
) -> str:
    """Render a Yomitan term-bank entry's glossary array + tags to HTML.

    Args:
        glossary: The 6th element of a term-bank tuple. Each item is a plain
                  string or a structured-content node.
        term_tags: Tag names (space-separated string is split by Yomitan but
                   we accept a pre-split list).
        tag_bank: Mapping from tag name to tag metadata (category, notes).
                  When omitted, tags render as bare badges.

    Returns:
        Concatenated HTML: tag badges followed by glossary entries.
    """
    parts: list[str] = []

    if term_tags:
        for tag_name in term_tags:
            meta = (tag_bank or {}).get(tag_name) or {}
            category = meta.get("category", "")
            css_class = f"tag tag-{category}" if category else "tag"
            parts.append(f'<span class="{escape(css_class, quote=True)}">{escape(tag_name)}</span>')

    for item in glossary:
        if isinstance(item, str):
            parts.append(f"<div>{escape(item)}</div>")
        else:
            parts.append(structured_content_to_html(item))

    return "".join(parts)
