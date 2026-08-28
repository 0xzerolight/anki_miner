"""File system utilities."""

import hashlib
import os
from pathlib import Path

#: Component-length fallback when the filesystem will not report ``PC_NAME_MAX``.
_DEFAULT_COMPONENT_BYTES = 255

#: Digest characters kept when a truncated stem needs a collision marker.
_TRUNCATED_HASH_CHARS = 12


def _truncate_utf8(value: str, byte_budget: int) -> str:
    """Return the longest codepoint prefix fitting ``byte_budget`` UTF-8 bytes."""
    used = 0
    for index, char in enumerate(value):
        char_bytes = len(char.encode("utf-8"))
        if used + char_bytes > byte_budget:
            return value[:index]
        used += char_bytes
    return value


def component_byte_limit(directory: Path) -> int:
    """Return the maximum filename length in bytes for *directory*."""
    try:
        limit = os.pathconf(directory, "PC_NAME_MAX")
    except (AttributeError, OSError, ValueError):
        return _DEFAULT_COMPONENT_BYTES
    return limit if limit > 0 else _DEFAULT_COMPONENT_BYTES


def bounded_output_name(stem: str, fixed: str, directory: Path) -> str:
    """Return ``stem + fixed`` shortened to fit *directory*'s component limit.

    *fixed* is the trailing part the caller must keep intact — a tool's suffix
    plus its extension (``"_condensed.mp3"``, ``"_retimed.srt"``). Long source
    names would otherwise push the derived name past ``NAME_MAX`` and the write
    would fail; the truncated stem carries a hash of the full one so two long
    names that share a prefix cannot collapse onto one output.

    Raises:
        ValueError: *fixed* alone does not fit the component limit.
    """
    byte_limit = component_byte_limit(directory)
    candidate = stem + fixed
    if len(candidate.encode("utf-8")) <= byte_limit:
        return candidate

    digest = hashlib.sha256(stem.encode("utf-8")).hexdigest()[:_TRUNCATED_HASH_CHARS]
    marker = f"-{digest}"
    stem_budget = byte_limit - len((marker + fixed).encode("utf-8"))
    if stem_budget < 0:
        raise ValueError("Output name suffix exceeds the filesystem component limit")
    return _truncate_utf8(stem, stem_budget) + marker + fixed


def ensure_directory(path: Path) -> Path:
    """Ensure a directory exists, creating it if necessary.

    Args:
        path: Directory path to ensure exists

    Returns:
        The directory path
    """
    path.mkdir(parents=True, exist_ok=True)
    return path


def safe_filename(filename: str) -> str:
    """Make a filename safe for the file system.

    Args:
        filename: Original filename

    Returns:
        Safe filename with invalid characters removed
    """
    import re

    # Remove or replace invalid filename characters. `[` and `]` are included
    # because they terminate Anki's `[sound:...]` media tag — a bracket in a
    # media filename truncates the reference and corrupts the card (7.5; Yomitan
    # strips `]` from audio filenames in backend.js).
    invalid_chars = '<>:"/\\|?*[]'
    safe_name = filename
    for char in invalid_chars:
        safe_name = safe_name.replace(char, "_")

    # Remove control characters
    safe_name = re.sub(r"[\x00-\x1f\x7f]", "", safe_name)

    # Handle Windows reserved names
    reserved = {"CON", "PRN", "AUX", "NUL"} | {f"{name}{i}" for name in ("COM", "LPT") for i in range(1, 10)}
    stem = Path(safe_name).stem.upper()
    if stem in reserved:
        safe_name = f"_{safe_name}"

    # Truncate to 255 bytes (filesystem limit)
    if len(safe_name.encode("utf-8")) > 255:
        ext = Path(safe_name).suffix
        name = Path(safe_name).stem
        name_budget = 255 - len(ext.encode("utf-8"))
        if name_budget < 0:
            name = ""
            ext = _truncate_utf8(ext, 255)
        else:
            name = _truncate_utf8(name, name_budget)
        safe_name = name + ext

    # Fallback for empty result
    if not safe_name or not safe_name.strip():
        safe_name = "unnamed"

    return safe_name
