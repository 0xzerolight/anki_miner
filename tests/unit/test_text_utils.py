"""Tests for text_utils module."""

from unittest.mock import MagicMock, PropertyMock

from anki_miner.utils.text_utils import (
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
)


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
        """Mixed kanji+kana token should get full token annotated."""
        token = _make_mock_token("食べる", kana="タベル")
        tagger = MagicMock(return_value=[token])
        result = generate_furigana("食べる", tagger)
        assert result == "食べる[たべる]"

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
