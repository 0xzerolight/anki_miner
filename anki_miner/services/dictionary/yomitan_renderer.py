"""HTML renderer for Yomitan structured-content nodes.

Walks the Yomitan term-bank glossary tree and emits sanitized HTML.
Output is stored as-is in the `content` column at import time; the
runtime path is a literal SELECT.
"""

from __future__ import annotations

import re
from html import escape
from typing import Any

_ALLOWED_TAGS = frozenset(
    {
        "div",
        "span",
        "p",
        "ul",
        "ol",
        "li",
        "dl",
        "dt",
        "dd",
        "a",
        "img",
        "table",
        "thead",
        "tbody",
        "tfoot",
        "tr",
        "td",
        "th",
        "br",
        "b",
        "i",
        "em",
        "strong",
        "ruby",
        "rt",
        "rp",
        "rb",
        "details",
        "summary",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
    }
)
_VOID_TAGS = frozenset({"br", "img"})

_WHITESPACE_RE = re.compile(r"\s+")
_CAMEL_RE = re.compile(r"([a-z0-9])([A-Z])")

# Yomitan structured-content trees are user-supplied data. Cap recursion so a
# pathological/malicious dict can't blow the Python stack mid-import.
_MAX_DEPTH = 100

# Inline CSS properties Yomitan dictionaries are allowed to emit. Mirrors the
# Yomitan structured-content style schema; anything outside this set is dropped.
_ALLOWED_STYLE_PROPS = frozenset(
    {
        "font-style",
        "font-weight",
        "font-size",
        "font-family",
        "color",
        "background",
        "background-color",
        "text-decoration",
        "text-decoration-line",
        "text-decoration-style",
        "text-decoration-color",
        "text-emphasis",
        "text-shadow",
        "vertical-align",
        "text-align",
        "border",
        "border-color",
        "border-style",
        "border-radius",
        "border-width",
        "margin",
        "margin-top",
        "margin-left",
        "margin-right",
        "margin-bottom",
        "padding",
        "padding-top",
        "padding-left",
        "padding-right",
        "padding-bottom",
        "word-break",
        "white-space",
        "list-style-type",
        "display",
    }
)

# Yomitan also exposes a few style props as siblings of `tag` (top-level
# shortcuts) instead of nested under `style`. Treat the union.
_STYLE_SHORTCUT_KEYS = frozenset(
    {
        "fontStyle",
        "fontWeight",
        "fontSize",
        "color",
        "background",
        "backgroundColor",
        "textDecorationLine",
        "textDecorationStyle",
        "textDecorationColor",
        "verticalAlign",
        "textAlign",
        "textEmphasis",
        "textShadow",
        "borderColor",
        "borderStyle",
        "borderRadius",
        "borderWidth",
        "margin",
        "marginTop",
        "marginLeft",
        "marginRight",
        "marginBottom",
        "padding",
        "paddingTop",
        "paddingLeft",
        "paddingRight",
        "paddingBottom",
        "wordBreak",
        "whiteSpace",
        "listStyleType",
    }
)

# Patterns/substrings forbidden inside any style value. CSS-escape sequences,
# function calls, and angle brackets are the realistic injection vectors here.
_STYLE_VALUE_BAD_RE = re.compile(
    r"""(?ix)
    (?:url\s*\() |
    (?:expression\s*\() |
    (?:javascript:) |
    (?:vbscript:) |
    (?:data:) |
    (?:@import) |
    [<>{};\\\"'`]
    """,
)

# Cap style values to head off pathological inputs without affecting real dicts;
# the longest legitimate Yomitan style value in practice is ~150 chars.
_MAX_STYLE_VALUE_LEN = 256

# data-* attribute names must match this; otherwise the key is dropped.
_DATA_KEY_RE = re.compile(r"^[a-z][a-z0-9_-]*$")

# Per-spec language tags are short ASCII. Reject anything that smells like
# injection (semicolons, quotes, brackets).
_LANG_RE = re.compile(r"^[A-Za-z0-9-]{1,35}$")

# Tag-specific HTML attribute whitelists. Yomitan SC allows these per its
# schema; dropping them silently lost layout in conjugation tables, expandable
# notes, and accessibility metadata on images.
_INT_ATTR_MAX = 1000  # colspan/rowspan/width/height cap
_TAG_STRING_ATTRS: dict[str, frozenset[str]] = {
    "td": frozenset({"title"}),
    "th": frozenset({"title"}),
    "img": frozenset({"alt", "title"}),
    "a": frozenset({"title"}),
    "div": frozenset({"title"}),
    "span": frozenset({"title"}),
    "details": frozenset({"title"}),
    "summary": frozenset({"title"}),
}
_TAG_INT_ATTRS: dict[str, frozenset[str]] = {
    "td": frozenset({"colspan", "rowspan"}),
    "th": frozenset({"colspan", "rowspan"}),
    "img": frozenset({"width", "height"}),
}
_TAG_BOOL_ATTRS: dict[str, frozenset[str]] = {
    "details": frozenset({"open"}),
}

# Yomitan keys are camelCase even for HTML attrs; map both forms.
_ATTR_KEY_ALIASES: dict[str, str] = {
    "colSpan": "colspan",
    "rowSpan": "rowspan",
}


def _is_safe_url(url: str) -> bool:
    """Return True if url uses an allowed scheme or is a relative path.

    Blocks javascript:, data:, vbscript:, file:, protocol-relative (//host),
    and any other scheme. Relative paths and same-page anchors pass.
    """
    if not isinstance(url, str):
        return False
    url = url.strip()
    if not url:
        return False
    if url.startswith("//"):
        return False
    # If there's a colon before any slash, it's a scheme — restrict the list
    colon = url.find(":")
    slash = url.find("/")
    if colon != -1 and (slash == -1 or colon < slash):
        scheme = url[:colon].lower()
        return scheme in ("http", "https", "mailto")
    return True  # relative path, fragment, query — all safe


def _camel_to_kebab(name: str) -> str:
    return _CAMEL_RE.sub(r"\1-\2", name).lower()


def _coerce_style_value(value: Any) -> str | None:
    """Stringify a Yomitan style value safely.

    Numbers become bare strings (Yomitan uses unitless ints for some props).
    Strings pass through after a bad-pattern scan.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return str(value)
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate:
        return None
    if len(candidate) > _MAX_STYLE_VALUE_LEN:
        return None
    if _STYLE_VALUE_BAD_RE.search(candidate):
        return None
    if any(ord(ch) < 0x20 for ch in candidate):
        return None
    return candidate


def _collect_style(node: dict[str, Any]) -> str:
    """Build an inline style="..." value from a node's style props.

    Reads both nested `style: {...}` and Yomitan's top-level shortcut keys
    (fontSize, verticalAlign, etc.). Only whitelisted CSS properties survive,
    and values are scrubbed for url()/expression()/quotes/braces.
    """
    # Dict preserves insertion order; nested `style:` block wins because it's
    # spec-canonical, top-level shortcuts overwrite only when nested didn't set.
    seen: dict[str, str] = {}

    style_block = node.get("style")
    if isinstance(style_block, dict):
        for key, value in style_block.items():
            if not isinstance(key, str):
                continue
            prop = _camel_to_kebab(key)
            if prop not in _ALLOWED_STYLE_PROPS:
                continue
            coerced = _coerce_style_value(value)
            if coerced is None:
                continue
            seen[prop] = coerced

    for key in _STYLE_SHORTCUT_KEYS:
        if key not in node:
            continue
        prop = _camel_to_kebab(key)
        if prop in seen or prop not in _ALLOWED_STYLE_PROPS:
            continue
        coerced = _coerce_style_value(node[key])
        if coerced is None:
            continue
        seen[prop] = coerced

    if not seen:
        return ""
    body = "; ".join(f"{prop}: {value}" for prop, value in seen.items())
    return f'style="{escape(body, quote=True)}"'


def structured_content_to_html(node: Any, _depth: int = 0) -> str:
    """Render a Yomitan structured-content node to HTML.

    Args:
        node: A string, list, or dict per Yomitan's term-bank schema.

    Returns:
        HTML string. Unknown tags become <span>; plain strings are escaped.
        Nodes deeper than _MAX_DEPTH are truncated to "" to bound stack use.
    """
    if _depth > _MAX_DEPTH:
        return ""

    if isinstance(node, str):
        return escape(node)

    if isinstance(node, list):
        return "".join(structured_content_to_html(child, _depth + 1) for child in node)

    if not isinstance(node, dict):
        return ""

    # Yomitan wraps top-level entries in {"type": "structured-content", "content": ...}
    if node.get("type") == "structured-content":
        return structured_content_to_html(node.get("content", ""), _depth + 1)

    tag = node.get("tag", "span")
    if tag not in _ALLOWED_TAGS:
        tag = "span"

    attrs = _render_attrs(node, tag)

    if tag in _VOID_TAGS:
        return f"<{tag}{attrs}>"

    inner = structured_content_to_html(node.get("content", ""), _depth + 1)
    return f"<{tag}{attrs}>{inner}</{tag}>"


def _render_attrs(node: dict[str, Any], tag: str) -> str:
    parts: list[str] = []

    # data: {key: value} → data-key="value" HTML attrs (matches Yomitan spec).
    data = node.get("data")
    if isinstance(data, dict):
        for key, value in data.items():
            if not isinstance(key, str) or not isinstance(value, str):
                continue
            safe_key = _camel_to_kebab(key)
            if not _DATA_KEY_RE.match(safe_key):
                continue
            parts.append(f'data-{safe_key}="{escape(value, quote=True)}"')

    lang = node.get("lang")
    if isinstance(lang, str):
        stripped = lang.strip()
        if stripped and _LANG_RE.match(stripped):
            parts.append(f'lang="{escape(stripped, quote=True)}"')

    style_attr = _collect_style(node)
    if style_attr:
        parts.append(style_attr)

    href = node.get("href")
    if isinstance(href, str) and tag == "a" and _is_safe_url(href):
        parts.append(f'href="{escape(href, quote=True)}"')

    path = node.get("path")
    if isinstance(path, str) and tag == "img" and _is_safe_url(path):
        parts.append(f'src="{escape(path, quote=True)}"')

    # Per-tag HTML attribute passthrough (title on most, alt/width/height on
    # img, colspan/rowspan on td/th, open on details). Keys arrive in camelCase
    # from Yomitan; aliases get normalized.
    string_attrs = _TAG_STRING_ATTRS.get(tag, frozenset())
    int_attrs = _TAG_INT_ATTRS.get(tag, frozenset())
    bool_attrs = _TAG_BOOL_ATTRS.get(tag, frozenset())

    for raw_key, value in node.items():
        if not isinstance(raw_key, str):
            continue
        attr = _ATTR_KEY_ALIASES.get(raw_key, raw_key.lower())
        if attr in string_attrs and isinstance(value, str):
            stripped = value.strip()
            if stripped and len(stripped) <= 256 and not any(ord(c) < 0x20 for c in stripped):
                parts.append(f'{attr}="{escape(stripped, quote=True)}"')
        elif attr in int_attrs:
            try:
                ival = int(value)
            except (TypeError, ValueError):
                continue
            if 1 <= ival <= _INT_ATTR_MAX:
                parts.append(f'{attr}="{ival}"')
        elif attr in bool_attrs and value:
            parts.append(attr)

    return (" " + " ".join(parts)) if parts else ""


# Inline style on tag badges so they read as separated chips even when the
# Anki card template ships no CSS for `.tag`. Padding + inline-block prevent
# the smushed-together "nouncolloquialpoliteabbr" rendering.
_TAG_BADGE_STYLE = (
    "display: inline-block; margin: 0 0.25em 0.15em 0; padding: 0 0.4em; "
    "border: 1px solid currentColor; border-radius: 0.25em; "
    "font-size: 0.85em; line-height: 1.4;"
)


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
        badges: list[str] = []
        for tag_name in term_tags:
            meta = (tag_bank or {}).get(tag_name) or {}
            if not isinstance(meta, dict):
                meta = {}
            category = meta.get("category", "")
            if isinstance(category, str) and category:
                safe_category = _WHITESPACE_RE.sub("-", category)
                css_class = f"tag tag-{safe_category}"
            else:
                css_class = "tag"
            badges.append(
                f'<span class="{escape(css_class, quote=True)}" '
                f'style="{escape(_TAG_BADGE_STYLE, quote=True)}">'
                f"{escape(tag_name)}</span>"
            )
        if badges:
            parts.append('<div class="tag-list">' + "".join(badges) + "</div>")

    for item in glossary:
        if isinstance(item, str):
            parts.append(f"<div>{escape(item)}</div>")
        else:
            parts.append(structured_content_to_html(item))

    return "".join(parts)
