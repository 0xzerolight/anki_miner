"""Per-kanji furigana distribution.

Line-for-line Python port of Yomitan's furigana-distribution family from
``ext/js/language/ja/japanese.js`` (upstream commit ``e2ed450``), GPL-3.0.
The ported symbols are:

======================================  ============================
This module                             Yomitan ``japanese.js``
======================================  ============================
``FuriganaSegment``                     ``createFuriganaSegment``
``_is_code_point_kana``                 ``isCodePointKana``
``_convert_katakana_to_hiragana``       ``convertKatakanaToHiragana``
``_get_prolonged_hiragana``             ``getProlongedHiragana``
``_segmentize_furigana``                ``segmentizeFurigana``
``_get_furigana_kana_segments``         ``getFuriganaKanaSegments``
``get_stem_length``                     ``getStemLength``
``distribute_furigana``                 ``distributeFurigana``
``distribute_furigana_inflected``       ``distributeFuriganaInflected``
======================================  ============================

Python ``str`` is codepoint-indexed, so the upstream UTF-16 surrogate handling
in ``getStemLength`` is dropped.

``distribute_furigana`` splits a term into maximal kana / non-kana codepoint
groups and searches, via recursive backtracking, for the *unique* way to split
the reading across the non-kana (kanji) groups. When more than one split is
consistent it deliberately returns the whole-word fallback rather than guess —
so 飼い犬/かいいぬ is *not* segmented, but 入り口/いりぐち becomes
``入[い]``/``り``/``口[ぐち]`` (rendaku) and 取り引き/とりひき becomes per-kanji.

The katakana→hiragana helper here is *prolonged-mark-aware* (ー resolves to the
preceding vowel) and used only for alignment/normalization; it is intentionally
separate from :func:`anki_miner.utils.text_utils.katakana_to_hiragana`, which
leaves ー visible for displayed reading fields.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FuriganaSegment:
    """One furigana segment: ``text`` with an (optionally empty) ``reading``.

    Port of Yomitan's ``createFuriganaSegment``. Mutable because
    :func:`distribute_furigana_inflected` extends a segment's ``text`` in place.
    """

    text: str
    reading: str


@dataclass
class _FuriganaGroup:
    """A maximal run of kana or non-kana codepoints within a term."""

    is_kana: bool
    text: str
    text_normalized: str | None


# Kana codepoint ranges (hiragana + katakana blocks), from japanese.js KANA_RANGES.
_HIRAGANA_RANGE = (0x3040, 0x309F)
_KATAKANA_RANGE = (0x30A0, 0x30FF)
_KANA_RANGES = (_HIRAGANA_RANGE, _KATAKANA_RANGE)

# convertKatakanaToHiragana constants.
_KATAKANA_SMALL_KA_CODE_POINT = 0x30F5
_KATAKANA_SMALL_KE_CODE_POINT = 0x30F6
_KANA_PROLONGED_SOUND_MARK_CODE_POINT = 0x30FC
_HIRAGANA_CONVERSION_RANGE = (0x3041, 0x3096)
_KATAKANA_CONVERSION_RANGE = (0x30A1, 0x30F6)

# VOWEL_TO_KANA_MAPPING (japanese.js:121-128), inverted into KANA_TO_VOWEL_MAPPING.
_VOWEL_TO_KANA_MAPPING = {
    "a": "ぁあかがさざただなはばぱまゃやらゎわヵァアカガサザタダナハバパマャヤラヮワヵヷ",
    "i": "ぃいきぎしじちぢにひびぴみりゐィイキギシジチヂニヒビピミリヰヸ",
    "u": "ぅうくぐすずっつづぬふぶぷむゅゆるゥウクグスズッツヅヌフブプムュユルヴ",
    "e": "ぇえけげせぜてでねへべぺめれゑヶェエケゲセゼテデネヘベペメレヱヶヹ",
    "o": "ぉおこごそぞとどのほぼぽもょよろをォオコゴソゾトドノホボポモョヨロヲヺ",
    "": "のノ",
}
_KANA_TO_VOWEL_MAPPING: dict[str, str] = {}
for _vowel, _characters in _VOWEL_TO_KANA_MAPPING.items():
    for _character in _characters:
        _KANA_TO_VOWEL_MAPPING[_character] = _vowel


def _is_code_point_kana(code_point: int) -> bool:
    """True iff ``code_point`` is in a hiragana or katakana block."""
    return any(low <= code_point <= high for low, high in _KANA_RANGES)


def _get_prolonged_hiragana(previous_character: str) -> str | None:
    """Resolve a prolonged sound mark to a vowel hiragana (getProlongedHiragana)."""
    vowel = _KANA_TO_VOWEL_MAPPING.get(previous_character)
    if vowel == "a":
        return "あ"
    if vowel == "i":
        return "い"
    if vowel == "u":
        return "う"
    if vowel == "e":
        return "え"
    if vowel == "o":
        return "う"
    return None


def _convert_katakana_to_hiragana(text: str, keep_prolonged_sound_marks: bool = False) -> str:
    """Prolonged-mark-aware katakana→hiragana (convertKatakanaToHiragana).

    Alignment-only helper: ー resolves to the preceding vowel (unless
    ``keep_prolonged_sound_marks``), while small ka/ke pass through unchanged.
    """
    result = ""
    offset = _HIRAGANA_CONVERSION_RANGE[0] - _KATAKANA_CONVERSION_RANGE[0]
    for char in text:
        code_point = ord(char)
        if code_point in (_KATAKANA_SMALL_KA_CODE_POINT, _KATAKANA_SMALL_KE_CODE_POINT):
            # No change
            pass
        elif code_point == _KANA_PROLONGED_SOUND_MARK_CODE_POINT:
            if not keep_prolonged_sound_marks and len(result) > 0:
                char2 = _get_prolonged_hiragana(result[-1])
                if char2 is not None:
                    char = char2
        else:
            if _KATAKANA_CONVERSION_RANGE[0] <= code_point <= _KATAKANA_CONVERSION_RANGE[1]:
                char = chr(code_point + offset)
        result += char
    return result


def _segmentize_furigana(
    reading: str,
    reading_normalized: str,
    groups: list[_FuriganaGroup],
    groups_start: int,
) -> list[FuriganaSegment] | None:
    """Recursive backtracking split of ``reading`` across ``groups``.

    Returns ``None`` when no split (or more than one) is consistent, so the
    caller can fall back to whole-word bracketing.
    """
    group_count = len(groups) - groups_start
    if group_count <= 0:
        return [] if len(reading) == 0 else None

    group = groups[groups_start]
    is_kana = group.is_kana
    text = group.text
    text_length = len(text)
    if is_kana:
        text_normalized = group.text_normalized
        if text_normalized is not None and reading_normalized.startswith(text_normalized):
            segments = _segmentize_furigana(
                reading[text_length:],
                reading_normalized[text_length:],
                groups,
                groups_start + 1,
            )
            if segments is not None:
                if reading.startswith(text):
                    segments.insert(0, FuriganaSegment(text, ""))
                else:
                    segments[0:0] = _get_furigana_kana_segments(text, reading)
                return segments
        return None
    else:
        result: list[FuriganaSegment] | None = None
        for i in range(len(reading), text_length - 1, -1):
            segments = _segmentize_furigana(
                reading[i:],
                reading_normalized[i:],
                groups,
                groups_start + 1,
            )
            if segments is not None:
                if result is not None:
                    # More than one way to segmentize the tail; mark as ambiguous
                    return None
                segment_reading = reading[0:i]
                segments.insert(0, FuriganaSegment(text, segment_reading))
                result = segments
            # There is only one way to segmentize the last non-kana group
            if group_count == 1:
                break
        return result


def _get_furigana_kana_segments(text: str, reading: str) -> list[FuriganaSegment]:
    """Split a kana group where surface and reading disagree per character."""
    text_length = len(text)
    new_segments: list[FuriganaSegment] = []
    start = 0
    state = reading[0] == text[0]
    for i in range(1, text_length):
        new_state = reading[i] == text[i]
        if state == new_state:
            continue
        new_segments.append(FuriganaSegment(text[start:i], "" if state else reading[start:i]))
        state = new_state
        start = i
    new_segments.append(FuriganaSegment(text[start:text_length], "" if state else reading[start:text_length]))
    return new_segments


def get_stem_length(text1: str, text2: str) -> int:
    """Length of the shared leading codepoint run (getStemLength)."""
    min_length = min(len(text1), len(text2))
    i = 0
    while i < min_length and text1[i] == text2[i]:
        i += 1
    return i


def distribute_furigana(term: str, reading: str) -> list[FuriganaSegment]:
    """Distribute ``reading`` over ``term`` per kanji group (distributeFurigana).

    Returns a list of :class:`FuriganaSegment`. Falls back to a single
    ``FuriganaSegment(term, reading)`` when the term is all-kana / equal to the
    reading, or when the split is ambiguous.
    """
    if reading == term:
        # Same
        return [FuriganaSegment(term, "")]

    groups: list[_FuriganaGroup] = []
    group_pre: _FuriganaGroup | None = None
    is_kana_pre: bool | None = None
    for c in term:
        is_kana = _is_code_point_kana(ord(c))
        if is_kana == is_kana_pre:
            assert group_pre is not None
            group_pre.text += c
        else:
            group_pre = _FuriganaGroup(is_kana=is_kana, text=c, text_normalized=None)
            groups.append(group_pre)
            is_kana_pre = is_kana
    for group in groups:
        if group.is_kana:
            group.text_normalized = _convert_katakana_to_hiragana(group.text)

    reading_normalized = _convert_katakana_to_hiragana(reading)
    segments = _segmentize_furigana(reading, reading_normalized, groups, 0)
    if segments is not None:
        return segments

    # Fallback
    return [FuriganaSegment(term, reading)]


def distribute_furigana_inflected(term: str, reading: str, source: str) -> list[FuriganaSegment]:
    """Align a dictionary form against an inflected surface (distributeFuriganaInflected).

    ``source`` is the inflected surface actually seen in text; the returned
    segments carry ``source``'s okurigana rather than ``term``'s.
    """
    term_normalized = _convert_katakana_to_hiragana(term)
    reading_normalized = _convert_katakana_to_hiragana(reading)
    source_normalized = _convert_katakana_to_hiragana(source)

    main_text = term
    stem_length = get_stem_length(term_normalized, source_normalized)

    # Check if source is derived from the reading instead of the term
    reading_stem_length = get_stem_length(reading_normalized, source_normalized)
    if reading_stem_length > 0 and reading_stem_length >= stem_length:
        main_text = reading
        stem_length = reading_stem_length
        reading = f"{source[0:stem_length]}{reading[stem_length:]}"

    segments: list[FuriganaSegment] = []
    if stem_length > 0:
        main_text = f"{source[0:stem_length]}{main_text[stem_length:]}"
        segments2 = distribute_furigana(main_text, reading)
        consumed = 0
        for segment in segments2:
            text = segment.text
            start = consumed
            consumed += len(text)
            if consumed < stem_length:
                segments.append(segment)
            elif consumed == stem_length:
                segments.append(segment)
                break
            else:
                if start < stem_length:
                    segments.append(FuriganaSegment(main_text[start:stem_length], ""))
                break

    if stem_length < len(source):
        remainder = source[stem_length:]
        segment_count = len(segments)
        if segment_count > 0 and len(segments[segment_count - 1].reading) == 0:
            # Append to the last segment if it has an empty reading
            segments[segment_count - 1].text += remainder
        else:
            # Otherwise, create a new segment
            segments.append(FuriganaSegment(remainder, ""))

    return segments
