"""Bounded readers for optional local startup artifacts."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

_T = TypeVar("_T")


def file_within_limit(path: Path, max_bytes: int, label: str) -> bool:
    """Return whether ``path`` is readable and no larger than ``max_bytes``."""
    try:
        size = path.stat().st_size
    except OSError as exc:
        logger.warning("Could not read %s %s: %s", label, path, exc)
        return False
    if size > max_bytes:
        logger.warning("Skipping oversized %s %s (%d > %d bytes)", label, path, size, max_bytes)
        return False
    return True


def read_text_bounded(path: Path, max_bytes: int, default: _T, label: str) -> str | _T:
    """Read UTF-8 text without ever reading more than ``max_bytes``."""
    if not file_within_limit(path, max_bytes, label):
        return default
    try:
        with path.open("rb") as stream:
            raw = stream.read(max_bytes + 1)
        if len(raw) > max_bytes:
            logger.warning("Skipping oversized %s %s (> %d bytes)", label, path, max_bytes)
            return default
        return raw.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("Could not decode %s %s: %s", label, path, exc)
        return default


def read_json_bounded(path: Path, max_bytes: int, default: _T, label: str) -> Any | _T:
    """Decode bounded UTF-8 JSON, returning ``default`` on any read/decode error."""
    text = read_text_bounded(path, max_bytes, None, label)
    if text is None:
        return default
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning("Could not decode %s %s: %s", label, path, exc)
        return default
