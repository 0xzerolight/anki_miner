"""HTML renderer for Yomitan structured-content nodes.

Walks the Yomitan term-bank glossary tree and emits sanitized HTML.
Output is stored as-is in the `content` column at import time; the
runtime path is a literal SELECT.
"""

from __future__ import annotations

import math
import re
from html import escape
from typing import Any, TypeAlias

# Yomitan structured-content nodes are heterogeneous: dict (with "tag"/"content" keys),
# list of children, or bare string leaf. Recursion is implicit via the dict["content"]
# subnode. Full TypedDict modeling is overkill given the schema's openness; this alias
# documents the shape and gives type checkers a hint at the top of each signature.
YomitanNode: TypeAlias = "dict[str, Any] | list[Any] | str"

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
# The resource-loading image functions (image-set, image, cross-fade, src,
# image-rect, element, paint) are listed explicitly because they can reference
# remote URLs or external paint sources in unquoted form and therefore bypass
# the url() guard — e.g. `background: image-set(https://… 1x)`. The vendor and
# Houdini variants (-moz-image-rect, -moz-element, paint()) are blocked too as
# defense-in-depth even though Qt's Chromium can't fetch from all of them.
# calc/rgb/rgba/hsl/hsla/var are intentionally NOT listed: they carry no URLs.
_STYLE_VALUE_BAD_RE = re.compile(
    r"""(?ix)
    (?:url\s*\() |
    (?:[a-z-]*image-set\s*\() |
    (?:[a-z-]*image-rect\s*\() |
    (?:[a-z-]*cross-fade\s*\() |
    (?:(?<![a-z])image\s*\() |
    (?:[a-z-]*element\s*\() |
    (?:paint\s*\() |
    (?:src\s*\() |
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
# `img` width/height are NOT here: Yomitan sizes images in `sizeUnits`
# (px or em), and a bare presentational `width="1"` attr loses the unit and is
# overridden anyway by the card stylesheet's `.gloss-image { height: auto }`
# (Issue #68). `_render_img` instead emits the size as inline CSS via
# `_img_size_decls`, which carries the unit and beats the stylesheet.
_TAG_INT_ATTRS: dict[str, frozenset[str]] = {
    "td": frozenset({"colspan", "rowspan"}),
    "th": frozenset({"colspan", "rowspan"}),
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


# Marker class for `<img>` tags whose `src` refers to a dictionary-bundled asset
# extracted at import time. AnkiService scans for this class to upload the file
# via AnkiConnect storeMediaFile so the image resolves in the Anki webview.
DICT_MEDIA_CLASS = "anki-miner-dict-media"

# CSS class on every `<img>` we emit in the envelope. Card templates style this
# to size/cap dictionary art; the marker class above lives alongside it for
# dict-bundled assets.
_GLOSS_IMAGE_CLASS = "gloss-image"


def _resolve_img_src(
    raw_path: YomitanNode | None,
    *,
    dict_id: str | None,
    media_collector: set[str] | None,
) -> tuple[str | None, bool]:
    """Decide what `src` an `<img>` node should emit.

    Returns (src, is_dict_media). is_dict_media=True means the caller should
    also emit the ``class="anki-miner-dict-media"`` marker so AnkiService
    knows to upload the corresponding file via AnkiConnect.

    Three cases:
      1. Relative path + dict_id provided → rewrite to namespaced flat filename
         and record the original path in `media_collector` for asset extraction.
      2. http/https URL → pass through unchanged (Anki webview can load it).
      3. Anything else → drop (relative paths without dict_id resolve to
         nothing inside Anki and render as broken-icon glyphs).
    """
    if not isinstance(raw_path, str):
        return None, False
    candidate = raw_path.strip()
    if not candidate:
        return None, False

    if dict_id and dict_media_safe_basename(candidate) is not None:
        if media_collector is not None:
            media_collector.add(candidate)
        return dict_media_filename(dict_id, candidate), True

    if candidate.startswith(("http://", "https://")) and _is_safe_url(candidate):
        return candidate, False

    return None, False


def dict_media_filename(dict_id: str, rel_path: str) -> str:
    """Build the flat Anki-media filename for a dict-internal asset.

    Anki's media collection is flat (no subfolders), so a Yomitan zip path like
    `sankoku8/svg-accent/X.svg` must become a single filename. We namespace by
    `dict_id` (already an ASCII slug from the importer) and flatten the inner
    path by replacing separators with `_`. CJK chars in the basename survive.
    """
    safe = dict_media_safe_basename(rel_path)
    return f"{dict_id}__{safe}"


def dict_media_safe_basename(rel_path: str) -> str | None:
    """Convert a dict-internal relative path to a flat safe filename.

    Returns None for absolute paths, scheme-prefixed values, or anything with
    parent traversal — those are not legitimate Yomitan media references.
    """
    if not isinstance(rel_path, str):
        return None
    p = rel_path.strip()
    if not p:
        return None
    if p.startswith(("/", "\\")) or p.startswith("//"):
        return None
    colon = p.find(":")
    slash = p.find("/")
    if colon != -1 and (slash == -1 or colon < slash):
        return None
    parts = p.replace("\\", "/").split("/")
    if any(part in ("", ".", "..") for part in parts):
        return None
    return "_".join(parts)


def _camel_to_kebab(name: str) -> str:
    return _CAMEL_RE.sub(r"\1-\2", name).lower()


def _coerce_style_value(value: YomitanNode) -> str | None:
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


def _collect_style(node: dict[str, YomitanNode], *, seed: dict[str, str] | None = None) -> str:
    """Build an inline style="..." value from a node's style props.

    Reads both nested `style: {...}` and Yomitan's top-level shortcut keys
    (fontSize, verticalAlign, etc.). Only whitelisted CSS properties survive,
    and values are scrubbed for url()/expression()/quotes/braces.

    `seed` pre-populates declarations the caller built itself (e.g. `<img>`
    width/height with units from `_img_size_decls`). Seeded props are not in
    `_ALLOWED_STYLE_PROPS`, so the style-block / shortcut loops can't clobber
    them — they pass through verbatim.
    """
    # Dict preserves insertion order; nested `style:` block wins because it's
    # spec-canonical, top-level shortcuts overwrite only when nested didn't set.
    seen: dict[str, str] = dict(seed) if seed else {}

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


def structured_content_to_html(
    node: YomitanNode,
    _depth: int = 0,
    *,
    dict_id: str | None = None,
    media_collector: set[str] | None = None,
) -> str:
    """Render a Yomitan structured-content node to HTML.

    Args:
        node: A string, list, or dict per Yomitan's term-bank schema.
        dict_id: When set, `<img>` nodes whose `path` is a dict-internal
            relative reference get rewritten to a namespaced flat filename
            suitable for Anki's media collection and tagged with
            ``class="anki-miner-dict-media"``. Without it, relative-path imgs
            are dropped entirely (their src would be unresolvable in Anki).
        media_collector: When set, every dict-internal asset path encountered
            is added to this set so the importer can copy the bytes out of
            the Yomitan zip.

    Returns:
        HTML string. Unknown tags become <span>; plain strings are escaped.
        Nodes deeper than _MAX_DEPTH are truncated to "" to bound stack use.
    """
    if _depth > _MAX_DEPTH:
        return ""

    if isinstance(node, str):
        return escape(node)

    if isinstance(node, list):
        return "".join(
            structured_content_to_html(child, _depth + 1, dict_id=dict_id, media_collector=media_collector)
            for child in node
        )

    if not isinstance(node, dict):
        return ""

    # Yomitan wraps top-level entries in {"type": "structured-content", "content": ...}
    if node.get("type") == "structured-content":
        return structured_content_to_html(
            node.get("content", ""),
            _depth + 1,
            dict_id=dict_id,
            media_collector=media_collector,
        )

    tag = node.get("tag", "span")
    if tag not in _ALLOWED_TAGS:
        tag = "span"

    # `<img>` takes the envelope path: the resolved tag is wrapped in
    # <a class="gloss-image-link"><span class="gloss-image-container">…</span></a>
    # so downstream CSS can layer captions/links over the bitmap. The bare
    # `<img>` (no src) shortcut still emits an empty tag for safety.
    if tag == "img":
        return _render_img(node, dict_id=dict_id, media_collector=media_collector)

    attrs = _render_attrs(node, tag)

    if tag in _VOID_TAGS:
        return f"<{tag}{attrs}>"

    inner = structured_content_to_html(
        node.get("content", ""),
        _depth + 1,
        dict_id=dict_id,
        media_collector=media_collector,
    )
    return f"<{tag}{attrs}>{inner}</{tag}>"


# Largest em dimension we'll honor on an image. Inline pitch/accent SVGs are
# ~1em; anything past this is almost certainly bad data, and capping bounds the
# blast radius of a hostile dict. px reuses _INT_ATTR_MAX.
_IMG_EM_MAX = 100.0


def _img_size_decls(node: dict[str, YomitanNode]) -> dict[str, str]:
    """Build unit-carrying CSS width/height decls from a Yomitan `<img>` node.

    Yomitan expresses image size as numeric `width`/`height` interpreted in
    `sizeUnits` (`"em"` → em, anything else / absent → px, matching Yomitan's
    default). Emitting these as bare presentational `width="1"` attrs drops the
    unit and gets overridden by the stylesheet (Issue #68); emitting them as
    inline CSS keeps the unit and wins over the stylesheet.

    Accepts int or float; rejects bool, non-numeric, non-finite, and <= 0;
    clamps to a sane bound. Returns an ordered {prop: "<value><unit>"} dict.
    """
    unit = "em" if node.get("sizeUnits") == "em" else "px"
    cap = _IMG_EM_MAX if unit == "em" else float(_INT_ATTR_MAX)
    decls: dict[str, str] = {}
    for key in ("width", "height"):
        raw = node.get(key)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            continue
        val = float(raw)
        if not math.isfinite(val) or val <= 0:
            continue
        if val > cap:
            val = cap
        decls[key] = f"{val:g}{unit}"
    return decls


def _render_img(
    node: dict[str, YomitanNode],
    *,
    dict_id: str | None,
    media_collector: set[str] | None,
) -> str:
    """Render an `<img>` SC node, wrapped in the gloss-image envelope when
    a src can be resolved.

    Without a resolvable src (e.g. relative path with no dict_id, traversal
    attempt, blocked scheme), we still emit a bare `<img>` so the upstream
    contract — "img nodes always produce something" — is preserved. The
    envelope only appears when there's actually an image to show.
    """
    img_src, dict_media = _resolve_img_src(node.get("path"), dict_id=dict_id, media_collector=media_collector)

    # Per-tag string passthroughs (alt/title) that belong on the inner <img>.
    extras: list[str] = []
    string_attrs = _TAG_STRING_ATTRS.get("img", frozenset())
    for raw_key, value in node.items():
        if not isinstance(raw_key, str):
            continue
        attr = _ATTR_KEY_ALIASES.get(raw_key, raw_key.lower())
        if attr in string_attrs and isinstance(value, str):
            stripped = value.strip()
            if stripped and len(stripped) <= 256 and not any(ord(c) < 0x20 for c in stripped):
                extras.append(f'{attr}="{escape(stripped, quote=True)}"')

    # Size (width/height + sizeUnits) and any other inline style (verticalAlign,
    # border, …) are merged into one style="…" attr. Size decls seed first so
    # they survive the style whitelist, which deliberately omits width/height.
    style_attr = _collect_style(node, seed=_img_size_decls(node))
    if style_attr:
        extras.append(style_attr)

    if img_src is None:
        # No envelope — nothing to point at. Emit a bare <img> with extras
        # only (no src, no class).
        tail = (" " + " ".join(extras)) if extras else ""
        return f"<img{tail}>"

    img_class = f"{_GLOSS_IMAGE_CLASS} {DICT_MEDIA_CLASS}" if dict_media else _GLOSS_IMAGE_CLASS
    extras_str = (" " + " ".join(extras)) if extras else ""
    raw_path = node.get("path")
    data_path = escape(raw_path, quote=True) if isinstance(raw_path, str) else ""
    return (
        f'<a class="gloss-image-link" data-path="{data_path}">'
        f'<span class="gloss-image-container">'
        f'<img class="{img_class}" src="{escape(img_src, quote=True)}"{extras_str}>'
        f"</span></a>"
    )


def _render_attrs(node: dict[str, YomitanNode], tag: str) -> str:
    parts: list[str] = []

    # Every element carries a `gloss-sc-<tag>` hook so card templates can
    # target Yomitan-sourced markup without a runtime walk. For unknown tags
    # the node has already been folded to <span>, so the class lands as
    # `gloss-sc-span` (matching the fallback).
    parts.append(f'class="gloss-sc-{tag}"')

    # data: {key: value} → data-sc-key="value" HTML attrs. Yomitan's own DOM
    # renders structured-content `data` entries under the `data-sc-` prefix
    # (dataset key `sc<Key>`), and published dictionary CSS (e.g. the Jitendex
    # "custom styles" snippets, `[data-sc-content|="example-sentence"]`) targets
    # that prefix. Emitting it verbatim lets those snippets work unmodified.
    data = node.get("data")
    if isinstance(data, dict):
        for key, value in data.items():
            if not isinstance(key, str) or not isinstance(value, str):
                continue
            safe_key = _camel_to_kebab(key)
            if not _DATA_KEY_RE.match(safe_key):
                continue
            parts.append(f'data-sc-{safe_key}="{escape(value, quote=True)}"')

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

    # Per-tag HTML attribute passthrough (title on most, colspan/rowspan on
    # td/th, open on details). `<img>` takes a separate path entirely — see
    # `_render_img` — so img attrs aren't handled here. Keys arrive in
    # camelCase from Yomitan; aliases get normalized.
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
                ival = int(value)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                continue
            if 1 <= ival <= _INT_ATTR_MAX:
                parts.append(f'{attr}="{ival}"')
        elif attr in bool_attrs and value:
            parts.append(attr)

    return " " + " ".join(parts)


def render_glossary_entry(
    glossary: list[YomitanNode],
    *,
    dict_id: str | None = None,
    media_collector: set[str] | None = None,
) -> str:
    """Render a Yomitan term-bank glossary array to `<li class="gloss-item">` items.

    Pure SC-tree → HTML translator. Each glossary item becomes one
    `<li class="gloss-item"><div class="gloss-content">…</div></li>`. The wrapper
    is a block-level `<div>` (not a `<span>`): structured-content items routinely
    contain block nodes (`div`/`table`/`ul`) and an inline `<span>` parent makes
    the HTML parser auto-close the span before the block, orphaning the content
    from `.gloss-content` styling. A short string sense still renders on one line.
    Plain strings are HTML-escaped and dropped into the inner div as text;
    structured-content nodes go through `structured_content_to_html`. The
    caller is responsible for wrapping the result in `<ul>`/`<ol>` or any
    dictionary-level chrome — this function emits items only.

    Args:
        glossary: The 6th element of a term-bank tuple. Each item is a plain
                  string or a structured-content node.
        dict_id: Forwarded to the SC renderer; rewrites dict-internal `<img>`
                 paths to Anki-media filenames.
        media_collector: Forwarded to the SC renderer; collects relative
                         asset paths so the importer can extract them.

    Returns:
        Concatenated `<li>` HTML. Empty input → empty string.
    """
    parts: list[str] = []
    for item in glossary:
        if isinstance(item, str):
            normalized = item.replace("\r\n", "\n").replace("\r", "\n")
            inner = escape(normalized).replace("\n", "<br>")
        else:
            inner = structured_content_to_html(item, dict_id=dict_id, media_collector=media_collector)
        parts.append(f'<li class="gloss-item"><div class="gloss-content">{inner}</div></li>')
    return "".join(parts)
