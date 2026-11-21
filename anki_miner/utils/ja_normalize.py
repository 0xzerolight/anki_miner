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
  so stdlib NFC is chosen for reuse. Observable consequences vs the guarded
  fold: compositions outside Yomitan's ``dakutenAllowed`` ranges also fold —
  the archaic katakana ワ/ヰ/ヱ/ヲ + U+3099 compose to ヷ/ヸ/ヹ/ヺ (the case the
  Yomitan test corpus pins), and likewise う/ウ + U+3099 → ゔ/ヴ — strictly
  more canonical, harmless for MeCab input.

* NFC runs as the *final* step of :func:`normalize_for_tokenization`, not at the
  position the plan's prose numbered it. The compat/radical steps are faithful
  per-character NFKD ports, and NFKD leaves katakana decomposed (パ → ハ + U+309A;
  Yomitan's own unit test asserts this decomposed output). Running NFC last
  recomposes that output so MeCab receives precomposed kana — the whole point of
  the chain. NFC is idempotent and non-destructive, so its position only affects
  whether decompositions introduced by *later* steps get recomposed.

* :func:`strip_decoration_glyphs` is not a Yomitan step at all — an owned
  addition run *first* in the chain. TV-caption sources (broadcast 字幕)
  decorate lines with continuation arrows (➡), device/speaker glyphs (📱) and
  renderer private-use codepoints; a 2026-07 audit of 729 mined cards found ➡
  on 42% of sentences. Yomitan never sees such text (its input is user-selected
  words); mined card sentences do, so the strip is owned here. Length-changing,
  hence pre-tokenization only — offsets are always computed after this chain.

* :func:`convert_halfwidth_punctuation` is owned too: Yomitan's halfwidth table
  covers kana only. Broadcast captions write ｡､｢｣ beside their halfwidth kana,
  and MeCab reads a halfwidth ｡ as a plain symbol, not a sentence end, which
  changes the words before it (オレはやる｡ tags は + やる as the one verb 逸る).
"""

from __future__ import annotations

import functools
import re
import unicodedata
from collections.abc import Sequence

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


_HALFWIDTH_PUNCTUATION = str.maketrans("｡｢｣､", "。「」、")


def convert_halfwidth_punctuation(text: str) -> str:
    """Widen the halfwidth full stop, corner brackets and comma (｡｢｣､ → 。「」、).

    One character for one, so offsets carry over. The halfwidth middle dot is
    already widened with the kana (:func:`convert_halfwidth_katakana`).
    """
    return text.translate(_HALFWIDTH_PUNCTUATION)


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


# TV-caption decoration glyphs stripped from mined text (owned, non-Yomitan —
# see module docstring). Arrows: line-continuation marks (➡ dominant in the
# audited broadcast subs, plus the plain, double-arrow and curved variants seen
# in other stations' captions: of 266,173 plain arrows in a 12,184-file subtitle
# library, all but 695 end a line, and those sit before its closing bracket).
# Device/speaker markers (📱 and ☎ phone-call lines), including U+FE0E/U+FE0F
# presentation selectors attached to stripped glyphs.
# Deliberately NOT stripped: ♪♫ music marks (the opt-in subtitle regex-filter
# presets own that choice) and 《》〈〉 narration brackets (linguistic content).
_CAPTION_GLYPHS = (
    "\u27a1"  # ➡
    "\u2192"  # →
    "\u2b05-\u2b07"  # ⬅⬆⬇
    "\u21d0\u21d2"  # ⇐⇒
    "\u2934\u2935"  # ⤴⤵
    "\U0001f4f1\U0001f4de\U0001f50a\U0001f4ac"  # 📱📞🔊💬
    "\u260e"  # ☎
)
# Renderer garbage by definition, in any language's captions. This is the half
# of the strip that is separable, and the reason :func:`strip_renderer_garbage`
# exists for callers that keep the decoration.
_RENDERER_GARBAGE = (
    "\ufffd"  # replacement character
    "\ue000-\uf8ff"  # BMP private-use area
)
_DECORATION_GLYPHS = _CAPTION_GLYPHS + _RENDERER_GARBAGE
_DECORATION_RUN_RE = re.compile(
    f"[ \t]*(?:[{_DECORATION_GLYPHS}][\ufe0e\ufe0f]?)+(?:[ \t]+(?:[{_DECORATION_GLYPHS}][\ufe0e\ufe0f]?)+)*[ \t]*"
)
_RENDERER_GARBAGE_RE = re.compile(f"[{_RENDERER_GARBAGE}]")


def strip_renderer_garbage(text: str) -> str:
    """Delete U+FFFD and BMP private-use codepoints, putting nothing in place.

    The half of :func:`strip_decoration_glyphs` that is not about captions: a
    replacement diamond or a tofu box is a decoding accident, never something
    the source wrote. No separator is left behind, unlike the caption strip —
    a language that writes no spaces has to reject one here, and a space would
    split the word the stray codepoint landed inside.
    """
    return _RENDERER_GARBAGE_RE.sub("", text)


def strip_decoration_glyphs(text: str) -> str:
    """Remove TV-caption decoration glyph runs, tidying surrounding spaces.

    A run (with any horizontal whitespace it is embedded in) collapses to a
    single space when it sits strictly between two non-space characters, and to
    nothing at a string edge — so ``あ ➡ い`` → ``あ い``, ``📱うん`` → ``うん``,
    and no doubled interior spaces are introduced. Only spaces the glyph run
    itself absorbs are touched; other interior whitespace is preserved (the
    reading/OCR path stores this text verbatim and does not pre-collapse).
    U+FE0E/U+FE0F presentation selectors are consumed only when attached to a
    stripped glyph.
    """

    def _repl(m: re.Match[str]) -> str:
        return " " if m.start() > 0 and m.end() < len(m.string) else ""

    return _DECORATION_RUN_RE.sub(_repl, text)


# The doubled parenthesis of TV captions: recalled speech, a thought, a voice in
# a flashback. The same captions also print it as the white parenthesis ｟…｠,
# which no cleaning step touches. Written as two plain parens it looked like a
# sound label: a cue wholly inside one was dropped as a caption, and the default
# cleanup pattern cut a group after other text at its first ")" and left the
# second in the sentence.
_PAREN_OPENERS = "(（"
_PAREN_CLOSERS = ")）"
_DOUBLE_PAREN_OPEN_RE = re.compile(r"\(\(|（（")
_DOUBLE_PAREN_CLOSE_RE = re.compile(r"\)\)|））")
# A doubled opener, text that holds only whole single groups (a reading, a
# note), and a doubled closer. The closer of a nested single group, as in
# (水篠(みずしの)), has no doubled opener and is never matched.
_DOUBLE_PAREN_PAIR_RE = re.compile(r"(?:\(\(|（（)((?:[^()（）]|[(（][^()（）]*[)）])*)(?:\)\)|））)")
_SINGLE_PAREN_GROUP_RE = re.compile(r"[(（][^()（）]*[)）]")


def rewrite_double_parens(text: str) -> str:
    """Respell the doubled parenthesis of a caption as the white parenthesis ｟…｠.

    ``((そういう提案です))`` and its full-width form become
    ``｟そういう提案です｠``, so the speech inside is kept like the white
    parenthesis the same captions use. A speech that opens in one cue and
    closes in a later one is respelled on each side: a doubled opener when no
    paren closes after it, a doubled closer when no paren opens before it
    (whole single groups such as a speaker label aside). Single parentheses,
    and a doubled closer that ends a nested group, are left as they are.
    """
    if _DOUBLE_PAREN_OPEN_RE.search(text) is None and _DOUBLE_PAREN_CLOSE_RE.search(text) is None:
        return text
    text = _DOUBLE_PAREN_PAIR_RE.sub("｟\\1｠", text)
    opener = _DOUBLE_PAREN_OPEN_RE.search(text)
    if opener is not None:
        rest = _SINGLE_PAREN_GROUP_RE.sub("", text[opener.end() :])
        if not any(char in rest for char in _PAREN_OPENERS + _PAREN_CLOSERS):
            text = f"{text[: opener.start()]}｟{text[opener.end() :]}"
    closer = _DOUBLE_PAREN_CLOSE_RE.search(text)
    if closer is not None:
        before = _SINGLE_PAREN_GROUP_RE.sub("", text[: closer.start()])
        if not any(char in before for char in _PAREN_OPENERS + _PAREN_CLOSERS):
            text = f"{text[: closer.start()]}｠{text[closer.end() :]}"
    return text


def normalize_for_tokenization(text: str) -> str:
    """Ordered pre-tokenization normalization chain (Yomitan ja preprocessor order).

    Applies, in order: the doubled-parenthesis respelling (owned), decoration-glyph strip (owned, non-Yomitan), halfwidth-
    katakana folding, halfwidth-punctuation widening (owned), CJK-compatibility
    NFKD, radical NFKD, then a final NFC pass. NFC both composes input NFD kana (か + U+3099 → が) and recomposes the
    katakana the NFKD steps decomposed, so MeCab always receives precomposed
    text. See the module docstring for the owned deviations from Yomitan
    (stdlib NFC in place of the guarded combining fold, NFC as the final rather
    than an intermediate step, and the decoration strip).
    """
    text = rewrite_double_parens(text)
    text = strip_decoration_glyphs(text)
    text = convert_halfwidth_katakana(text)
    text = convert_halfwidth_punctuation(text)
    text = normalize_cjk_compat(text)
    text = normalize_radicals(text)
    return unicodedata.normalize("NFC", text)


# A Chinese line in a Japanese subtitle file: a fansub's credit line (staff
# roles in simplified characters) or the Chinese half of a bilingual file. Every
# kanji is "Japanese script" to the script gate, so the line was mined as
# Japanese: cards for its kanji, with a Chinese sentence.
_KANA_RE = re.compile(r"[\u3041-\u309f\u30a1-\u30ff]")
_HAN_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")
# Chinese pronouns and sentence particles that Japanese text does not write.
# Each is in a Japanese character set as an old or rare form, so only a line
# with no kana at all is judged by them: 「你好」って言ってみて stays.
_CHINESE_ONLY_HAN = frozenset("你您妳她它們嗎呢吧啊哦嗯麼沒怎很哪")


@functools.cache
def _japanese_charset_has(char: str) -> bool:
    """True iff a Japanese character set encodes *char*.

    JIS X 0213 (``euc_jis_2004``) holds every kanji Japanese text prints,
    simplified Chinese forms excluded; ``cp932`` adds the name variants (髙, 﨑)
    Japanese systems write that the standard left out.
    """
    for codec in ("euc_jis_2004", "cp932"):
        try:
            char.encode(codec)
        except UnicodeEncodeError:
            continue
        return True
    return False


def is_chinese_line(line: str) -> bool:
    """True iff *line*, from a Japanese subtitle file, is written in Chinese.

    It is when it holds a kanji no Japanese character set has (a simplified
    form: 译, 对, 轴), kana beside it or not, or when it has no kana and holds a
    character from ``_CHINESE_ONLY_HAN``. Japanese with no kana (了解,
    中村先生) has neither. A Chinese line with neither is not recognized: the
    rule names the lines it is sure of, it does not classify the language.

    The Japanese parser is given this as its ``skip_line`` and mines nothing
    from such a line. It is not part of the text cleaning, which also serves
    Korean and a config whose language fell back to Japanese: there the line
    is the mining language, or the preview of a file that will not be mined.
    """
    han = set(_HAN_RE.findall(line))
    if not han:
        return False
    if not _CHINESE_ONLY_HAN.isdisjoint(han) and _KANA_RE.search(line) is None:
        return True
    return not all(_japanese_charset_has(char) for char in han)


# Sound labels a caption service writes in tortoise-shell brackets: 〔ドアが開く音〕,
# 〔銃声〕, 〔晴子の声〕. The same brackets carry speech in those files (a voice
# over a device, a thought: 〔考えろ！〕), so the bracket alone decides nothing.
# A label is short, holds no sentence mark and ends in a noun that names a sound.
_TORTOISE_GROUP_RE = re.compile(r"〔([^〔〕]*)〕")
_SOUND_LABEL_TAILS = (
    "音",
    "声",
    "息",
    "息遣い",
    "拍手",
    "悲鳴",
    "ノック",
    "チャイム",
    "ざわめき",
    "どよめき",
    "せき込み",
    "すすり泣き",
    "いびき",
)
_SOUND_LABEL_MAX_CHARS = 16
_SENTENCE_MARKS = frozenset("。！？!?")


def _strip_sound_label_match(match: re.Match[str]) -> str:
    """A space for a 〔…〕 group that names a sound; any other group as written."""
    inner = "".join(match.group(1).split())
    if len(inner) > _SOUND_LABEL_MAX_CHARS or not inner.endswith(_SOUND_LABEL_TAILS):
        return match.group(0)
    if not _SENTENCE_MARKS.isdisjoint(inner):
        return match.group(0)
    return " "


def strip_sound_labels(text: str) -> str:
    """Delete the 〔…〕 groups of a cue that name a sound, keeping the ones that are speech.

    ``〔鍵を掛ける音〕 待って`` keeps ``待って`` and a cue that is only
    ``〔銃声〕`` is left blank; ``〔考えろ！〕`` and ``〔お父さん…〕`` stay as
    written. A group may wrap over two physical lines. The Japanese parser is
    given this as its ``strip_labels``. Like :func:`is_chinese_line` it is not
    part of the shared text cleaning: the test reads Japanese nouns, and drops
    text for not being speech.
    """
    if "〔" not in text:
        return text
    return _TORTOISE_GROUP_RE.sub(_strip_sound_label_match, text)


# The Chinese style of a bilingual ASS file (Dial-CH, Text - CN, 中文).
# is_chinese_line judges a line alone and misses a short one written with
# shared characters (不要, 明白, 真的假的): about one line in five. Its style
# gives it away: most of the style's lines are named and few carry kana. A
# Japanese style never qualifies, kana-less lines and all, because far more
# than three in ten of its lines carry kana.
_CHINESE_STYLE_MIN_LINES = 10


def chinese_style_lines(lines: Sequence[tuple[str, str]]) -> list[bool]:
    """For each ``(style, cleaned line)`` of one file: a kana-less line of a Chinese style.

    A style is Chinese when it has at least ten lines with text,
    :func:`is_chinese_line` names two in five or more of them, and under three
    in ten carry kana. Every kana-less line of such a style is named; a line
    with kana in it (「你好」って言ってみて) is not. The Japanese parser is given
    this as its ``skip_style_lines`` and mines nothing from a named line.
    """
    counts: dict[str, list[int]] = {}
    for style, text in lines:
        if not text:
            continue
        count = counts.setdefault(style, [0, 0, 0])
        count[0] += 1
        count[1] += is_chinese_line(text)
        count[2] += _KANA_RE.search(text) is not None
    chinese = {
        style
        for style, (total, named, kana) in counts.items()
        if total >= _CHINESE_STYLE_MIN_LINES and named * 5 >= total * 2 and kana * 10 < total * 3
    }
    return [bool(text) and style in chinese and _KANA_RE.search(text) is None for style, text in lines]
