"""Tests for the pre-tokenization Japanese normalization chain.

Fixture slices transcribed from Yomitan ``test/japanese-util.test.js`` (commit
e2ed450): halfwidth (:237-244), combining dakuten/handakuten (:929-1102), and
CJK-compatibility (:1110-1266). Combining behavior is asserted against
:func:`normalize_for_tokenization` because this port uses stdlib NFC in place of
Yomitan's guarded ``normalizeCombiningCharacters`` (an owned deviation). All
combining inputs use explicit ``\\u3099`` / ``\\u309A`` escapes so the decomposed
form is unambiguous in source.
"""

import unicodedata

import pytest

from anki_miner.services.ja_normalize import (
    CJK_IDEOGRAPH_RANGES,
    convert_halfwidth_katakana,
    is_cjk_ideograph,
    normalize_cjk_compat,
    normalize_for_tokenization,
    normalize_radicals,
    standardize_kanji_variants,
)

_DAKUTEN = "゙"  # combining katakana-hiragana voiced sound mark
_HANDAKUTEN = "゚"  # combining katakana-hiragana semi-voiced sound mark

# --- Halfwidth katakana (Yomitan japanese-util.test.js :237-244) -------------

HALFWIDTH_CASES = [
    ("0123456789", "0123456789"),
    ("abcdefghij", "abcdefghij"),
    ("カタカナ", "カタカナ"),
    ("ひらがな", "ひらがな"),
    ("ｶｷ", "カキ"),
    ("ｶﾞｷ", "ガキ"),  # dakuten folds onto the preceding halfwidth letter
    ("ﾆﾎﾝ", "ニホン"),
    ("ﾆｯﾎﾟﾝ", "ニッポン"),  # handakuten folds; small tsu passes through
]


@pytest.mark.parametrize("text,expected", HALFWIDTH_CASES)
def test_convert_halfwidth_katakana(text, expected):
    assert convert_halfwidth_katakana(text) == expected


def test_convert_halfwidth_katakana_is_idempotent():
    for text, expected in HALFWIDTH_CASES:
        once = convert_halfwidth_katakana(text)
        assert convert_halfwidth_katakana(once) == expected


def test_halfwidth_middle_dot_and_prolonged_mark():
    assert convert_halfwidth_katakana("ﾛﾎﾞｯﾄ･X") == "ロボット・X"
    assert convert_halfwidth_katakana("ｺｰﾋｰ") == "コーヒー"


def test_halfwidth_standalone_dakuten_passes_through():
    # A dakuten mark not preceded by a mappable halfwidth letter is untouched.
    assert convert_halfwidth_katakana("ﾞ") == "ﾞ"


# --- Combining dakuten/handakuten via NFC (test.js :929-1102) -----------------

# (base + combining mark, expected precomposed) — Yomitan testCasesDakuten.
COMBINING_DAKUTEN = [
    ("か", "が"),
    ("き", "ぎ"),
    ("く", "ぐ"),
    ("こ", "ご"),
    ("さ", "ざ"),
    ("し", "じ"),
    ("た", "だ"),
    ("つ", "づ"),
    ("は", "ば"),
    ("ひ", "び"),
    ("ふ", "ぶ"),
    ("ほ", "ぼ"),
    ("カ", "ガ"),
    ("ハ", "バ"),
    ("ホ", "ボ"),
]

# Yomitan testCasesHandakuten.
COMBINING_HANDAKUTEN = [
    ("は", "ぱ"),
    ("ひ", "ぴ"),
    ("ふ", "ぷ"),
    ("へ", "ぺ"),
    ("ほ", "ぽ"),
    ("ハ", "パ"),
    ("ホ", "ポ"),
]


@pytest.mark.parametrize("base,expected", COMBINING_DAKUTEN)
def test_nfc_composes_combining_dakuten(base, expected):
    assert normalize_for_tokenization(base + _DAKUTEN) == expected


@pytest.mark.parametrize("base,expected", COMBINING_HANDAKUTEN)
def test_nfc_composes_combining_handakuten(base, expected):
    assert normalize_for_tokenization(base + _HANDAKUTEN) == expected


# Yomitan testCasesIgnored — bases with no voiced form stay decomposed.
# (Excludes the 4 archaic katakana ワ/ヰ/ヱ/ヲ, covered separately below.)
COMBINING_IGNORED_BASES = ["な", "に", "ぬ", "ま", "や", "ん", "を", "ゐ", "ゑ", "ナ", "ン"]


@pytest.mark.parametrize("base", COMBINING_IGNORED_BASES)
@pytest.mark.parametrize("mark", [_DAKUTEN, _HANDAKUTEN])
def test_nfc_leaves_non_combinable_marks(base, mark):
    assert normalize_for_tokenization(base + mark) == base + mark


# Owned NFC deviation: ワ/ヰ/ヱ/ヲ + U+3099 compose to the archaic voiced katakana
# where Yomitan's guarded fold leaves them decomposed. Strictly more canonical.
ARCHAIC_KATAKANA = [
    ("ワ", "ヷ"),
    ("ヰ", "ヸ"),
    ("ヱ", "ヹ"),
    ("ヲ", "ヺ"),
]


@pytest.mark.parametrize("base,expected", ARCHAIC_KATAKANA)
def test_nfc_composes_archaic_katakana_dakuten(base, expected):
    assert normalize_for_tokenization(base + _DAKUTEN) == expected


def test_nfc_combining_misc():
    # Empty string.
    assert normalize_for_tokenization("") == ""
    # A leading combining mark cannot combine with anything.
    assert normalize_for_tokenization(_DAKUTEN + "ハ") == _DAKUTEN + "ハ"
    assert normalize_for_tokenization(_HANDAKUTEN + "ハ") == _HANDAKUTEN + "ハ"
    # Interior marks compose (Yomitan textCasesMisc).
    assert normalize_for_tokenization("さくらし" + _DAKUTEN + "また" + _DAKUTEN + "いこん") == "さくらじまだいこん"
    assert normalize_for_tokenization("いっほ" + _HANDAKUTEN + "ん") == "いっぽん"


def test_nfc_deviation_latin_combining_is_non_destructive():
    """Mandated regression fixture for the owned NFC deviation.

    NFC composes a non-kana canonical sequence (Latin e + U+0301 combining acute
    → precomposed é) in a mixed JP/Latin line. Precomposed é is visually
    identical to the decomposed form, so nothing user-visible is harmed, and the
    Japanese content is untouched.
    """
    line = "カフェのcaféメニュー"
    result = normalize_for_tokenization(line)
    # The e + combining acute composes to the single precomposed U+00E9.
    assert "caf\u00e9" in result
    assert "e\u0301" not in result
    # The result is exactly the canonical composition of the input: no data lost.
    assert result == unicodedata.normalize("NFC", line)
    # Rendered identically to the decomposed input (same NFD).
    assert unicodedata.normalize("NFD", result) == unicodedata.normalize("NFD", line)


# --- CJK compatibility (test.js :1110-1266) -----------------------------------


def test_normalize_cjk_compat_is_faithful_per_char_nfkd():
    """Every codepoint in U+3300–33FF folds to its per-char NFKD (Yomitan parity)."""
    for cp in range(0x3300, 0x3400):
        ch = chr(cp)
        assert normalize_cjk_compat(ch) == unicodedata.normalize("NFKD", ch)


def test_normalize_cjk_compat_leaves_out_of_range_untouched():
    # Fullwidth punctuation / kana / kanji outside U+3300–33FF must not fold.
    for ch in "！？　あアＡ１日本語、。":
        assert normalize_cjk_compat(ch) == ch


# Readable end-to-end expansions (post-NFC recomposition). NFC-guarded so a
# decomposed source literal can't produce a false pass.
COMPAT_END_TO_END = [
    ("㌀", "アパート"),
    ("㌫", "パーセント"),
    ("㌍", "カロリー"),
    ("㍍", "メートル"),
    ("㍿", "株式会社"),
    ("㍻", "平成"),
    ("㍼", "昭和"),
    ("㍘", "0点"),
    ("㏾", "31日"),
]


@pytest.mark.parametrize("text,expected", COMPAT_END_TO_END)
def test_compat_expands_through_full_chain(text, expected):
    assert normalize_for_tokenization(text) == unicodedata.normalize("NFC", expected)


def test_compat_expansion_recomposes_katakana_for_mecab():
    # ㌀ → アパート must be *precomposed* (パ is one codepoint), not ハ + U+309A,
    # so MeCab does not see fragmented NFD katakana.
    result = normalize_for_tokenization("㌀")
    assert _HANDAKUTEN not in result
    assert result == "アパート"


# --- Radicals (Yomitan CJK-util.js normalizeRadicals) -------------------------

RADICAL_CASES = [
    ("⼀", "一"),  # Kangxi radical one → U+4E00
    ("⼭", "山"),  # Kangxi radical mountain → U+5C71
    ("⼝", "口"),  # Kangxi radical mouth → U+53E3
]


@pytest.mark.parametrize("text,expected", RADICAL_CASES)
def test_normalize_radicals(text, expected):
    assert normalize_radicals(text) == expected


def test_normalize_radicals_folds_all_ranges_to_nfkd():
    # Sample each of the three radical/stroke ranges: in-range == per-char NFKD.
    for cp in (0x2F00, 0x2F2D, 0x2E80, 0x2E85, 0x31C0):
        ch = chr(cp)
        assert normalize_radicals(ch) == unicodedata.normalize("NFKD", ch)


def test_normalize_radicals_leaves_real_kanji_untouched():
    for ch in "山口一日本あ":
        assert normalize_radicals(ch) == ch


# --- Full chain + variants + kanji ranges -------------------------------------


def test_normalize_for_tokenization_combined_line():
    # Halfwidth katakana + Kangxi radical + compat ligature + NFD kana in one line.
    line = "ﾊﾟｿｺﾝで⼭にか" + _DAKUTEN + "㍿"
    result = normalize_for_tokenization(line)
    assert "ﾊ" not in result and "ﾟ" not in result  # halfwidth folded
    assert "パソコン" in result
    assert "山" in result and "⼭" not in result  # radical folded
    assert "株式会社" in result and "㍿" not in result  # compat expanded
    assert "が" in result and _DAKUTEN not in result  # NFD kana composed


def test_standardize_kanji_variants():
    assert standardize_kanji_variants("𠮟") == "叱"
    assert standardize_kanji_variants("𠮟られた") == "叱られた"
    # Length-preserving at the codepoint level (offset stability).
    assert len(standardize_kanji_variants("𠮟る")) == len("𠮟る")


def test_standardize_kanji_variants_leaves_others():
    assert standardize_kanji_variants("叱る") == "叱る"
    assert standardize_kanji_variants("日本語") == "日本語"


def test_is_cjk_ideograph_extended_ranges():
    assert is_cjk_ideograph("一")  # BMP unified
    assert is_cjk_ideograph("鿿")  # U+9FFF last unified
    assert is_cjk_ideograph("㐀")  # U+3400 Ext A
    assert is_cjk_ideograph("﨑")  # U+FA11 compatibility ideograph
    assert is_cjk_ideograph("𠮟")  # U+20B9F Ext B (astral)


def test_is_cjk_ideograph_rejects_non_kanji():
    assert not is_cjk_ideograph("あ")
    assert not is_cjk_ideograph("ア")
    assert not is_cjk_ideograph("々")  # iteration mark handled by callers
    assert not is_cjk_ideograph("〇")  # U+3007 ideographic number zero
    assert not is_cjk_ideograph("A")


def test_cjk_ideograph_ranges_are_well_formed():
    for low, high in CJK_IDEOGRAPH_RANGES:
        assert low <= high
