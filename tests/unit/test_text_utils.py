"""Tests for text_utils module."""

from unittest.mock import MagicMock, PropertyMock

from anki_miner.utils.text_utils import (
    clean_subtitle_text,
    extract_japanese_text,
    generate_furigana,
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


class TestExtractJapaneseText:
    """Tests for extract_japanese_text function."""

    def test_extracts_hiragana(self):
        """Should extract hiragana characters."""
        text = "Hello こんにちは world"
        assert extract_japanese_text(text) == "こんにちは"

    def test_extracts_katakana(self):
        """Should extract katakana characters."""
        text = "Welcome カタカナ here"
        assert extract_japanese_text(text) == "カタカナ"

    def test_extracts_kanji(self):
        """Should extract kanji characters."""
        text = "漢字 are Chinese characters"
        assert extract_japanese_text(text) == "漢字"

    def test_extracts_mixed_japanese(self):
        """Should extract mixed hiragana, katakana, and kanji."""
        text = "今日はカタカナとひらがな"
        assert extract_japanese_text(text) == "今日はカタカナとひらがな"

    def test_preserves_japanese_punctuation(self):
        """Should preserve common Japanese punctuation."""
        text = "こんにちは。元気ですか？"
        assert extract_japanese_text(text) == "こんにちは。元気ですか？"

    def test_removes_english(self):
        """Should remove English characters."""
        text = "ABC日本語DEF"
        assert extract_japanese_text(text) == "日本語"

    def test_handles_empty_string(self):
        """Should handle empty string."""
        assert extract_japanese_text("") == ""

    def test_handles_no_japanese(self):
        """Should return empty string when no Japanese present."""
        assert extract_japanese_text("Hello World 123") == ""

    def test_preserves_long_vowel_mark(self):
        """Should preserve the long vowel mark (ー)."""
        text = "コーヒー"
        assert extract_japanese_text(text) == "コーヒー"

    def test_preserves_middle_dot(self):
        """Should preserve the middle dot (・)."""
        text = "カレー・ライス"
        assert extract_japanese_text(text) == "カレー・ライス"


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
