"""Scope a dictionary's ``styles.css`` to its own glossary markup (Issue #87).

Yomitan dictionaries (Jitendex, NHK, 三省堂, pitch dicts, …) ship a ``styles.css``
at the root of their zip that styles the structured-content DOM they emit — tag
pills, example-sentence boxes, forms tables, and so on. anki_miner historically
ignored that file, so its cards rendered dictionary content unstyled (Issue #87,
"Bug 1").

This module replicates Yomitan's own Anki behavior: prefix every top-level
selector with ``.yomitan-glossary [data-dictionary="<title>"]`` and emit the
result inline as a ``<style>`` block on each card. Two things fall out of that
scope: the rules only ever match the *miner's* glossary markup for *this*
dictionary, so one dict's CSS never bleeds into another's entries.

Because the output is injected verbatim into an HTML ``<style>`` element inside a
card field, the CSS is third-party and untrusted. We sanitize defensively:

* Rules referencing remote/dynamic resources (``url()``, ``image-set()``,
  ``expression()``, …) are dropped wholesale.
* Any rule whose text contains ``<`` is dropped — a ``</style>`` sequence would
  otherwise terminate the ``<style>`` element early and allow HTML injection.
* At-rules other than the conditional group rules (``@media`` / ``@supports`` /
  ``@container`` / ``@layer``) are removed — notably ``@import`` and
  ``@font-face`` (remote fetch vectors).

The parser is intentionally hand-rolled (no CSS library dependency, matching
:mod:`anki_miner.services.dictionary.yomitan_renderer`). It does not resolve CSS
nesting: a rule body is kept verbatim, so a dictionary's own ``&`` nesting still
resolves against the scoped parent selector — exactly as it does under Yomitan.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# Skip pathological stylesheets outright rather than spend time scoping them. A
# real dictionary styles.css is a few KB; Jitendex's is ~6 KB. 512 KB is far
# beyond any legitimate file and bounds the work a hostile zip can impose.
_MAX_STYLES_BYTES = 512 * 1024

# At-rules that wrap nested style rules. We recurse into their block and keep the
# wrapper; everything else (@import, @font-face, @charset, @keyframes, @page, …)
# is dropped — none carry scoped selectors, and @import/@font-face can fetch
# remote resources.
_CONDITIONAL_GROUP_AT_RULES = frozenset({"@media", "@supports", "@container", "@layer", "@scope"})

# Forbidden substrings/patterns in any rule's prelude or body. Mirrors the
# inline-style scrubbing in yomitan_renderer (url()/image funcs/expression/
# scheme handlers) and adds two emission-context guards:
#   * ``@import`` — remote stylesheet fetch.
#   * ``<``       — a ``</style>`` (or ``</STYLE >``) sequence would close the
#                   surrounding <style> element and let arbitrary HTML through;
#                   real CSS never needs a literal ``<``.
# A match drops the whole rule (coarse but safe: a single tainted declaration
# forfeits its rule block, including any nested rules — legitimate dictionaries
# never trip this).
#
# ReDoS note: the image-function branches match an optional single vendor prefix
# (``-webkit-``/``-moz-``) anchored by a token-boundary lookbehind, NOT an
# unbounded ``[a-z-]*`` greedy prefix. A greedy prefix makes ``.search`` O(n²)
# on a long single-token prelude/body (it re-consumes the run from every start
# position) — a hostile under-cap styles.css would hang dictionary load. The
# anchored form fails in O(1) per position, so the whole scan stays O(n).
_FORBIDDEN_RE = re.compile(
    r"""(?ix)
    url\s*\( |
    (?<![a-z-])(?:-[a-z]+-)?(?:image-set|image-rect|cross-fade|element|image)\s*\( |
    paint\s*\( |
    src\s*\( |
    expression\s*\( |
    javascript: |
    vbscript: |
    @import |
    @charset |
    <
    """,
)


def _css_string_escape(title: str) -> str:
    """Escape a dictionary title for use inside a CSS attribute-selector string.

    Backslash and double-quote are escaped per CSS string rules (matching
    Yomitan's ``addDictionaryScopeToCss``). ``<``/``>`` and control characters
    are stripped so a hostile title can neither break out of the ``<style>``
    element nor smuggle in markup; a real title (e.g. ``Jitendex.org
    [2026-06-06]``) is unaffected.
    """
    escaped = title.replace("\\", "\\\\").replace('"', '\\"')
    return "".join(ch for ch in escaped if ch not in "<>" and ord(ch) >= 0x20)


def _iter_rules(css: str):
    """Yield ``(prelude, body)`` for each top-level rule in ``css``.

    ``body`` is ``None`` for a statement at-rule (``@import …;``) — no block.
    Comments and whitespace between rules are skipped. Strings, comments, and
    brace nesting inside a rule are respected so a ``{``/``}``/``;`` embedded in
    any of them does not split a rule prematurely.
    """
    i, n = 0, len(css)
    prelude_start = 0
    while i < n:
        ch = css[i]
        # Comment: skip to the closing */ (only outside the prelude-accumulation
        # sense — a comment inside a prelude is preserved by virtue of i moving).
        if ch == "/" and i + 1 < n and css[i + 1] == "*":
            end = css.find("*/", i + 2)
            i = n if end == -1 else end + 2
            continue
        if ch in "\"'":
            i = _skip_string(css, i)
            continue
        if ch == "{":
            prelude = css[prelude_start:i]
            body, i = _read_block(css, i)
            yield prelude, body
            prelude_start = i
            continue
        if ch == ";":
            # Statement (e.g. @import …;) — no block. Surface prelude+';' so the
            # scoper can decide to drop it.
            stmt = css[prelude_start : i + 1]
            i += 1
            if stmt.strip():
                yield stmt, None
            prelude_start = i
            continue
        i += 1
    # Trailing junk without a closing brace is ignored.


def _skip_string(css: str, i: int) -> int:
    """Return the index just past the string literal starting at ``css[i]``."""
    quote = css[i]
    i += 1
    n = len(css)
    while i < n:
        c = css[i]
        if c == "\\":
            i += 2
            continue
        if c == quote:
            return i + 1
        i += 1
    return n


def _read_block(css: str, open_idx: int) -> tuple[str, int]:
    """Read a ``{ … }`` block opening at ``open_idx``.

    Returns ``(inner, next_index)`` where ``inner`` is the text between the
    braces and ``next_index`` points just past the closing ``}``. Brace
    nesting, strings, and comments inside are respected.
    """
    i = open_idx + 1
    n = len(css)
    depth = 1
    while i < n:
        c = css[i]
        if c == "/" and i + 1 < n and css[i + 1] == "*":
            end = css.find("*/", i + 2)
            i = n if end == -1 else end + 2
            continue
        if c in "\"'":
            i = _skip_string(css, i)
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return css[open_idx + 1 : i], i + 1
        i += 1
    # Unbalanced — treat the remainder as the block body.
    return css[open_idx + 1 : n], n


def _strip_css_comments(s: str) -> str:
    """Remove ``/* … */`` comments, leaving string literals intact."""
    out: list[str] = []
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c == "/" and i + 1 < n and s[i + 1] == "*":
            end = s.find("*/", i + 2)
            i = n if end == -1 else end + 2
            continue
        if c in "\"'":
            j = _skip_string(s, i)
            out.append(s[i:j])
            i = j
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _split_top_level_commas(prelude: str) -> list[str]:
    """Split a selector list on commas that sit outside (), [], and strings."""
    parts: list[str] = []
    depth = 0
    start = 0
    i, n = 0, len(prelude)
    while i < n:
        c = prelude[i]
        if c in "\"'":
            i = _skip_string(prelude, i)
            continue
        if c in "([":
            depth += 1
        elif c in ")]":
            depth = max(0, depth - 1)
        elif c == "," and depth == 0:
            parts.append(prelude[start:i])
            start = i + 1
        i += 1
    parts.append(prelude[start:])
    return parts


def scope_dict_css(styles_css: str, dict_title: str) -> str:
    """Return ``styles_css`` scoped to one dictionary's glossary markup.

    Every top-level selector is prefixed with
    ``.yomitan-glossary [data-dictionary="<dict_title>"]``; conditional group
    at-rules are recursed into and preserved; unsafe rules and other at-rules
    are dropped (see module docstring).

    Returns ``""`` for empty input, oversized input, or input that scopes to
    nothing — callers treat that as "this dictionary contributes no styling".
    """
    if not styles_css or not styles_css.strip():
        return ""
    if len(styles_css) > _MAX_STYLES_BYTES:
        logger.debug("styles.css for %r exceeds %d bytes; skipping", dict_title, _MAX_STYLES_BYTES)
        return ""
    scope = f'.yomitan-glossary [data-dictionary="{_css_string_escape(dict_title)}"]'
    return _scope_block(styles_css, scope)


def _scope_block(css: str, scope: str) -> str:
    """Scope every rule in one block of CSS. Used at top level and recursively
    for the body of conditional group at-rules."""
    out: list[str] = []
    for prelude, body in _iter_rules(css):
        stripped = _strip_css_comments(prelude).strip()
        if not stripped:
            continue
        if stripped.startswith("@"):
            rule = _scope_at_rule(stripped, body, scope)
            if rule:
                out.append(rule)
            continue
        if body is None:
            continue  # bare statement that is not an at-rule — drop
        if _FORBIDDEN_RE.search(prelude) or _FORBIDDEN_RE.search(body):
            logger.debug("Dropping dictionary CSS rule with forbidden construct: %s", stripped[:80])
            continue
        selectors = [s.strip() for s in _split_top_level_commas(stripped)]
        scoped = ", ".join(f"{scope} {s}" for s in selectors if s)
        if scoped:
            out.append(f"{scoped} {{{body.strip()}}}")
    return "\n".join(out)


def _scope_at_rule(prelude: str, body: str | None, scope: str) -> str:
    """Scope a single at-rule. Recurse into conditional group rules, drop the
    rest (including statement at-rules like ``@import``)."""
    name = re.match(r"@[\w-]+", prelude)
    if name is None or name.group(0).lower() not in _CONDITIONAL_GROUP_AT_RULES:
        return ""
    if body is None:
        return ""  # a group at-rule with no block is malformed — drop
    if _FORBIDDEN_RE.search(prelude):
        return ""
    inner = _scope_block(body, scope)
    if not inner:
        return ""
    return f"{prelude.strip()} {{\n{inner}\n}}"
