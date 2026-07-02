"""Pre-tokenization Japanese text normalization.

An ordered normalization chain applied to cleaned subtitle text *before* it
reaches MeCab, so that input classes which silently break tokenization are
folded to their canonical forms first: halfwidth katakana (ﾊﾟｿｺﾝ), NFD kana
(か + U+3099), CJK-compatibility ligatures (㌀ ㍿ ㍻) and OCR Kangxi-radical
substitutions (⼀ U+2F00 for 一 U+4E00).

Ported from Yomitan (GPL-3.0), upstream commit ``e2ed450``:

======================================  ===================================================
This module                             Yomitan source
======================================  ===================================================
``convert_halfwidth_katakana``          ``ext/js/language/ja/japanese.js`` ``convertHalfWidthKanaToFullWidth`` + ``HALFWIDTH_KATAKANA_MAPPING``
``normalize_cjk_compat``                ``ext/js/language/ja/japanese.js`` ``normalizeCJKCompatibilityCharacters`` + ``CJK_COMPATIBILITY``
``normalize_radicals``                  ``ext/js/language/CJK-util.js`` ``normalizeRadicals`` + ``CJK_RADICALS_RANGES``
``CJK_IDEOGRAPH_RANGES`` / ``is_cjk_ideograph``  ``ext/js/language/CJK-util.js`` ``CJK_IDEOGRAPH_RANGES`` / ``isCodePointInRanges``
======================================  ===================================================

Owned deviations from Yomitan (see the pinned plan item 1.3):

* Combining-character normalization uses stdlib :func:`unicodedata.normalize`
  ``NFC`` rather than a port of Yomitan's guarded ``normalizeCombiningCharacters``.
  NFC composes more than U+3099/U+309A (any canonical composition anywhere in
  the line), which Yomitan's guarded fold avoids because it must preserve the
  on-screen lookup string byte-for-byte. Here the cleaned line *is* the stored
  card sentence and canonical composition is non-destructive for subtitle text,
  so stdlib NFC is chosen for reuse. One observable consequence on the Yomitan
  test corpus: the archaic katakana ワ/ヰ/ヱ/ヲ + U+3099 compose to ヷ/ヸ/ヹ/ヺ
  (Yomitan's guard leaves them decomposed) — strictly more canonical, harmless.

* NFC runs as the *final* step of :func:`normalize_for_tokenization`, not at the
  position the plan's prose numbered it. The compat/radical steps are faithful
  per-character NFKD ports, and NFKD leaves katakana decomposed (パ → ハ + U+309A;
  Yomitan's own unit test asserts this decomposed output). Running NFC last
  recomposes that output so MeCab receives precomposed kana — the whole point of
  the chain. NFC is idempotent and non-destructive, so its position only affects
  whether decompositions introduced by *later* steps get recomposed.
"""

from __future__ import annotations

import unicodedata

# Halfwidth katakana → fullwidth mapping table. Each value is exactly three
# characters: [base, dakuten form, handakuten form]; '-' marks an absent form.
# Ported verbatim from Yomitan ext/js/language/ja/japanese.js (commit e2ed450),
# HALFWIDTH_KATAKANA_MAPPING.
_HALFWIDTH_KATAKANA_MAPPING: dict[str, str] = {
    "･": "・--",
    "ｦ": "ヲヺ-",
    "ｧ": "ァ--",
    "ｨ": "ィ--",
    "ｩ": "ゥ--",
    "ｪ": "ェ--",
    "ｫ": "ォ--",
    "ｬ": "ャ--",
    "ｭ": "ュ--",
    "ｮ": "ョ--",
    "ｯ": "ッ--",
    "ｰ": "ー--",
    "ｱ": "ア--",
    "ｲ": "イ--",
    "ｳ": "ウヴ-",
    "ｴ": "エ--",
    "ｵ": "オ--",
    "ｶ": "カガ-",
    "ｷ": "キギ-",
    "ｸ": "クグ-",
    "ｹ": "ケゲ-",
    "ｺ": "コゴ-",
    "ｻ": "サザ-",
    "ｼ": "シジ-",
    "ｽ": "スズ-",
    "ｾ": "セゼ-",
    "ｿ": "ソゾ-",
    "ﾀ": "タダ-",
    "ﾁ": "チヂ-",
    "ﾂ": "ツヅ-",
    "ﾃ": "テデ-",
    "ﾄ": "トド-",
    "ﾅ": "ナ--",
    "ﾆ": "ニ--",
    "ﾇ": "ヌ--",
    "ﾈ": "ネ--",
    "ﾉ": "ノ--",
    "ﾊ": "ハバパ",
    "ﾋ": "ヒビピ",
    "ﾌ": "フブプ",
    "ﾍ": "ヘベペ",
    "ﾎ": "ホボポ",
    "ﾏ": "マ--",
    "ﾐ": "ミ--",
    "ﾑ": "ム--",
    "ﾒ": "メ--",
    "ﾓ": "モ--",
    "ﾔ": "ヤ--",
    "ﾕ": "ユ--",
    "ﾖ": "ヨ--",
    "ﾗ": "ラ--",
    "ﾘ": "リ--",
    "ﾙ": "ル--",
    "ﾚ": "レ--",
    "ﾛ": "ロ--",
    "ﾜ": "ワ--",
    "ﾝ": "ン--",
}

_HALFWIDTH_DAKUTEN = "ﾞ"  # ﾞ
_HALFWIDTH_HANDAKUTEN = "ﾟ"  # ﾟ

# CJK ideograph ranges, ported verbatim from Yomitan ext/js/language/CJK-util.js
# (commit e2ed450) CJK_IDEOGRAPH_RANGES. Extends the legacy BMP-only kanji check
# to Ext A–I, compatibility ideographs (﨑) and the astral extensions (𠮟).
CJK_IDEOGRAPH_RANGES: tuple[tuple[int, int], ...] = (
    (0x4E00, 0x9FFF),  # CJK Unified Ideographs
    (0x3400, 0x4DBF),  # Extension A
    (0x20000, 0x2A6DF),  # Extension B
    (0x2A700, 0x2B73F),  # Extension C
    (0x2B740, 0x2B81F),  # Extension D
    (0x2B820, 0x2CEAF),  # Extension E
    (0x2CEB0, 0x2EBEF),  # Extension F
    (0x30000, 0x3134F),  # Extension G
    (0x31350, 0x323AF),  # Extension H
    (0x2EBF0, 0x2EE5F),  # Extension I
    (0xF900, 0xFAFF),  # CJK Compatibility Ideographs
    (0x2F800, 0x2FA1F),  # CJK Compatibility Ideographs Supplement
)

# NFKD-normalization ranges (Yomitan CJK-util.js). Compat block is folded whole;
# the three radical blocks map OCR/legacy radical glyphs back to real kanji.
_CJK_COMPATIBILITY_RANGE: tuple[int, int] = (0x3300, 0x33FF)
_CJK_RADICALS_RANGES: tuple[tuple[int, int], ...] = (
    (0x2F00, 0x2FDF),  # Kangxi Radicals
    (0x2E80, 0x2EFF),  # CJK Radicals Supplement
    (0x31C0, 0x31EF),  # CJK Strokes
)

# Minimal kanji-variant standardization. The full Yomitan kanji-processor variant
# table is license-unverified and deferred; only the astral 𠮟 (U+20B9F) →
# 叱 (U+53F1) swap is applied here (both single Python codepoints, so the swap is
# length-preserving and keeps character offsets stable).
_KANJI_VARIANT_TRANSLATION: dict[int, str] = {0x20B9F: "叱"}


def _in_ranges(code_point: int, ranges: tuple[tuple[int, int], ...]) -> bool:
    """True iff ``code_point`` falls inside any ``[min, max]`` range (inclusive).

    Port of Yomitan ``isCodePointInRanges`` (``CJK-util.js``).
    """
    return any(low <= code_point <= high for low, high in ranges)


def is_cjk_ideograph(char: str) -> bool:
    """True iff ``char`` is a CJK ideograph in any ``CJK_IDEOGRAPH_RANGES`` block.

    Shared kanji-membership predicate for the whole codebase, replacing the two
    former BMP-only checks (``text_utils._is_kanji`` and ``morphology`` word
    filter). ``char`` must be a single Python codepoint. Note this does NOT
    include the iteration mark 々 (U+3005) — callers that need it add it
    explicitly, matching Yomitan's kanji-range semantics.
    """
    return _in_ranges(ord(char), CJK_IDEOGRAPH_RANGES)


def convert_halfwidth_katakana(text: str) -> str:
    """Convert halfwidth katakana to fullwidth, folding a following dakuten mark.

    Port of Yomitan ``convertHalfWidthKanaToFullWidth`` (``japanese.js``). Each
    mapped halfwidth letter looks ahead one character: a following U+FF9E
    (dakuten) or U+FF9F (handakuten) selects the voiced/semi-voiced fullwidth
    form and is consumed; an invalid diacritic ('-' in the table) is ignored and
    the base form kept. ``ﾊﾟｿｺﾝ`` → ``パソコン``.
    """
    result: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        char = text[i]
        mapping = _HALFWIDTH_KATAKANA_MAPPING.get(char)
        if mapping is None:
            result.append(char)
            i += 1
            continue

        index = 0
        next_char = text[i + 1] if i + 1 < n else ""
        if next_char == _HALFWIDTH_DAKUTEN:
            index = 1
        elif next_char == _HALFWIDTH_HANDAKUTEN:
            index = 2

        converted = mapping[index]
        if index > 0:
            if converted == "-":  # No voiced/semi-voiced form; ignore the mark.
                index = 0
                converted = mapping[0]
            else:
                i += 1  # Consume the folded diacritic.

        result.append(converted)
        i += 1

    return "".join(result)


def normalize_cjk_compat(text: str) -> str:
    """NFKD-fold CJK Compatibility characters (U+3300–33FF) in place.

    Port of Yomitan ``normalizeCJKCompatibilityCharacters`` (``japanese.js``).
    Expands squared-katakana abbreviations and ligatures (㌀ → アパート,
    ㍿ → 株式会社, ㍻ → 平成). Faithful per-character NFKD, so katakana with a
    voiced sound mark decomposes (パ → ハ + U+309A); the trailing NFC step in
    :func:`normalize_for_tokenization` recomposes it. Characters outside the
    range pass through untouched, so fullwidth punctuation the on-screen
    sentence should keep is not collateral-folded by a blanket NFKC/NFKD.
    """
    low, high = _CJK_COMPATIBILITY_RANGE
    result: list[str] = []
    for char in text:
        if low <= ord(char) <= high:
            result.append(unicodedata.normalize("NFKD", char))
        else:
            result.append(char)
    return "".join(result)


def normalize_radicals(text: str) -> str:
    """NFKD-fold Kangxi/CJK radical & stroke glyphs back to real kanji.

    Port of Yomitan ``normalizeRadicals`` (``CJK-util.js``). OCR and legacy
    sources substitute radical glyphs for the ideographs they resemble
    (⼀ U+2F00 for 一 U+4E00); folding the three radical/stroke blocks
    (Kangxi, CJK Radicals Supplement, CJK Strokes) restores the unified
    ideograph so it tokenizes and looks up correctly. Characters outside the
    ranges pass through untouched.
    """
    result: list[str] = []
    for char in text:
        if _in_ranges(ord(char), _CJK_RADICALS_RANGES):
            result.append(unicodedata.normalize("NFKD", char))
        else:
            result.append(char)
    return "".join(result)


def standardize_kanji_variants(text: str) -> str:
    """Apply the minimal kanji-variant standardization map (𠮟 → 叱).

    Kept separate from :func:`normalize_for_tokenization` because it is a
    deliberately minimal, license-verified subset of Yomitan's ``standardizeKanji``
    preprocessor rather than a full port. Length-preserving at the codepoint
    level, so it does not perturb character offsets.
    """
    return text.translate(_KANJI_VARIANT_TRANSLATION)


def normalize_for_tokenization(text: str) -> str:
    """Ordered pre-tokenization normalization chain (Yomitan ja preprocessor order).

    Applies, in order: halfwidth-katakana folding, CJK-compatibility NFKD,
    radical NFKD, then a final NFC pass. NFC both composes input NFD kana
    (か + U+3099 → が) and recomposes the katakana the NFKD steps decomposed, so
    MeCab always receives precomposed text. See the module docstring for the two
    owned deviations from Yomitan (stdlib NFC in place of the guarded combining
    fold, and NFC as the final rather than an intermediate step).
    """
    text = convert_halfwidth_katakana(text)
    text = normalize_cjk_compat(text)
    text = normalize_radicals(text)
    return unicodedata.normalize("NFC", text)
