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

# Converters outside the lookup ladder. tw2s folds Taiwan variants (著 -> 着) that
# plain t2s leaves alone; s2tw is the traditional spelling a card shows; s2t and
# s2hk are extra round-trip targets for script_key.
_TO_SIMPLIFIED = "tw2s"
_TO_TRADITIONAL = "s2tw"
_ROUND_TRIPS = ("s2t", "s2tw", "s2hk")
_ALL_CONFIGS = frozenset((*_CONFIGS, _TO_SIMPLIFIED, *_ROUND_TRIPS))

# One key step can land on a spelling that folds once more (麼 -> 么 -> 幺); over
# jieba's 30k most frequent words no key needed more than two steps.
_MAX_KEY_STEPS = 4


def normalize_zh(text: str) -> str:
    """NFC-normalise ``text``. Single normalisation rule for the zh engine."""
    return unicodedata.normalize("NFC", text)


@lru_cache(maxsize=len(_ALL_CONFIGS))
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


def _convert_with(name: str, normalized: str) -> str:
    """``normalized`` through converter ``name``; the input itself on any failure."""
    converter = _converter(name)
    if converter is None:
        return normalized
    return _convert(converter, normalized) or normalized


def to_traditional(text: str) -> str:
    """Taiwan-standard traditional spelling of ``text`` (s2tw), NFC-normalised.

    Single-direction on purpose: the card's traditional-variant field is a
    one-way projection of the front, not a candidate ladder. Taiwan standard
    (這裡, 麵條) rather than OpenCC's generic s2t (這裏, 麪條), because that is
    what traditional-script subtitles and learners use. Returns the normalised
    input unchanged when OpenCC is absent or the conversion fails, so the render
    hook emits an empty field rather than a wrong one.
    """
    return _convert_with(_TO_TRADITIONAL, normalize_zh(text))


def to_simplified(text: str) -> str:
    """Simplified spelling of ``text`` (tw2s), NFC-normalised.

    Many-to-one and lossy (麵 and 面 both become 面), so it feeds the tokenizer
    and pinyin, which want the most simplified text they can get, never a
    comparison key — :func:`script_key` is the key. Returns the normalised input
    when OpenCC is absent or the conversion fails.
    """
    return _convert_with(_TO_SIMPLIFIED, normalize_zh(text))


def _key_step(word: str) -> str:
    simplified = _convert_with(_TO_SIMPLIFIED, word)
    if simplified == word:
        return word
    if word in (_convert_with(name, simplified) for name in _ROUND_TRIPS):
        return simplified
    return word


def script_key(word: str) -> str:
    """Script-neutral comparison key for ``word``: the zh ``dedup_fold``.

    A word folds to its simplified spelling only when that spelling converts
    back to exactly this word under some traditional standard (s2t, s2tw,
    s2hk). 頭髮 and 头发 share a key; 麵 stays 麵, because 面 converts back to
    面 and folding would merge noodles into face. The step repeats to a fixed
    point, which makes the key idempotent — stored keys are folded again on
    every read.

    Known limits: a few rare single-character words still merge (干/幹, 后/後,
    于/於, 云/雲, 余/餘) and a few pairs never match (讚/赞). Without OpenCC the
    key is the NFC word, the same comparison as before this fold existed.
    """
    key = normalize_zh(word)
    for _ in range(_MAX_KEY_STEPS):
        folded = _key_step(key)
        if folded == key:
            break
        key = folded
    return key


def to_script(text: str, script_variant: str) -> str:
    """Card-front spelling of ``text`` for ``config.script_variant``.

    ``"simplified"`` uses :func:`script_key`, so a front always equals its own
    comparison key and an ambiguous word (麵) keeps its source spelling rather
    than becoming a different word's front. ``"traditional"`` keeps text that is
    already traditional and converts the rest to Taiwan spelling. Any other
    value leaves the text as written.
    """
    normalized = normalize_zh(text)
    if script_variant == "simplified":
        return script_key(normalized)
    if script_variant == "traditional" and to_simplified(normalized) == normalized:
        return to_traditional(normalized)
    return normalized
