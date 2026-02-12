"""Tests for subtitle_parser module."""

from pathlib import Path
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions import SubtitleParseError
from anki_miner.services.subtitle_parser import SubtitleParserService

# --- Helpers for building mock MeCab tokens ---


def _make_token(surface, pos1, pos2=None, lemma=None, kana=None):
    """Build a mock fugashi word token with feature attributes."""
    token = MagicMock()
    token.surface = surface
    token.feature.pos1 = pos1
    token.feature.pos2 = pos2
    token.feature.lemma = lemma if lemma is not None else surface
    token.feature.kana = kana if kana is not None else surface
    return token


def _make_token_no_feature(surface):
    """Build a mock token that raises AttributeError on feature access."""
    token = MagicMock()
    token.surface = surface
    token.feature = MagicMock(
        spec=[],  # empty spec → attribute access raises AttributeError
    )
    type(token.feature).pos1 = PropertyMock(side_effect=AttributeError)
    type(token.feature).pos2 = PropertyMock(side_effect=AttributeError)
    type(token.feature).lemma = PropertyMock(side_effect=AttributeError)
    type(token.feature).kana = PropertyMock(side_effect=AttributeError)
    return token


class TestParseSubtitleFile:
    """Tests for parse_subtitle_file method."""

    def test_file_not_found_raises_subtitle_parse_error(self, test_config):
        with patch("anki_miner.services.subtitle_parser.fugashi.Tagger"):
            service = SubtitleParserService(test_config)

        with pytest.raises(SubtitleParseError, match="not found"):
            service.parse_subtitle_file(Path("/nonexistent/file.ass"))

    def test_parse_failure_raises_subtitle_parse_error(self, test_config, tmp_path):
        with patch("anki_miner.services.subtitle_parser.fugashi.Tagger"):
            service = SubtitleParserService(test_config)

        bad_file = tmp_path / "bad.ass"
        bad_file.write_text("not valid subtitle data!!!", encoding="utf-8")

        with (
            patch(
                "anki_miner.services.subtitle_parser.pysubs2.load",
                side_effect=Exception("parse error"),
            ),
            pytest.raises(SubtitleParseError, match="Failed to parse"),
        ):
            service.parse_subtitle_file(bad_file)

    def test_parses_words_from_lines(self, test_config, tmp_path):
        """Should extract TokenizedWord objects from subtitle lines."""
        sub_file = tmp_path / "test.ass"
        sub_file.write_text("placeholder", encoding="utf-8")

        # Build mock subtitle lines
        mock_line = MagicMock()
        mock_line.text = "食べる"
        mock_line.start = 1000  # 1 second in ms
        mock_line.end = 3000

        mock_subs = MagicMock()
        mock_subs.__iter__ = MagicMock(return_value=iter([mock_line]))

        word_token = _make_token("食べる", "動詞", lemma="食べる", kana="タベル")

        mock_tagger = MagicMock()
        mock_tagger.return_value = [word_token]

        with (
            patch("anki_miner.services.subtitle_parser.pysubs2.load", return_value=mock_subs),
            patch("anki_miner.services.subtitle_parser.fugashi.Tagger", return_value=mock_tagger),
        ):
            service = SubtitleParserService(test_config)
            words = service.parse_subtitle_file(sub_file)

        assert len(words) == 1
        assert words[0].lemma == "食べる"
        assert words[0].reading == "タベル"
        assert words[0].start_time == 1.0
        assert words[0].end_time == 3.0
        assert words[0].duration == 2.0
        assert words[0].expression_furigana != ""
        assert words[0].sentence_furigana != ""

    def test_applies_subtitle_offset(self, tmp_path):
        """Subtitle offset should shift timing."""
        config = AnkiMinerConfig(
            subtitle_offset=5.0,
            media_temp_folder=tmp_path / "media",
        )
        sub_file = tmp_path / "test.ass"
        sub_file.write_text("placeholder", encoding="utf-8")

        mock_line = MagicMock()
        mock_line.text = "勉強する"
        mock_line.start = 2000
        mock_line.end = 4000

        mock_subs = MagicMock()
        mock_subs.__iter__ = MagicMock(return_value=iter([mock_line]))

        word_token = _make_token("勉強", "名詞", lemma="勉強", kana="ベンキョウ")

        mock_tagger = MagicMock()
        mock_tagger.return_value = [word_token]

        with (
            patch("anki_miner.services.subtitle_parser.pysubs2.load", return_value=mock_subs),
            patch("anki_miner.services.subtitle_parser.fugashi.Tagger", return_value=mock_tagger),
        ):
            service = SubtitleParserService(config)
            words = service.parse_subtitle_file(sub_file)

        assert len(words) == 1
        assert words[0].start_time == pytest.approx(7.0)  # 2.0 + 5.0
        assert words[0].end_time == pytest.approx(9.0)  # 4.0 + 5.0

    def test_deduplicates_by_lemma(self, test_config, tmp_path):
        """Same lemma appearing twice should only produce one word."""
        sub_file = tmp_path / "test.ass"
        sub_file.write_text("placeholder", encoding="utf-8")

        line1 = MagicMock()
        line1.text = "食べる"
        line1.start = 1000
        line1.end = 3000

        line2 = MagicMock()
        line2.text = "食べた"
        line2.start = 4000
        line2.end = 6000

        mock_subs = MagicMock()
        mock_subs.__iter__ = MagicMock(return_value=iter([line1, line2]))

        # Both tokens have same lemma
        token1 = _make_token("食べる", "動詞", lemma="食べる", kana="タベル")
        token2 = _make_token("食べた", "動詞", lemma="食べる", kana="タベタ")

        mock_tagger = MagicMock()
        # Extra entries for generate_furigana calls (expression + sentence) after token1
        mock_tagger.side_effect = [[token1], [token1], [token1], [token2]]

        with (
            patch("anki_miner.services.subtitle_parser.pysubs2.load", return_value=mock_subs),
            patch("anki_miner.services.subtitle_parser.fugashi.Tagger", return_value=mock_tagger),
        ):
            service = SubtitleParserService(test_config)
            words = service.parse_subtitle_file(sub_file)

        assert len(words) == 1

    def test_deduplicates_by_surface(self, test_config, tmp_path):
        """Same surface form should only produce one word."""
        sub_file = tmp_path / "test.ass"
        sub_file.write_text("placeholder", encoding="utf-8")

        line1 = MagicMock()
        line1.text = "学生です"
        line1.start = 1000
        line1.end = 3000

        line2 = MagicMock()
        line2.text = "学生だ"
        line2.start = 4000
        line2.end = 6000

        mock_subs = MagicMock()
        mock_subs.__iter__ = MagicMock(return_value=iter([line1, line2]))

        token1 = _make_token("学生", "名詞", lemma="学生", kana="ガクセイ")
        token2 = _make_token("学生", "名詞", lemma="学生X", kana="ガクセイ")

        mock_tagger = MagicMock()
        # Extra entries for generate_furigana calls (expression + sentence) after token1
        mock_tagger.side_effect = [[token1], [token1], [token1], [token2]]

        with (
            patch("anki_miner.services.subtitle_parser.pysubs2.load", return_value=mock_subs),
            patch("anki_miner.services.subtitle_parser.fugashi.Tagger", return_value=mock_tagger),
        ):
            service = SubtitleParserService(test_config)
            words = service.parse_subtitle_file(sub_file)

        assert len(words) == 1

    def test_skips_empty_cleaned_text(self, test_config, tmp_path):
        """Lines that clean to empty should be skipped."""
        sub_file = tmp_path / "test.ass"
        sub_file.write_text("placeholder", encoding="utf-8")

        mock_line = MagicMock()
        mock_line.text = "{\\an8}  "
        mock_line.start = 1000
        mock_line.end = 3000

        mock_subs = MagicMock()
        mock_subs.__iter__ = MagicMock(return_value=iter([mock_line]))

        mock_tagger = MagicMock()

        with (
            patch("anki_miner.services.subtitle_parser.pysubs2.load", return_value=mock_subs),
            patch("anki_miner.services.subtitle_parser.fugashi.Tagger", return_value=mock_tagger),
            patch("anki_miner.services.subtitle_parser.clean_subtitle_text", return_value=""),
        ):
            service = SubtitleParserService(test_config)
            words = service.parse_subtitle_file(sub_file)

        assert len(words) == 0
        mock_tagger.assert_not_called()


class TestShouldIncludeWord:
    """Tests for _should_include_word method."""

    @pytest.fixture
    def service(self, test_config):
        with patch("anki_miner.services.subtitle_parser.fugashi.Tagger"):
            return SubtitleParserService(test_config)

    def test_excludes_empty_surface(self, service):
        token = _make_token("", "名詞")
        assert service._should_include_word(token) is False

    def test_excludes_whitespace_surface(self, service):
        token = _make_token("  ", "名詞")
        assert service._should_include_word(token) is False

    def test_excludes_short_words(self, service):
        """Words shorter than min_word_length (2) should be excluded."""
        token = _make_token("あ", "名詞", lemma="あ")
        assert service._should_include_word(token) is False

    @pytest.mark.parametrize("pos1", ["助詞", "助動詞", "記号", "補助記号"])
    def test_excludes_non_content_pos(self, service, pos1):
        token = _make_token("から", pos1, lemma="から")
        assert service._should_include_word(token) is False

    @pytest.mark.parametrize("pos1", ["感動詞", "フィラー"])
    def test_excludes_interjections_and_fillers(self, service, pos1):
        token = _make_token("ええ", pos1, lemma="ええ")
        assert service._should_include_word(token) is False

    @pytest.mark.parametrize("pos1", ["名詞", "動詞", "形容詞", "副詞", "形状詞"])
    def test_includes_content_pos_with_kanji(self, service, pos1):
        token = _make_token("勉強", pos1, lemma="勉強")
        assert service._should_include_word(token) is True

    @pytest.mark.parametrize(
        "pos2",
        ["非自立", "代名詞", "数詞", "接尾", "助動詞", "接頭", "固有名詞"],
    )
    def test_excludes_filtered_subtypes(self, service, pos2):
        token = _make_token("もの", "名詞", pos2=pos2, lemma="もの")
        assert service._should_include_word(token) is False

    def test_excludes_no_lemma(self, service):
        token = _make_token("何か", "名詞")
        token.feature.lemma = None
        assert service._should_include_word(token) is False

    def test_excludes_no_feature(self, service):
        token = _make_token_no_feature("何か")
        assert service._should_include_word(token) is False

    def test_excludes_single_kanji(self, service):
        token = _make_token("日", "名詞", lemma="日")
        assert service._should_include_word(token) is False

    def test_includes_kanji_compound(self, service):
        token = _make_token("勉強", "名詞", lemma="勉強")
        assert service._should_include_word(token) is True

    def test_includes_kanji_with_okurigana(self, service):
        token = _make_token("食べる", "動詞", lemma="食べる")
        assert service._should_include_word(token) is True

    def test_excludes_katakana_onomatopoeia(self, service):
        """Short katakana with repeated chars (likely onomatopoeia)."""
        token = _make_token("ドキドキ", "副詞", lemma="ドキドキ")
        # 4 chars, stripped unique = {ド,キ} = 2, len<=4 → excluded
        assert service._should_include_word(token) is False

    def test_excludes_katakana_ending_small_tsu(self, service):
        """Short katakana ending in ッ (likely sound effect)."""
        token = _make_token("バッ", "副詞", lemma="バッ")
        assert service._should_include_word(token) is False

    def test_excludes_single_char_katakana(self, service):
        """Single katakana character is too short."""
        token = _make_token("ア", "名詞", lemma="ア")
        # Already excluded by min_word_length=2, but also by katakana <2 check
        assert service._should_include_word(token) is False

    def test_includes_long_katakana(self, service):
        """Real katakana loanwords should pass."""
        token = _make_token("コンピューター", "名詞", lemma="コンピューター")
        assert service._should_include_word(token) is True

    def test_excludes_pos_not_in_allowed(self, service):
        """POS types not in allowed list should be excluded."""
        token = _make_token("接続詞", "接続詞", lemma="接続詞")
        assert service._should_include_word(token) is False


class TestExtractLemma:
    """Tests for _extract_lemma method."""

    @pytest.fixture
    def service(self, test_config):
        with patch("anki_miner.services.subtitle_parser.fugashi.Tagger"):
            return SubtitleParserService(test_config)

    def test_returns_lemma(self, service):
        token = _make_token("食べた", "動詞", lemma="食べる")
        assert service._extract_lemma(token) == "食べる"

    def test_falls_back_to_surface(self, service):
        token = _make_token_no_feature("食べた")
        assert service._extract_lemma(token) == "食べた"

    def test_strips_english_after_hyphen(self, service):
        token = _make_token("スクランブル", "名詞", lemma="スクランブル-scramble")
        assert service._extract_lemma(token) == "スクランブル"


class TestExtractReading:
    """Tests for _extract_reading method."""

    @pytest.fixture
    def service(self, test_config):
        with patch("anki_miner.services.subtitle_parser.fugashi.Tagger"):
            return SubtitleParserService(test_config)

    def test_returns_kana(self, service):
        token = _make_token("食べる", "動詞", kana="タベル")
        assert service._extract_reading(token) == "タベル"

    def test_falls_back_to_surface(self, service):
        token = _make_token_no_feature("食べる")
        assert service._extract_reading(token) == "食べる"
