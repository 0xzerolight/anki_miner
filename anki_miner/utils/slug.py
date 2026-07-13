"""Shared ASCII slug helper for on-disk resource directory names."""

from __future__ import annotations

import re

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(text: str, *, fallback: str) -> str:
    """ASCII slug suitable for a directory name.

    Lowercase ASCII passes through; non-ASCII code points become ``u<hex>`` so
    folder names survive filesystem restrictions. Runs of other characters
    collapse to ``-``. Returns *fallback* when the result would be empty.
    """
    text = text.strip().lower()
    parts: list[str] = []
    buf: list[str] = []
    for ch in text:
        if ord(ch) < 128:
            buf.append(ch)
        else:
            if buf:
                parts.append("".join(buf))
                buf.clear()
            parts.append(f"u{ord(ch):x}")
    if buf:
        parts.append("".join(buf))
    slug = _SLUG_RE.sub("-", "-".join(parts)).strip("-")
    return slug or fallback
