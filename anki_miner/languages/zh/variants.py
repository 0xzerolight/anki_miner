"""Normalisation and simplified/traditional variant generation for zh (spec 10.1).

OpenCC performs no Unicode normalisation of its own, so every string crossing
into it goes through :func:`normalize_zh` first — the one shared rule the spec
pins, reused by the profile's ``normalize`` field and by the dictionary key
folding, so import-time and query-time keys can never disagree.

OpenCC is optional. Without it there are no variants and lookups behave exactly
as they would for a single-script corpus, so it stays out of the availability
gate (``availability.zh_missing_required_reason``) and is named only by
``availability.zh_unavailable_reason``, which lists the whole stack.
"""

from __future__ import annotations

import logging
import unicodedata
from functools import lru_cache
from typing import Any

logger = logging.getLogger(__name__)

# Both directions: a simplified corpus queried with a traditional front needs
# t2s, and vice versa. Ordering is stable so candidate lists are deterministic.
_CONFIGS = ("s2t", "t2s")


def normalize_zh(text: str) -> str:
    """NFC-normalise ``text``. Single normalisation rule for the zh engine."""
    return unicodedata.normalize("NFC", text)


@lru_cache(maxsize=len(_CONFIGS))
def _converter(name: str) -> Any | None:
    """One OpenCC converter for ``name``, or ``None`` when unavailable."""
    try:
        import opencc
    except ImportError:
        logger.debug("OpenCC not importable; zh script variants disabled")
        return None
    try:
        return opencc.OpenCC(name)
    except Exception:  # noqa: BLE001 — a broken config must not break mining
        logger.warning("OpenCC configuration %s failed to load; skipping it", name)
        return None


@lru_cache(maxsize=1)
def _converters() -> tuple[Any, ...]:
    """Every usable converter, in ``_CONFIGS`` order; empty when unavailable."""
    return tuple(c for c in (_converter(name) for name in _CONFIGS) if c is not None)


def _convert(converter: Any, normalized: str) -> str | None:
    try:
        return normalize_zh(converter.convert(normalized))
    except Exception:  # noqa: BLE001 — conversion failure = no extra candidate
        logger.debug("OpenCC conversion failed for %r", normalized)
        return None


def variant_candidates(word: str) -> list[str]:
    """Ordered script variants of ``word``, NFC-normalised, ``word`` first.

    First occurrence wins, so a word that is identical in both scripts yields a
    single entry.
    """
    normalized = normalize_zh(word)
    candidates = [normalized]
    for converter in _converters():
        converted = _convert(converter, normalized)
        if converted and converted not in candidates:
            candidates.append(converted)
    return candidates


def to_traditional(text: str) -> str:
    """Traditional spelling of ``text`` (s2t only), NFC-normalised.

    Single-direction on purpose: the card's traditional-variant field is a
    one-way projection of the simplified front, not a candidate ladder. Returns
    the normalised input unchanged when OpenCC is absent or the conversion
    fails, so the render hook emits an empty field rather than a wrong one.
    """
    normalized = normalize_zh(text)
    converter = _converter("s2t")
    if converter is None:
        return normalized
    return _convert(converter, normalized) or normalized
