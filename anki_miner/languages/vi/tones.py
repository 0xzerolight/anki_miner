"""Vietnamese tone-mark placement: the fold every vi key and the P3 normaliser run.

Ported from viet_text_tools 0.1.6 ``normalize_diacritics`` (MIT, Copyright (c) 2020
enricobarzetti; notice and provenance in ``licenses/viet_text_tools/``). The rules and
their order are upstream's; the patterns are compiled once, and ``decomposed`` is dropped.

Old style (``hòa``, ``khỏe``, ``thủy``) is canonical (spec C.4): subtitles are 96 % old
style. New style (``hoà``) is what underthesea's segmentation dictionary spells, so the
tokenizer tags a new-style copy (``to_new_style``). Both directions only move a tone mark
between vowels of one syllable, so on NFC Vietnamese text the length never changes.
Diacritics are never stripped: ``ma mà má mả mã mạ`` are six words.

These functions are the raw port and know nothing about the Vietnamese alphabet: the tone
class also holds the tilde of ``ñ`` and the acute of ``é``, so running them over a whole
line rewrites foreign names. Every caller outside this module therefore goes through
``script.fold_tone_placement``, which scopes the fold to one Vietnamese word at a time.
"""

from __future__ import annotations

import re
import unicodedata

#: The five combining tone marks (grave, hook above, tilde, acute, dot below). Escapes, never literals.
_TONE = "([\u0300\u0309\u0303\u0301\u0323])"
#: Breve, circumflex and horn: the vowel carrying one of these takes the tone.
_DIACRITICS = "\u0306\u0302\u031b"

_TONE_AFTER_VOWELS = re.compile(rf"(?i){_TONE}([aeiouy{_DIACRITICS}]+)")
_TONE_ON_DIACRITIC_VOWEL = re.compile(rf"(?i)(?<=[{_DIACRITICS}])(.){_TONE}")
_TONE_BEFORE_AE_GLIDE = re.compile(rf"(?i)(?<=[ae])([iouy]){_TONE}")
_TONE_BEFORE_OY_GLIDE = re.compile(rf"(?i)(?<=[oy])([iuy]){_TONE}")
_TONE_ON_U_ONSET = re.compile(rf"(?i)(?<!q)(u)([aeiou]){_TONE}")
_TONE_ON_I_ONSET = re.compile(rf"(?i)(?<!g)(i)([aeiouy]){_TONE}")
_OLD_STYLE_OA_OE_UY = re.compile(rf"(?i)(?<!q)([ou])([aeoy]){_TONE}(?!\w)")


def normalize_diacritics(source: str, new_style: bool = False) -> str:
    """Place every tone mark by the orthographic rules; NFC out.

    Repairs misplaced marks (``nghìên`` -> ``nghiền``, ``cuả`` -> ``của``) either way;
    ``new_style`` only decides a syllable-final ``oa``/``oe``/``uy`` not after ``q``
    (``hoà`` new, ``hòa`` old).
    """
    result = unicodedata.normalize("NFD", source)
    result = _TONE_AFTER_VOWELS.sub(r"\2\1", result)
    result = _TONE_ON_DIACRITIC_VOWEL.sub(r"\2\1", result)
    result = _TONE_BEFORE_AE_GLIDE.sub(r"\2\1", result)
    result = _TONE_BEFORE_OY_GLIDE.sub(r"\2\1", result)
    result = _TONE_ON_U_ONSET.sub(r"\1\3\2", result)
    result = _TONE_ON_I_ONSET.sub(r"\1\3\2", result)
    if not new_style:
        result = _OLD_STYLE_OA_OE_UY.sub(r"\1\3\2", result)
    return unicodedata.normalize("NFC", result)


def to_old_style(text: str) -> str:
    """The canonical spelling (``hòa``): cue text, card fronts and every key."""
    return normalize_diacritics(text)


def to_new_style(text: str) -> str:
    """The spelling underthesea's dictionary uses (``hoà``): the tokenizer's tagging copy only."""
    return normalize_diacritics(text, new_style=True)
