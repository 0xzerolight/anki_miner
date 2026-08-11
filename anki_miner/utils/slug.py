"""Shared ASCII slug helper for on-disk resource directory names."""

from __future__ import annotations

import hashlib
import re

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_WINDOWS_DEVICE_BASENAMES = frozenset(
    {"con", "prn", "aux", "nul", "com¹", "com²", "com³", "lpt¹", "lpt²", "lpt³"}
    | {f"com{index}" for index in range(1, 10)}
    | {f"lpt{index}" for index in range(1, 10)}
)


def is_windows_device_basename(value: str) -> bool:
    """Return whether Windows reserves *value*, with or without an extension."""
    basename = value.partition(".")[0].rstrip(" ").casefold()
    return basename in _WINDOWS_DEVICE_BASENAMES


def slugify(text: str, *, fallback: str, max_bytes: int | None = None) -> str:
    """ASCII slug suitable for a directory name.

    Lowercase ASCII passes through; non-ASCII code points become ``u<hex>`` so
    folder names survive filesystem restrictions. Runs of other characters
    collapse to ``-``. Returns *fallback* when the result would be empty. When
    ``max_bytes`` is set, truncated slugs keep a short hash of the full value.
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
    slug = _SLUG_RE.sub("-", "-".join(parts)).strip("-") or fallback
    if is_windows_device_basename(slug):
        slug = f"x-{slug}"
    if max_bytes is None or len(slug.encode("utf-8")) <= max_bytes:
        return slug
    if max_bytes < 1:
        raise ValueError("max_bytes must be positive")

    digest = hashlib.sha256(slug.encode("utf-8")).hexdigest()[:8]
    if max_bytes <= len(digest):
        return digest[:max_bytes]
    prefix = slug[: max_bytes - len(digest) - 1].rstrip("-")
    return f"{prefix}-{digest}" if prefix else digest
