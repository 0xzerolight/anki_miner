"""Tests for text_utils module."""

from unittest.mock import MagicMock, PropertyMock

import pytest

from anki_miner.utils.text_utils import (
    _format_furigana,
    _is_kanji,
    clean_subtitle_text,
    generate_furigana,
    generate_furigana_from_tokens,
    generate_reading,
    generate_reading_from_tokens,
    has_katakana,
    hiragana_to_katakana,
    is_hiragana_only,
    is_katakana_only,
    katakana_to_hiragana,
    strip_inline_annotations,
    strip_subtitle_markup,
)


class TestStripSubtitleMarkup:
    """Tests for strip_subtitle_markup — the display-safe tag-strip subset of
    clean_subtitle_text (no MeCab-oriented Japanese normalization)."""

    def test_removes_ass_style_tags(self):
        assert strip_subtitle_markup(r"{\pos(100,200)}Hello World") == "Hello World"

    def test_removes_multiple_ass_tags(self):
        assert strip_subtitle_markup(r"{\fad(100,200)}{\b1}Bold text{\b0}") == "Bold text"

    def test_converts_line_break_tags_to_spaces(self):
        assert strip_subtitle_markup(r"Line one\NLine two\nLine three") == "Line one Line two Line three"

    def test_removes_html_tags(self):
        assert strip_subtitle_markup("<b>Bold</b> and <i>italic</i>") == "Bold and italic"

    def test_handles_empty_string(self):
        assert strip_subtitle_markup("") == ""

    def test_handles_complex_subtitle(self):
        assert strip_subtitle_markup(r"{\pos(100,200)}<b>日本語</b>\Nテスト") == "日本語 テスト"

    def test_does_not_normalize_whitespace(self):
        """Unlike clean_subtitle_text, raw whitespace is preserved (no collapse/strip)."""
        assert strip_subtitle_markup("  Too   many    spaces  ") == "  Too   many    spaces  "

    def test_does_not_apply_japanese_normalization(self):
        """The MeCab-oriented normalization must NOT run: halfwidth katakana,
        NFKD folding, and kanji-variant mapping are all left untouched."""
        assert strip_subtitle_markup("ﾊﾟｿｺﾝ") == "ﾊﾟｿｺﾝ"
        assert strip_subtitle_markup("⼭") == "⼭"  # Kangxi radical NOT folded to 山
        assert strip_subtitle_markup("𠮟られた") == "𠮟られた"  # variant NOT standardized

    def test_clean_subtitle_text_still_normalizes(self):
        """Sanity: clean_subtitle_text (which now delegates markup-strip to this
        helper) still applies the JP normalization the helper deliberately omits."""
        assert clean_subtitle_text("ﾊﾟｿｺﾝ") == "パソコン"
        assert clean_subtitle_text("𠮟られた") == "叱られた"


class TestCleanSubtitleText:
    """Tests for clean_subtitle_text function."""

    def test_removes_ass_style_tags(self):
        """Should remove ASS/SSA style tags like {\\pos(x,y)}."""
        text = r"{\pos(100,200)}Hello World"
        assert clean_subtitle_text(text) == "Hello World"

    def test_removes_multiple_ass_tags(self):
        """Should remove multiple ASS tags."""
        text = r"{\fad(100,200)}{\b1}Bold text{\b0}"
        assert clean_subtitle_text(text) == "Bold text"

    def test_removes_line_break_tags(self):
        """Should convert \\N and \\n to spaces."""
        text = r"Line one\NLine two\nLine three"
        assert clean_subtitle_text(text) == "Line one Line two Line three"

    def test_strips_leading_annotation_after_ass_hard_break(self):
        text = r"猫が好き\N（案内）犬が眠る"

        assert clean_subtitle_text(text, strip_annotations=True) == "猫が好き 犬が眠る"

    def test_removes_html_tags(self):
        """Should remove HTML tags."""
        text = "<b>Bold</b> and <i>italic</i>"
        assert clean_subtitle_text(text) == "Bold and italic"

    def test_normalizes_whitespace(self):
        """Should normalize multiple spaces to single space."""
        text = "Too   many    spaces"
        assert clean_subtitle_text(text) == "Too many spaces"

    def test_strips_leading_trailing_whitespace(self):
        """Should strip leading and trailing whitespace."""
        text = "  trimmed  "
        assert clean_subtitle_text(text) == "trimmed"

    def test_handles_empty_string(self):
        """Should handle empty string."""
        assert clean_subtitle_text("") == ""

    def test_handles_complex_subtitle(self):
        """Should handle complex subtitle with multiple tag types."""
        text = r"{\pos(100,200)}<b>日本語</b>\Nテスト"
        assert clean_subtitle_text(text) == "日本語 テスト"

    def test_normalizes_halfwidth_katakana(self):
        """Halfwidth katakana (with folded dakuten) is normalized to fullwidth."""
        assert clean_subtitle_text("ﾊﾟｿｺﾝ") == "パソコン"

    def test_normalizes_nfd_kana(self):
        """NFD dakuten kana (\u306f + U+3099) is composed to the precomposed form."""
        assert clean_subtitle_text("\u306f\u3099\u304b") == "\u3070\u304b"  # は+゙か -> ばか
        assert clean_subtitle_text("\u304b\u3099") == "\u304c"  # か+゙ -> が

    def test_normalizes_kangxi_radical(self):
        """OCR Kangxi radical ⼭ (U+2F2D) folds to the real ideograph 山."""
        assert clean_subtitle_text("⼭") == "山"

    def test_expands_cjk_compatibility_ligature(self):
        """CJK-compat ligature ㍿ expands to 株式会社."""
        assert clean_subtitle_text("㍿") == "株式会社"

    def test_standardizes_kanji_variant(self):
        """Astral variant 𠮟 (U+20B9F) is standardized to 叱."""
        assert clean_subtitle_text("𠮟られた") == "叱られた"


# --- Helpers for building mock MeCab tokens ---


def _make_mock_token(surface, kana=None, has_feature=True):
    """Build a mock fugashi word token for furigana tests."""
    token = MagicMock()
    token.surface = surface
    if has_feature and kana is not None:
        token.feature.kana = kana
    elif has_feature and kana is None:
        token.feature.kana = None
    else:
        # Simulate missing feature — AttributeError on kana access
        token.feature = MagicMock(spec=[])
        type(token.feature).kana = PropertyMock(side_effect=AttributeError)
    return token


class TestKatakanaToHiragana:
    """Tests for katakana_to_hiragana function."""

    def test_converts_basic_katakana(self):
        assert katakana_to_hiragana("タベル") == "たべる"

    def test_preserves_hiragana(self):
        assert katakana_to_hiragana("たべる") == "たべる"

    def test_preserves_long_vowel_mark(self):
        assert katakana_to_hiragana("コーヒー") == "こーひー"

    def test_empty_string(self):
        assert katakana_to_hiragana("") == ""

    def test_mixed_katakana_and_other(self):
        assert katakana_to_hiragana("タベル123") == "たべる123"

    def test_preserves_kanji(self):
        assert katakana_to_hiragana("漢字タベル") == "漢字たべる"


class TestHiraganaToKatakana:
    """Tests for hiragana_to_katakana function."""

    def test_converts_basic_hiragana(self):
        assert hiragana_to_katakana("たべる") == "タベル"

    def test_preserves_katakana(self):
        assert hiragana_to_katakana("タベル") == "タベル"

    def test_preserves_long_vowel_mark(self):
        assert hiragana_to_katakana("ぐれー") == "グレー"

    def test_empty_string(self):
        assert hiragana_to_katakana("") == ""

    def test_mixed_hiragana_and_other(self):
        assert hiragana_to_katakana("たべる123") == "タベル123"

    def test_round_trips_with_katakana_to_hiragana(self):
        for s in ("ちっぷ", "ぐれー", "さがす", "こーひー"):
            assert katakana_to_hiragana(hiragana_to_katakana(s)) == s


class TestHasKatakana:
    """Tests for has_katakana predicate."""

    def test_pure_katakana(self):
        assert has_katakana("チップ")

    def test_mixed_katakana_and_kanji(self):
        assert has_katakana("ツギハギ状")

    def test_no_katakana(self):
        assert not has_katakana("食べる")
        assert not has_katakana("さがす")
        assert not has_katakana("")


class TestGenerateFurigana:
    """Tests for generate_furigana function."""

    def test_kanji_word(self):
        """Kanji word should get furigana brackets."""
        token = _make_mock_token("王国", kana="オウコク")
        tagger = MagicMock(return_value=[token])
        result = generate_furigana("王国", tagger)
        assert result == "王国[おうこく]"

    def test_pure_hiragana(self):
        """Pure hiragana should not get brackets."""
        token = _make_mock_token("です", kana="デス")
        tagger = MagicMock(return_value=[token])
        result = generate_furigana("です", tagger)
        assert result == "です"

    def test_pure_katakana(self):
        """Pure katakana should not get brackets."""
        token = _make_mock_token("コーヒー", kana="コーヒー")
        tagger = MagicMock(return_value=[token])
        result = generate_furigana("コーヒー", tagger)
        assert result == "コーヒー"

    def test_mixed_sentence(self):
        """Sentence with kanji and kana should only annotate kanji tokens."""
        tokens = [
            _make_mock_token("スウェーデン", kana="スウェーデン"),
            _make_mock_token("や", kana="ヤ"),
            _make_mock_token("オランダ", kana="オランダ"),
            _make_mock_token("は", kana="ハ"),
            _make_mock_token("王国", kana="オウコク"),
            _make_mock_token("です", kana="デス"),
            _make_mock_token("。", kana="。"),
        ]
        tagger = MagicMock(return_value=tokens)
        result = generate_furigana("スウェーデンやオランダは王国です。", tagger)
        assert result == "スウェーデンやオランダは 王国[おうこく]です。"

    def test_kanji_with_okurigana(self):
        """Mixed kanji+kana token keeps the okurigana outside the brackets."""
        token = _make_mock_token("食べる", kana="タベル")
        tagger = MagicMock(return_value=[token])
        result = generate_furigana("食べる", tagger)
        assert result == "食[た]べる"

    def test_unknown_token_no_kana(self):
        """Token with no kana attribute should be output as-is."""
        token = _make_mock_token("謎", has_feature=False)
        tagger = MagicMock(return_value=[token])
        result = generate_furigana("謎", tagger)
        assert result == "謎"

    def test_empty_kana_falls_back(self):
        """Token with None kana should be output as-is."""
        token = _make_mock_token("謎", kana=None)
        tagger = MagicMock(return_value=[token])
        result = generate_furigana("謎", tagger)
        assert result == "謎"

    def test_empty_string(self):
        """Empty string should return empty string."""
        tagger = MagicMock(return_value=[])
        result = generate_furigana("", tagger)
        assert result == ""

    def test_trailing_okurigana_split(self):
        """終い → 終[しま]い: trailing kana stays outside the bracket."""
        token = _make_mock_token("終い", kana="シマイ")
        tagger = MagicMock(return_value=[token])
        assert generate_furigana("終い", tagger) == "終[しま]い"

    def test_leading_and_trailing_okurigana_split(self):
        """お祭り → お 祭[まつ]り: honorific お is its own bare segment and trailing
        り stays out; the separator space keeps まつ bound to 祭 alone."""
        token = _make_mock_token("お祭り", kana="オマツリ")
        tagger = MagicMock(return_value=[token])
        assert generate_furigana("お祭り", tagger) == "お 祭[まつ]り"

    def test_iteration_mark_kept_in_bracket(self):
        """時々 → 時々[ときどき]: 々 counts as kanji, not stripped as okurigana."""
        token = _make_mock_token("時々", kana="トキドキ")
        tagger = MagicMock(return_value=[token])
        assert generate_furigana("時々", tagger) == "時々[ときどき]"

    def test_internal_okurigana_per_kanji(self):
        """打ち合わせ → 打[う]ち 合[あ]わせ: interior okurigana now segments
        per kanji instead of being swallowed into one bracket."""
        token = _make_mock_token("打ち合わせ", kana="ウチアワセ")
        tagger = MagicMock(return_value=[token])
        assert generate_furigana("打ち合わせ", tagger) == "打[う]ち 合[あ]わせ"

    def test_all_kanji_not_mis_stripped(self):
        """王国 → 王国[おうこく]: an all-kanji word still gets whole-word ruby."""
        token = _make_mock_token("王国", kana="オウコク")
        tagger = MagicMock(return_value=[token])
        assert generate_furigana("王国", tagger) == "王国[おうこく]"


class TestFormatFurigana:
    """Unit tests for the per-kanji furigana formatter and kanji predicate.

    These pin the Anki-bracket rendering of ``distribute_furigana`` segments —
    the separator-space rule and the whole-word fallback — directly (the
    token-driven tests above cover the integrated path). The full segmentation
    corpus lives in ``test_furigana_distribute.py``.
    """

    def test_is_kanji_includes_iteration_mark(self):
        assert _is_kanji("一") and _is_kanji("鿿") and _is_kanji("々")
        assert not _is_kanji("あ") and not _is_kanji("ア") and not _is_kanji("〇")

    def test_is_kanji_includes_extended_ranges(self):
        # Now backed by the shared ported CJK_IDEOGRAPH_RANGES: Ext-A, compat
        # ideographs, and astral extensions count as kanji, not just BMP Unified.
        assert _is_kanji("㐀")  # U+3400 Ext A
        assert _is_kanji("﨑")  # U+FA11 compatibility ideograph
        assert _is_kanji("𠮟")  # U+20B9F Ext B (astral)

    def test_single_kanji_whole_bracket(self):
        assert _format_furigana("国", "くに") == "国[くに]"

    def test_trailing_split(self):
        assert _format_furigana("食べる", "たべる") == "食[た]べる"

    def test_leading_and_trailing_split(self):
        """お預け → お 預[あず]け: leading お becomes its own bare segment, so a
        separator space precedes 預 (あず binds to 預 alone in Anki)."""
        assert _format_furigana("お預け", "おあずけ") == "お 預[あず]け"

    def test_mismatched_script_okurigana_collapses_to_plain(self):
        """見ル/みる: the katakana okurigana's reading is just its own fold, so
        the render layer drops the redundant bracket (見[み]ル). Deliberate
        reversal of the earlier 見[み] ル[る] design (2026-07 audit F6): a
        fold-equal reading over kana carries no information on the card."""
        assert _format_furigana("見ル", "みる") == "見[み]ル"

    def test_katakana_word_reading_collapses_to_plain(self):
        """バカ力/ばかりょく: distribute brackets the katakana run against the
        hiragana reading (raw-codepoint compare, faithful port); the render
        layer collapses it, keeping furigana only over the kanji."""
        assert _format_furigana("バカ力", "ばかりょく") == "バカ 力[りょく]"

    def test_prolonged_mark_stays_inside_katakana_run(self):
        """エネルギー源/えねるぎーげん: the ー no longer orphans outside the
        bracket (was エネルギ[えねるぎ]ー 源[げん])."""
        assert _format_furigana("エネルギー源", "えねるぎーげん") == "エネルギー 源[げん]"

    @pytest.mark.parametrize(
        ("surface", "reading", "expected"),
        [
            ("カ月", "かげつ", "カ 月[げつ]"),
            ("ページ違反", "ぺーじいはん", "ページ 違反[いはん]"),
            ("スズメの涙", "すずめのなみだ", "スズメの 涙[なみだ]"),
        ],
    )
    def test_phonetic_katakana_segments_render_plain(self, surface, reading, expected):
        assert _format_furigana(surface, reading) == expected

    def test_rendaku_tail_segments_per_kanji(self):
        """入り口/いりぐち: rendaku no longer forces whole-word bracketing — it
        segments to 入[い]り 口[ぐち] (the space keeps ぐち bound to 口)."""
        assert _format_furigana("入り口", "いりぐち") == "入[い]り 口[ぐち]"

    def test_leading_kana_mismatch_falls_back(self):
        """お預/よ: the leading kana お has no match in the reading, so no
        consistent split exists and it falls back to whole-word bracketing."""
        assert _format_furigana("お預", "よ") == "お預[よ]"

    def test_reading_shorter_than_okurigana_no_crash(self):
        """Degenerate case: a reading too short to cover the kana group yields no
        consistent split, so it falls back to whole-word bracketing (no crash)."""
        assert _format_furigana("食べる", "た") == "食べる[た]"


class TestGenerateReading:
    """Tests for generate_reading function (Yomitan {reading} style)."""

    def test_kanji_word_returns_hiragana(self):
        """Pure kanji token should emit hiragana from the kana feature."""
        token = _make_mock_token("王国", kana="オウコク")
        tagger = MagicMock(return_value=[token])
        assert generate_reading("王国", tagger) == "おうこく"

    def test_kanji_with_okurigana(self):
        """Mixed kanji+kana token should emit full hiragana reading, no brackets."""
        token = _make_mock_token("食べる", kana="タベル")
        tagger = MagicMock(return_value=[token])
        assert generate_reading("食べる", tagger) == "たべる"

    def test_hiragana_passes_through(self):
        """Hiragana input should be preserved (kana feature is katakana → hiragana)."""
        token = _make_mock_token("です", kana="デス")
        tagger = MagicMock(return_value=[token])
        assert generate_reading("です", tagger) == "です"

    def test_katakana_converted_to_hiragana(self):
        """Katakana input should be converted to hiragana to match {reading} semantics."""
        token = _make_mock_token("コーヒー", kana="コーヒー")
        tagger = MagicMock(return_value=[token])
        assert generate_reading("コーヒー", tagger) == "こーひー"

    def test_mixed_sentence(self):
        """Full sentence reading should concatenate hiragana for every token."""
        tokens = [
            _make_mock_token("私", kana="ワタシ"),
            _make_mock_token("は", kana="ハ"),
            _make_mock_token("猫", kana="ネコ"),
            _make_mock_token("です", kana="デス"),
            _make_mock_token("。", kana=None),
        ]
        tagger = MagicMock(return_value=tokens)
        assert generate_reading("私は猫です。", tagger) == "わたしはねこです。"

    def test_token_missing_kana_feature_falls_back_to_surface(self):
        """Token whose feature lacks 'kana' should fall back to surface unchanged."""
        token = _make_mock_token("謎", has_feature=False)
        tagger = MagicMock(return_value=[token])
        assert generate_reading("謎", tagger) == "謎"

    def test_token_with_none_kana_falls_back_to_surface(self):
        """Token with kana=None (e.g. punctuation) should fall back to surface."""
        token = _make_mock_token("！", kana=None)
        tagger = MagicMock(return_value=[token])
        assert generate_reading("！", tagger) == "！"

    def test_empty_string(self):
        """Empty input should return empty string."""
        tagger = MagicMock(return_value=[])
        assert generate_reading("", tagger) == ""

    def test_issue_7_example(self):
        """Issue #7 example: 真竹 should yield まだけ (not the 真竹[まだけ] furigana form)."""
        token = _make_mock_token("真竹", kana="マダケ")
        tagger = MagicMock(return_value=[token])
        assert generate_reading("真竹", tagger) == "まだけ"


class TestIsHiraganaOnly:
    """Tests for is_hiragana_only (Issue #57)."""

    def test_pure_hiragana(self):
        assert is_hiragana_only("これ") is True
        assert is_hiragana_only("する") is True

    def test_pure_katakana_is_false(self):
        assert is_hiragana_only("コーヒー") is False

    def test_mixed_kana_kanji_is_false(self):
        assert is_hiragana_only("お茶") is False

    def test_kanji_is_false(self):
        assert is_hiragana_only("漢字") is False

    def test_romaji_and_digits_false(self):
        assert is_hiragana_only("abc") is False
        assert is_hiragana_only("123") is False

    def test_empty_string_false(self):
        assert is_hiragana_only("") is False


class TestIsKatakanaOnly:
    """Tests for is_katakana_only (Issue #57)."""

    def test_pure_katakana(self):
        assert is_katakana_only("カタカナ") is True

    def test_prolonged_mark_counts(self):
        # ー (U+30FC) and ・ (U+30FB) are within the katakana block.
        assert is_katakana_only("コーヒー") is True
        assert is_katakana_only("ロボット・X") is False  # X is romaji

    def test_pure_hiragana_is_false(self):
        assert is_katakana_only("これ") is False

    def test_mixed_kana_kanji_is_false(self):
        assert is_katakana_only("お茶") is False

    def test_kanji_is_false(self):
        assert is_katakana_only("漢字") is False

    def test_empty_string_false(self):
        assert is_katakana_only("") is False

    def test_halfwidth_katakana_counts(self):
        # Halfwidth katakana (U+FF66–U+FF9F) including the halfwidth prolonged
        # mark ｰ and voiced sound mark ﾞ must qualify (Issue #57 review gap).
        assert is_katakana_only("ｺｰﾋｰ") is True
        assert is_katakana_only("ｺｰﾋﾞｰ") is True
        assert is_katakana_only("ﾛﾎﾞｯﾄ") is True

    def test_mixed_fullwidth_and_halfwidth_katakana(self):
        assert is_katakana_only("コーヒーｺｰﾋｰ") is True

    def test_fullwidth_latin_is_false(self):
        # Fullwidth latin (ＡＢＣ) is not katakana.
        assert is_katakana_only("ＡＢＣ") is False


# ---------------------------------------------------------------------------
# Equivalence tests: *_from_tokens variants must match the wrapper functions
# ---------------------------------------------------------------------------


class TestGenerateFuriganaFromTokensEquivalence:
    """generate_furigana_from_tokens must be byte-identical to generate_furigana."""

    CORPUS = [
        # (text, tokens)
        (
            "王国",
            [_make_mock_token("王国", kana="オウコク")],
        ),
        (
            "です",
            [_make_mock_token("です", kana="デス")],
        ),
        (
            "コーヒー",
            [_make_mock_token("コーヒー", kana="コーヒー")],
        ),
        (
            "スウェーデンやオランダは王国です。",
            [
                _make_mock_token("スウェーデン", kana="スウェーデン"),
                _make_mock_token("や", kana="ヤ"),
                _make_mock_token("オランダ", kana="オランダ"),
                _make_mock_token("は", kana="ハ"),
                _make_mock_token("王国", kana="オウコク"),
                _make_mock_token("です", kana="デス"),
                _make_mock_token("。", kana="。"),
            ],
        ),
        (
            "無償",
            [_make_mock_token("無償", kana="ムショウ")],
        ),
        (
            "謎",
            [_make_mock_token("謎", has_feature=False)],
        ),
        (
            "",
            [],
        ),
    ]

    def test_equivalence(self):
        for text, tokens in self.CORPUS:
            tagger = MagicMock(return_value=tokens)
            expected = generate_furigana(text, tagger)
            # tagger has already been called once; reset so the wrapper call works
            tagger.reset_mock()
            tagger.return_value = tokens
            actual = generate_furigana_from_tokens(iter(tokens))
            assert actual == expected, f"Mismatch for {text!r}: {actual!r} != {expected!r}"


class TestGenerateReadingFromTokensEquivalence:
    """generate_reading_from_tokens must be byte-identical to generate_reading."""

    CORPUS = [
        (
            "王国",
            [_make_mock_token("王国", kana="オウコク")],
        ),
        (
            "食べる",
            [_make_mock_token("食べる", kana="タベル")],
        ),
        (
            "コーヒー",
            [_make_mock_token("コーヒー", kana="コーヒー")],
        ),
        (
            "私は猫です。",
            [
                _make_mock_token("私", kana="ワタシ"),
                _make_mock_token("は", kana="ハ"),
                _make_mock_token("猫", kana="ネコ"),
                _make_mock_token("です", kana="デス"),
                _make_mock_token("。", kana=None),
            ],
        ),
        (
            "謎",
            [_make_mock_token("謎", has_feature=False)],
        ),
        (
            "",
            [],
        ),
    ]

    def test_equivalence(self):
        for text, tokens in self.CORPUS:
            tagger = MagicMock(return_value=tokens)
            expected = generate_reading(text, tagger)
            tagger.reset_mock()
            tagger.return_value = tokens
            actual = generate_reading_from_tokens(iter(tokens))
            assert actual == expected, f"Mismatch for {text!r}: {actual!r} != {expected!r}"


class TestTokenSeparatorSpaceRule:
    """Token-join spaces appear only before bracket-leading renders (audit F6):
    a space before a plain-leading render shows literally on the card."""

    def test_no_space_before_plain_leading_render(self):
        # トカゲの + しっぽ切り: the second token renders "しっぽ 切[き]り"
        # (plain-leading) — joining must NOT add a visible space before しっぽ.
        tokens = [
            _make_mock_token("トカゲの", kana="トカゲノ"),
            _make_mock_token("しっぽ切り", kana="シッポキリ"),
        ]
        assert generate_furigana_from_tokens(tokens) == "トカゲのしっぽ 切[き]り"

    def test_space_kept_before_bracket_leading_render(self):
        # Existing behavior stays: …は + 王国です needs the separator so
        # おうこく binds to 王国 alone (pinned also at the 王国 test above).
        tokens = [
            _make_mock_token("は", kana="ハ"),
            _make_mock_token("王国", kana="オウコク"),
        ]
        assert generate_furigana_from_tokens(tokens) == "は 王国[おうこく]"


class TestStripInlineAnnotations:
    """Tests for strip_inline_annotations — the structural subtitle-annotation
    stripper (SFX captions, speaker tags, inline furigana). Cases use the REAL
    lines from the 816-card batch-mining audit, both paren widths."""

    # --- Pass 1: inline furigana (kanji-run + kana-only paren group ≤10) ---

    def test_inline_furigana_halfwidth_kept_kanji(self):
        # 瀕死(ひんし) → 瀕死: the furigana kana ひんし otherwise tokenizes to
        # the junk verb ひんする (audit).
        assert strip_inline_annotations("何度も瀕死(ひんし)の重傷を…") == "何度も瀕死の重傷を…"

    def test_inline_furigana_fullwidth(self):
        assert strip_inline_annotations("何度も瀕死（ひんし）の重傷を…") == "何度も瀕死の重傷を…"

    def test_inline_furigana_moronishi_building(self):
        # 諸菱(もろびし)建設 → 諸菱建設: kana もろびし tokenizes to junk もろい.
        assert strip_inline_annotations("諸菱(もろびし)建設") == "諸菱建設"

    def test_inline_furigana_nested_mixed_widths(self):
        # （水篠(みずしの) 旬(しゅん)）ん…: pass 1 removes the inner halfwidth
        # furigana groups (→ （水篠 旬）ん…), then pass 3 removes the leading
        # fullwidth speaker tag → ん…
        assert strip_inline_annotations("（水篠(みずしの) 旬(しゅん)）ん…") == "ん…"

    def test_annotation_loop_is_bounded(self):
        text = "漢" + "(あ)" * 40

        assert strip_inline_annotations(text) == "漢" + "(あ)" * 8

    def test_inline_furigana_only_after_kanji(self):
        # A kana paren group NOT preceded by kanji is not furigana; it is
        # left for pass 2/3 (here it is mid-line, so untouched).
        assert strip_inline_annotations("これは(ひんし)テスト") == "これは(ひんし)テスト"

    def test_kana_run_over_ten_chars_not_stripped(self):
        # 11 kana in the group → over the ≤10 furigana cap → left intact.
        long_kana = "あいうえおかきくけこさ"  # 11 chars
        assert len(long_kana) == 11
        text = f"漢字（{long_kana}）"
        assert strip_inline_annotations(text) == text

    def test_kana_run_exactly_ten_chars_stripped(self):
        # Boundary: exactly 10 kana after a kanji is still treated as furigana.
        ten_kana = "あいうえおかきくけこ"  # 10 chars
        assert len(ten_kana) == 10
        assert strip_inline_annotations(f"漢字（{ten_kana}）だ") == "漢字だ"

    # --- Pass 2: whole-line caption → empty string ---

    def test_whole_line_sfx_caption_fullwidth(self):
        assert strip_inline_annotations("（スマホのバイブ音）") == ""

    def test_whole_line_captions_various(self):
        assert strip_inline_annotations("（笑い声）") == ""
        assert strip_inline_annotations("（斬撃音）") == ""
        assert strip_inline_annotations("（サイレン）") == ""
        assert strip_inline_annotations("（咆哮）") == ""

    def test_whole_line_caption_halfwidth(self):
        assert strip_inline_annotations("(咆哮)") == ""

    def test_whole_line_multiple_adjacent_groups(self):
        # Solely paren groups + whitespace → whole-line → empty.
        assert strip_inline_annotations("（笑い声） （拍手）") == ""

    def test_adjacent_groups_with_trailing_text_not_whole_line(self):
        # （ブザー） （石田）… has trailing text, so it is NOT whole-line;
        # the leading speaker/SFX tags are peeled by pass 3 instead.
        result = strip_inline_annotations("（ブザー） （石田）…")
        assert "ブザー" not in result
        assert "石田" not in result
        assert result == "…"

    # --- Pass 3: leading speaker tag → stripped ---

    def test_leading_speaker_style_tag_fullwidth(self):
        assert strip_inline_annotations("（旬: 小声で）余計なことはすんなよ") == "余計なことはすんなよ"

    def test_leading_speaker_tag_halfwidth(self):
        assert strip_inline_annotations("(旬)余計なことはすんなよ") == "余計なことはすんなよ"

    def test_leading_tag_followed_by_whitespace(self):
        assert strip_inline_annotations("（田中） 勉強する") == "勉強する"

    # --- Conservative negatives ---

    def test_mid_line_kanji_paren_group_untouched(self):
        # A mid-line paren group containing kanji is left alone (conservative).
        assert strip_inline_annotations("これは（重要）です") == "これは（重要）です"

    def test_text_after_paren_group_kept_when_not_leading(self):
        assert strip_inline_annotations("これは（メモ）本文") == "これは（メモ）本文"

    def test_unbalanced_parens_unchanged(self):
        assert strip_inline_annotations("瀕死(ひんし") == "瀕死(ひんし"
        assert strip_inline_annotations("(未完了 の文") == "(未完了 の文"

    def test_ascii_parens_in_latin_text_untouched_by_pass1(self):
        # No kanji before the group and mid-line → pass 1/3 leave it; only a
        # whole-line group would be emptied by pass 2.
        assert strip_inline_annotations("hello (world) foo") == "hello (world) foo"

    def test_no_parens_unchanged(self):
        assert strip_inline_annotations("普通の日本語の文です") == "普通の日本語の文です"

    def test_empty_string(self):
        assert strip_inline_annotations("") == ""

    def test_never_raises_on_weird_input(self):
        # Pure function must never throw on malformed/adversarial paren soup.
        for weird in ("）（", "(((", ")))", "（（あ）", "あ）（い", "()()()"):
            strip_inline_annotations(weird)
