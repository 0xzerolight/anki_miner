"""Normalisation and simplified/traditional variant generation for zh (spec 10.1).

OpenCC performs no Unicode normalisation of its own, so every string crossing
into it goes through :func:`normalize_zh` first — the one shared rule the spec
pins, reused by the dictionary key folding, so import-time and query-time keys
can never disagree. The profile's ``normalize`` field is
:func:`normalize_zh_text`, which adds the radical fold that only mined text
needs.

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

from anki_miner.utils.ja_normalize import normalize_radicals, strip_renderer_garbage

logger = logging.getLogger(__name__)

# Both directions: a simplified corpus queried with a traditional front needs
# t2s, and vice versa. Ordering is stable so candidate lists are deterministic.
_CONFIGS = ("s2t", "t2s")

# Converters outside the lookup ladder. tw2s folds Taiwan variants (著 -> 着) that
# plain t2s leaves alone; s2tw is the traditional spelling a card shows; s2t and
# s2hk are extra round-trip targets for script_key.
_TO_SIMPLIFIED = "tw2s"
_TO_TRADITIONAL = "s2tw"
# Simplifications script_key may propose, each still subject to the round-trip
# proof below. hk2s is second because it is the only one carrying the HK-only
# glyphs (衞 -> 卫, 敍 -> 叙) the Taiwan standard leaves alone.
_SIMPLIFY_STEPS = (_TO_SIMPLIFIED, "hk2s")
_ROUND_TRIPS = ("s2t", "s2tw", "s2hk")
_ALL_CONFIGS = frozenset((*_CONFIGS, *_SIMPLIFY_STEPS, *_ROUND_TRIPS))

# One key step can land on a spelling that folds once more (麼 -> 么 -> 幺); over
# jieba's 30k most frequent words no key needed more than two steps.
_MAX_KEY_STEPS = 4

# Glyphs no OpenCC standard emits, mapped to the one it does, for the
# round-trip proof only — never for the spelling a card or a key carries.
# 麽 (U+9EBD) is a variant of 麼 (U+9EBC), never of 幺, and OpenCC's own
# STCharacters maps it that way. Machine-converted and fan-subbed traditional
# subtitles spell 怎麽/那麽/這麽/什麽 with it, and without this the proof
# declines them and each earns a card beside its 怎么 twin.
#
# Hand-pinned rather than derived: both obvious derivations merge words that
# are not one word. Canonicalising with s2t brings its phrase rules along
# (咨詢/諮詢, 神雕/神鵰, 獨佔/獨占) and leaves 21 more pairs unfolded; doing it
# character by character breaks 43 (裡/里, 遊/游, 鹹/咸). Anything added here
# must leave 係/系, 週/周 and 麵/面 apart.
_GLYPH_VARIANTS = str.maketrans({"麽": "麼"})


def normalize_zh(text: str) -> str:
    """NFC-normalise ``text``. Single normalisation rule for the zh engine."""
    return unicodedata.normalize("NFC", text)


def normalize_zh_text(text: str) -> str:
    """The profile's ``normalize``: garbage out, then the key rule and radicals.

    Two different folds, as in ``languages/yue/normalize.py``. Keys keep
    :func:`normalize_zh` alone because they are folded symmetrically at import
    and at query and no CC-CEDICT headword carries a radical glyph. Mined TEXT
    needs more:

    * OCR and legacy sources substitute Kangxi radicals for the ideographs they
      look exactly like (U+2F24 for 大), and jieba segments the substitution as
      junk -- a 大家好 spelt that way mines 好 alone, with nothing on screen to
      say 大家 went missing. NFC runs before the fold so it sees composed input;
      its NFKD output for these blocks is a unified ideograph already.
    * A decoding accident leaves U+FFFD or a private-use codepoint in the line,
      and the card would print the diamond or the tofu box inside an otherwise
      clean sentence. :func:`strip_renderer_garbage` deletes those outright --
      no separator, because Chinese writes none and one would split the word
      the codepoint landed inside.

    Deliberately kept, unlike the Japanese chain this replaces: caption
    decoration glyphs (➡ 📱) are what the subtitle wrote, and the squared units
    in U+3300-33FF are ㎡ and ㎞ on a card, not "m2" and "km".
    """
    return normalize_radicals(normalize_zh(strip_renderer_garbage(text)))


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


def is_traditional(text: str) -> bool:
    """Whether ``text`` carries a traditional spelling of its own.

    The one "is this traditional?" probe in the engine — the card front
    (:func:`to_script`), the classifier's script (``render``) and the card's
    ``lang`` tag (``style``) all ask it here, because each of them got it wrong
    on its own. Asked with t2s, never with :func:`to_simplified`: tw2s also
    folds the Taiwan variants 著 -> 着 and 麼 -> 么, so every simplified word
    spelled with one of those (显著, 著称, 专著, 执著, 论著, 土著, 原著, 编著)
    looked traditional. The cost is 么 in its rare yāo sense (老么), which reads
    as traditional; no spelling-level rule separates the two senses.

    False without OpenCC, where nothing converts and no text can prove its
    script — the same answer as text that is spelt the same in both.
    """
    normalized = normalize_zh(text)
    return _convert_with("t2s", normalized) != normalized


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
    canonical = word.translate(_GLYPH_VARIANTS)
    for name in _SIMPLIFY_STEPS:
        simplified = _convert_with(name, word)
        if simplified == word:
            continue
        if canonical in (_convert_with(target, simplified) for target in _ROUND_TRIPS):
            return simplified
    return word


def script_key(word: str) -> str:
    """Script-neutral comparison key for ``word``: the zh ``dedup_fold``.

    A word folds to its simplified spelling only when that spelling converts
    back to exactly this word under some traditional standard (s2t, s2tw,
    s2hk). 頭髮 and 头发 share a key; 麵 stays 麵, because 面 converts back to
    面 and folding would merge noodles into face. Both tw2s and hk2s may
    propose the simplified spelling, so a Hong Kong glyph the Taiwan standard
    does not carry (衞生) folds on the same evidence, and the proof reads the
    word through ``_GLYPH_VARIANTS`` so a spelling no standard emits (怎麽)
    still meets it. The step repeats to a fixed point, which makes the key
    idempotent — stored keys are folded again on every read.

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
    than becoming a different word's front. ``"traditional"`` keeps text
    :func:`is_traditional` already answers for and converts the rest to Taiwan
    spelling. Any other value leaves the text as written.
    """
    normalized = normalize_zh(text)
    if script_variant == "simplified":
        return script_key(normalized)
    if script_variant == "traditional" and not is_traditional(normalized):
        return to_traditional(normalized)
    return normalized
