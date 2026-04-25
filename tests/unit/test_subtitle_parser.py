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


class TestExpressionFuriganaFromSurface:
    """Regression: ExpressionFurigana must be generated from surface, not lemma.

    Lapis renders ExpressionFurigana as the headword, so it must agree with the
    Expression field (which is the surface). For tokens where surface != lemma
    (e.g. 豪腕 vs 剛腕), generating furigana from lemma would put a different
    kanji on the card than the one in Expression.
    """

    def test_generate_furigana_called_with_surface_for_expression(self, test_config, tmp_path):
        """generate_furigana for the Expression field must be called with surface."""
        sub_file = tmp_path / "test.ass"
        sub_file.write_text("placeholder", encoding="utf-8")

        mock_line = MagicMock()
        mock_line.text = "彼は豪腕の投手だ"
        mock_line.start = 1000
        mock_line.end = 3000

        mock_subs = MagicMock()
        mock_subs.__iter__ = MagicMock(return_value=iter([mock_line]))

        # surface ≠ lemma — unidic sometimes maps 豪腕 to 剛腕 as the lemma.
        word_token = _make_token("豪腕", "名詞", lemma="剛腕", kana="ゴウワン")

        mock_tagger = MagicMock()
        mock_tagger.return_value = [word_token]

        with (
            patch("anki_miner.services.subtitle_parser.pysubs2.load", return_value=mock_subs),
            patch("anki_miner.services.subtitle_parser.fugashi.Tagger", return_value=mock_tagger),
            patch(
                "anki_miner.services.subtitle_parser.generate_furigana",
                return_value="stub",
            ) as mock_furigana,
        ):
            service = SubtitleParserService(test_config)
            service.parse_subtitle_file(sub_file)

        # First call generates expression furigana, second generates sentence furigana.
        # The first positional arg of the first call must be the surface, not the lemma.
        assert mock_furigana.call_count >= 1
        first_call_text = mock_furigana.call_args_list[0].args[0]
        assert (
            first_call_text == "豪腕"
        ), f"Expression furigana must be generated from surface (豪腕), got {first_call_text!r}"


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
        ["非自立", "数詞", "接尾", "助動詞", "接頭", "固有名詞"],
    )
    def test_excludes_filtered_subtypes(self, service, pos2):
        token = _make_token("物事", "名詞", pos2=pos2, lemma="物事")
        assert service._should_include_word(token) is False

    @pytest.mark.parametrize("surface", ["彼", "誰", "何", "我々", "貴様"])
    def test_includes_pronouns_by_default(self, service, surface):
        """Pronouns (pos1=代名詞) like 彼/誰/何/我々/貴様 must be mined."""
        token = _make_token(surface, "代名詞", pos2="*", lemma=surface)
        assert service._should_include_word(token) is True

    @pytest.mark.parametrize("surface", ["これ", "それ", "ここ", "あれ"])
    def test_excludes_hiragana_pronouns(self, service, surface):
        """Hiragana-only pronouns must still be filtered as noise."""
        token = _make_token(surface, "代名詞", pos2="*", lemma=surface)
        assert service._should_include_word(token) is False

    def test_excludes_no_lemma(self, service):
        token = _make_token("何か", "名詞")
        token.feature.lemma = None
        assert service._should_include_word(token) is False

    def test_excludes_no_feature(self, service):
        token = _make_token_no_feature("何か")
        assert service._should_include_word(token) is False

    def test_includes_single_kanji_by_default(self, service):
        """Single kanji content words are always admitted."""
        token = _make_token("皿", "名詞", lemma="皿")
        assert service._should_include_word(token) is True

    def test_excludes_single_katakana(self, service):
        """Single katakana characters are filtered as noise."""
        token = _make_token("ア", "名詞", lemma="ア")
        assert service._should_include_word(token) is False

    def test_excludes_single_hiragana(self, service):
        """Single hiragana characters are filtered as noise."""
        token = _make_token("あ", "名詞", lemma="あ")
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
        """Single katakana character is rejected by the katakana <2 floor."""
        token = _make_token("ア", "名詞", lemma="ア")
        assert service._should_include_word(token) is False

    def test_includes_long_katakana(self, service):
        """Real katakana loanwords should pass."""
        token = _make_token("コンピューター", "名詞", lemma="コンピューター")
        assert service._should_include_word(token) is True

    def test_excludes_pos_not_in_allowed(self, service):
        """POS types not in allowed list should be excluded."""
        token = _make_token("接続詞", "接続詞", lemma="接続詞")
        assert service._should_include_word(token) is False

    def test_excludes_hiragana_only_word(self, service):
        """Hiragana-only words (no kanji, not katakana) should return False."""
        token = _make_token("ところ", "名詞", lemma="ところ")
        assert service._should_include_word(token) is False

    def test_excludes_three_char_katakana_ending_tsu(self, service):
        """Three-char katakana ending in ッ should be excluded as sound effect."""
        token = _make_token("ガッ", "副詞", lemma="ガッ")
        assert service._should_include_word(token) is False
        token2 = _make_token("ドンッ", "副詞", lemma="ドンッ")
        assert service._should_include_word(token2) is False


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


class TestParseRawEntries:
    """Tests for parse_raw_entries method."""

    def test_returns_tuples_of_start_end_text(self, test_config, tmp_path):
        """Should return list of (start, end, text) tuples."""
        sub_file = tmp_path / "test.ass"
        sub_file.write_text("placeholder", encoding="utf-8")

        mock_line = MagicMock()
        mock_line.text = "こんにちは"
        mock_line.start = 1000
        mock_line.end = 3000

        mock_subs = MagicMock()
        mock_subs.__iter__ = MagicMock(return_value=iter([mock_line]))

        with (
            patch("anki_miner.services.subtitle_parser.fugashi.Tagger"),
            patch("anki_miner.services.subtitle_parser.pysubs2.load", return_value=mock_subs),
        ):
            service = SubtitleParserService(test_config)
            entries = service.parse_raw_entries(sub_file)

        assert len(entries) == 1
        start, end, text = entries[0]
        assert start == pytest.approx(1.0)
        assert end == pytest.approx(3.0)
        assert text == "こんにちは"

    def test_skips_empty_lines(self, test_config, tmp_path):
        """Should skip subtitle lines with empty text."""
        sub_file = tmp_path / "test.ass"
        sub_file.write_text("placeholder", encoding="utf-8")

        line1 = MagicMock()
        line1.text = ""
        line1.start = 0
        line1.end = 1000

        line2 = MagicMock()
        line2.text = "テスト"
        line2.start = 2000
        line2.end = 4000

        mock_subs = MagicMock()
        mock_subs.__iter__ = MagicMock(return_value=iter([line1, line2]))

        with (
            patch("anki_miner.services.subtitle_parser.fugashi.Tagger"),
            patch("anki_miner.services.subtitle_parser.pysubs2.load", return_value=mock_subs),
        ):
            service = SubtitleParserService(test_config)
            entries = service.parse_raw_entries(sub_file)

        assert len(entries) == 1
        assert entries[0][2] == "テスト"

    def test_file_not_found_raises_error(self, test_config):
        """Should raise SubtitleParseError for missing file."""
        with patch("anki_miner.services.subtitle_parser.fugashi.Tagger"):
            service = SubtitleParserService(test_config)

        with pytest.raises(SubtitleParseError, match="not found"):
            service.parse_raw_entries(Path("/nonexistent/file.ass"))

    def test_applies_subtitle_offset(self, tmp_path):
        """Should apply config subtitle_offset to timing."""

        config = AnkiMinerConfig(subtitle_offset=2.0)

        sub_file = tmp_path / "test.ass"
        sub_file.write_text("placeholder", encoding="utf-8")

        mock_line = MagicMock()
        mock_line.text = "テスト"
        mock_line.start = 1000  # 1.0s
        mock_line.end = 3000  # 3.0s

        mock_subs = MagicMock()
        mock_subs.__iter__ = MagicMock(return_value=iter([mock_line]))

        with (
            patch("anki_miner.services.subtitle_parser.fugashi.Tagger"),
            patch("anki_miner.services.subtitle_parser.pysubs2.load", return_value=mock_subs),
        ):
            service = SubtitleParserService(config)
            entries = service.parse_raw_entries(sub_file)

        assert len(entries) == 1
        start, end, _ = entries[0]
        assert start == pytest.approx(3.0)  # 1.0 + 2.0 offset
        assert end == pytest.approx(5.0)  # 3.0 + 2.0 offset


class TestCompoundReassembly:
    """Tests for _merge_compound_suffixes — 名詞+接尾辞 reassembly."""

    @pytest.fixture
    def service(self, test_config):
        with patch("anki_miner.services.subtitle_parser.fugashi.Tagger"):
            return SubtitleParserService(test_config)

    @pytest.mark.parametrize(
        "head_surface,head_pos2,suffix_surface,suffix_pos2,expected",
        [
            ("刑務", "普通名詞", "所", "名詞的", "刑務所"),
            ("爆発", "普通名詞", "的", "形状詞的", "爆発的"),
            ("死傷", "普通名詞", "者", "名詞的", "死傷者"),
            ("入院", "普通名詞", "中", "名詞的", "入院中"),
        ],
    )
    def test_merges_noun_plus_nominal_suffix(
        self, service, head_surface, head_pos2, suffix_surface, suffix_pos2, expected
    ):
        head = _make_token(head_surface, "名詞", pos2=head_pos2, lemma=head_surface)
        suffix = _make_token(suffix_surface, "接尾辞", pos2=suffix_pos2, lemma=suffix_surface)
        result = service._merge_compound_suffixes([head, suffix])
        assert len(result) == 1
        merged = result[0]
        assert merged.surface == expected
        assert merged.feature.lemma == expected
        assert merged.feature.pos1 == "名詞"
        assert merged.feature.pos2 == head_pos2

    def test_suffix_at_line_start_unchanged(self, service):
        """Bare suffix with no preceding 名詞 head must not be merged."""
        suffix = _make_token("所", "接尾辞", pos2="名詞的", lemma="所")
        result = service._merge_compound_suffixes([suffix])
        assert len(result) == 1
        assert result[0] is suffix

    def test_noun_plus_noun_no_merge(self, service):
        """Two adjacent 名詞 tokens (no suffix) emit independently."""
        a = _make_token("学校", "名詞", pos2="普通名詞", lemma="学校")
        b = _make_token("生活", "名詞", pos2="普通名詞", lemma="生活")
        result = service._merge_compound_suffixes([a, b])
        assert len(result) == 2
        assert result[0] is a
        assert result[1] is b

    def test_chain_merge_noun_two_suffixes(self, service):
        """Chain: 入院(名詞) + 中(接尾辞,名詞的) + 的(接尾辞,形状詞的) → 入院中的."""
        head = _make_token("入院", "名詞", pos2="普通名詞", lemma="入院")
        suf1 = _make_token("中", "接尾辞", pos2="名詞的", lemma="中")
        suf2 = _make_token("的", "接尾辞", pos2="形状詞的", lemma="的")
        result = service._merge_compound_suffixes([head, suf1, suf2])
        assert len(result) == 1
        merged = result[0]
        assert merged.surface == "入院中的"
        assert merged.feature.lemma == "入院中的"
        assert merged.feature.pos1 == "名詞"

    def test_non_nominal_suffix_pos2_not_merged(self, service):
        """Suffix with pos2 outside _NOMINAL_SUFFIX_POS2 (e.g. 動詞的) is not merged."""
        head = _make_token("勉強", "名詞", pos2="普通名詞", lemma="勉強")
        suffix = _make_token("する", "接尾辞", pos2="動詞的", lemma="する")
        result = service._merge_compound_suffixes([head, suffix])
        assert len(result) == 2
        assert result[0] is head
        assert result[1] is suffix

    def test_empty_token_list(self, service):
        """Empty input must return an empty list (no IndexError, no merge)."""
        assert service._merge_compound_suffixes([]) == []

    def test_token_without_feature_passes_through(self, service):
        """A token whose feature.pos1 raises AttributeError must pass through unchanged."""
        bad = _make_token_no_feature("???")
        result = service._merge_compound_suffixes([bad])
        assert len(result) == 1
        assert result[0] is bad

    def test_propagates_proper_noun_pos2(self, service):
        """A 固有名詞 head + nominal suffix must keep pos2=固有名詞 on the synthetic.

        This matters because the include filter drops 固有名詞 via
        config.excluded_subtypes, so the synthetic must carry the head's
        pos2 to be filtered out correctly.
        """
        head = _make_token("田中", "名詞", pos2="固有名詞", lemma="田中")
        suffix = _make_token("様", "接尾辞", pos2="名詞的", lemma="様")
        result = service._merge_compound_suffixes([head, suffix])
        assert len(result) == 1
        merged = result[0]
        assert merged.surface == "田中様"
        assert merged.feature.pos2 == "固有名詞"


class TestPrefixCompounds:
    """Tests for _merge_prefix_compounds — 接頭辞 + 名詞/形状詞 merging."""

    @pytest.fixture
    def service(self, test_config):
        with patch("anki_miner.services.subtitle_parser.fugashi.Tagger"):
            return SubtitleParserService(test_config)

    @pytest.mark.parametrize(
        "prefix_surface,root_surface,root_pos1,expected",
        [
            ("不", "可能", "形状詞", "不可能"),
            ("無", "関心", "名詞", "無関心"),
            ("非", "常識", "名詞", "非常識"),
            ("反", "社会", "名詞", "反社会"),
            ("超", "能力", "名詞", "超能力"),
        ],
    )
    def test_merges_whitelisted_prefix_plus_nominal(
        self, service, prefix_surface, root_surface, root_pos1, expected
    ):
        """Whitelisted prefix + 名詞/形状詞 root → single synthetic emitted as 名詞.

        pos1 is normalized to 名詞 regardless of root_pos1 so that downstream
        noun-suffix merge can chain (e.g. 不+可能+性 → 不可能 → 不可能性).
        """
        prefix = _make_token(prefix_surface, "接頭辞", pos2="*", lemma=prefix_surface)
        root_pos2 = "一般" if root_pos1 == "形状詞" else "普通名詞"
        root = _make_token(root_surface, root_pos1, pos2=root_pos2, lemma=root_surface)
        result = service._merge_compound_suffixes([prefix, root])
        assert len(result) == 1
        merged = result[0]
        assert merged.surface == expected
        assert merged.feature.lemma == expected
        # pos1 is normalized to 名詞 to allow chaining with noun-suffix merge.
        assert merged.feature.pos1 == "名詞"
        # pos2 inherits from root (with "*" coerced to 普通名詞).
        assert merged.feature.pos2 == root_pos2

    def test_non_whitelisted_prefix_not_merged(self, service):
        """Prefix not in _PREFIX_WHITELIST (e.g. お) must not merge."""
        prefix = _make_token("お", "接頭辞", pos2="*", lemma="お")
        root = _make_token("金", "名詞", pos2="普通名詞", lemma="金")
        result = service._merge_compound_suffixes([prefix, root])
        # Both pass through; お is dropped later by allowed_pos filter, not here.
        assert len(result) == 2
        assert result[0] is prefix
        assert result[1] is root

    def test_prefix_followed_by_verb_not_merged(self, service):
        """Whitelisted prefix followed by a 動詞 (not 名詞/形状詞) must not merge."""
        prefix = _make_token("不", "接頭辞", pos2="*", lemma="不")
        verb = _make_token("食べる", "動詞", pos2="一般", lemma="食べる")
        result = service._merge_compound_suffixes([prefix, verb])
        assert len(result) == 2
        assert result[0] is prefix
        assert result[1] is verb

    def test_prefix_at_line_end_not_merged(self, service):
        """A trailing 接頭辞 with no following root must pass through."""
        prefix = _make_token("不", "接頭辞", pos2="*", lemma="不")
        result = service._merge_compound_suffixes([prefix])
        assert len(result) == 1
        assert result[0] is prefix

    def test_prefix_chain_into_noun_suffix(self, service):
        """接頭辞 + 名詞 + 接尾辞(名詞的) chain: prefix-merge then noun-suffix-merge.

        Empirically 可能 is 形状詞 in unidic, but the prefix-merge synthetic
        always emits pos1=名詞 so the suffix-chain can fire. Tested here with
        a 名詞 root (関心) for clarity; the 形状詞 case is covered separately.
        """
        prefix = _make_token("不", "接頭辞", pos2="*", lemma="不")
        root = _make_token("関心", "名詞", pos2="普通名詞", lemma="関心")
        suffix = _make_token("性", "接尾辞", pos2="名詞的", lemma="性")
        result = service._merge_compound_suffixes([prefix, root, suffix])
        assert len(result) == 1
        merged = result[0]
        assert merged.surface == "不関心性"
        assert merged.feature.lemma == "不関心性"
        assert merged.feature.pos1 == "名詞"

    def test_prefix_chain_with_keijoushi_root_into_noun_suffix(self, service):
        """不 + 可能(形状詞) + 性 → 不可能性 (chains because synthetic emits pos1=名詞)."""
        prefix = _make_token("不", "接頭辞", pos2="*", lemma="不")
        root = _make_token("可能", "形状詞", pos2="一般", lemma="可能")
        suffix = _make_token("性", "接尾辞", pos2="名詞的", lemma="性")
        result = service._merge_compound_suffixes([prefix, root, suffix])
        assert len(result) == 1
        merged = result[0]
        assert merged.surface == "不可能性"
        assert merged.feature.lemma == "不可能性"
        assert merged.feature.pos1 == "名詞"


class TestVerbNominalizers:
    """Tests for _merge_verb_nominalizers — 動詞(連用形) + nominalizer suffix."""

    @pytest.fixture
    def service(self, test_config):
        with patch("anki_miner.services.subtitle_parser.fugashi.Tagger"):
            return SubtitleParserService(test_config)

    @pytest.mark.parametrize(
        "verb_surface,verb_lemma,suffix_surface,expected",
        [
            ("言い", "言う", "方", "言い方"),
            ("読み", "読む", "方", "読み方"),
            ("生き", "生きる", "方", "生き方"),
            ("やり", "遣る", "方", "やり方"),
        ],
    )
    def test_merges_verb_stem_plus_nominalizer(
        self, service, verb_surface, verb_lemma, suffix_surface, expected
    ):
        """Verb 連用形 + nominalizer (方/手/様) → synthetic with surface=lemma=連用形+suffix."""
        verb = _make_token(verb_surface, "動詞", pos2="一般", lemma=verb_lemma)
        suffix = _make_token(suffix_surface, "接尾辞", pos2="名詞的", lemma=suffix_surface)
        result = service._merge_compound_suffixes([verb, suffix])
        assert len(result) == 1
        merged = result[0]
        # CRITICAL: surface uses verb's CONJUGATED form (連用形), not lemma.
        # Lemma == surface for the merged form (NOT 言う方).
        assert merged.surface == expected
        assert merged.feature.lemma == expected
        assert merged.feature.pos1 == "名詞"
        assert merged.feature.pos2 == "普通名詞"

    def test_non_whitelisted_verb_suffix_not_merged(self, service):
        """Verb + 接尾辞(名詞的) where suffix not in _VERB_NOMINALIZER_SUFFIXES is not merged.

        Example: 話し + 者 — 者 is not a productive verb-stem nominalizer in
        the same way 方/手/様 are, so we don't merge it here.
        """
        verb = _make_token("話し", "動詞", pos2="一般", lemma="話す")
        suffix = _make_token("者", "接尾辞", pos2="名詞的", lemma="者")
        result = service._merge_compound_suffixes([verb, suffix])
        assert len(result) == 2
        assert result[0] is verb
        assert result[1] is suffix

    def test_verb_at_line_end_not_merged(self, service):
        """A 動詞 with no following suffix must pass through unchanged."""
        verb = _make_token("言い", "動詞", pos2="一般", lemma="言う")
        result = service._merge_compound_suffixes([verb])
        assert len(result) == 1
        assert result[0] is verb

    def test_verb_plus_non_nominal_suffix_not_merged(self, service):
        """動詞 + 接尾辞(動詞的) (e.g. する) is not a nominalizer — no merge here."""
        verb = _make_token("勉強し", "動詞", pos2="一般", lemma="勉強する")
        suffix = _make_token("する", "接尾辞", pos2="動詞的", lemma="する")
        result = service._merge_compound_suffixes([verb, suffix])
        assert len(result) == 2
        assert result[0] is verb
        assert result[1] is suffix


def _fugashi_available() -> bool:
    try:
        import fugashi  # noqa: F401
        import unidic_lite  # noqa: F401
    except ImportError:
        return False
    return True


@pytest.mark.skipif(not _fugashi_available(), reason="fugashi/unidic-lite not installed")
def test_real_fugashi_mines_target_words(tmp_path):
    """Real fugashi pipeline must mine the FMA-style targets after the fixes."""
    srt_file = tmp_path / "fma_ep1.srt"
    srt_file.write_text(
        "1\n" "00:00:01,000 --> 00:00:05,000\n" "彼は刑務所で爆発的な事件を起こし、死傷者が出た\n",
        encoding="utf-8",
    )

    config = AnkiMinerConfig(media_temp_folder=tmp_path / "media")
    service = SubtitleParserService(config)
    words = service.parse_subtitle_file(srt_file)

    surfaces = {w.surface for w in words}
    expected = {"彼", "刑務所", "爆発的", "事件", "死傷者"}
    missing = expected - surfaces
    assert not missing, f"missing target surfaces: {missing}; got: {surfaces}"

    # Verify the 刑務所 merged synthetic carries the correct lemma and reading.
    by_surface = {w.surface: w for w in words}
    keimusho = by_surface["刑務所"]
    assert keimusho.lemma == "刑務所"
    # unidic emits katakana readings; concatenated stems give ケイムショ.
    assert keimusho.reading == "ケイムショ"


@pytest.mark.skipif(not _fugashi_available(), reason="fugashi/unidic-lite not installed")
def test_real_fugashi_mines_prefix_compound(tmp_path):
    """Real fugashi pipeline must mine 不可能 from 不+可能 prefix-merge."""
    srt_file = tmp_path / "prefix.srt"
    srt_file.write_text(
        "1\n" "00:00:01,000 --> 00:00:05,000\n" "不可能な事を諦めた\n",
        encoding="utf-8",
    )

    config = AnkiMinerConfig(media_temp_folder=tmp_path / "media")
    service = SubtitleParserService(config)
    words = service.parse_subtitle_file(srt_file)

    surfaces = {w.surface for w in words}
    assert "不可能" in surfaces, f"expected 不可能 in mined surfaces; got: {surfaces}"


@pytest.mark.skipif(not _fugashi_available(), reason="fugashi/unidic-lite not installed")
def test_real_fugashi_mines_verb_nominalizer(tmp_path):
    """Real fugashi pipeline must mine 生き方 from 生き(動詞) + 方(接尾辞,名詞的)."""
    srt_file = tmp_path / "verb_nom.srt"
    srt_file.write_text(
        "1\n" "00:00:01,000 --> 00:00:05,000\n" "生き方を考える\n",
        encoding="utf-8",
    )

    config = AnkiMinerConfig(media_temp_folder=tmp_path / "media")
    service = SubtitleParserService(config)
    words = service.parse_subtitle_file(srt_file)

    surfaces = {w.surface for w in words}
    assert "生き方" in surfaces, f"expected 生き方 in mined surfaces; got: {surfaces}"
    # Lemma must be the merged surface, NOT 生きる方.
    by_surface = {w.surface: w for w in words}
    assert by_surface["生き方"].lemma == "生き方"
