"""Tests for PitchAccentService."""

import pytest

from anki_miner.exceptions import SetupError
from anki_miner.services.pitch_accent_service import (
    PitchAccentService,
    classify_pitch,
    count_mora,
)


class TestCountMora:
    """Tests for mora counting."""

    def test_simple_hiragana(self):
        assert count_mora("たべる") == 3

    def test_with_combining_kana(self):
        # きょう = き + ょ + う = 2 mora (ょ combines with き)
        assert count_mora("きょう") == 2

    def test_with_small_tsu(self):
        # がっこう = が + っ + こ + う = 4 mora (っ counts as 1)
        assert count_mora("がっこう") == 4

    def test_with_long_vowel(self):
        # コーヒー = コ + ー + ヒ + ー = 4 mora
        assert count_mora("コーヒー") == 4

    def test_single_kana(self):
        assert count_mora("あ") == 1

    def test_katakana_combining(self):
        # シャ = シ + ャ = 1 mora
        assert count_mora("シャ") == 1

    def test_empty_string(self):
        assert count_mora("") == 0


class TestClassifyPitch:
    """Tests for pitch category classification."""

    def test_heiban(self):
        assert classify_pitch(0, 3) == "平板"

    def test_atamadaka(self):
        assert classify_pitch(1, 3) == "頭高"

    def test_odaka(self):
        # position == mora count → odaka
        assert classify_pitch(3, 3) == "尾高"

    def test_nakadaka(self):
        assert classify_pitch(2, 3) == "中高"

    def test_atamadaka_two_mora(self):
        assert classify_pitch(1, 2) == "頭高"

    def test_odaka_two_mora(self):
        assert classify_pitch(2, 2) == "尾高"

    def test_kifuku_verb(self):
        # 動詞 with drop on last mora → 起伏 (kifuku)
        assert classify_pitch(3, 3, pos="動詞") == "起伏"

    def test_kifuku_i_adjective(self):
        # 形容詞 with drop on last mora → 起伏 (kifuku)
        assert classify_pitch(2, 2, pos="形容詞") == "起伏"

    def test_odaka_noun_explicit(self):
        # 名詞 with drop on last mora → 尾高 (odaka)
        assert classify_pitch(3, 3, pos="名詞") == "尾高"

    def test_verbal_pos_only_affects_final_mora(self):
        # Non-final drop position keeps the standard category regardless of POS
        assert classify_pitch(1, 3, pos="動詞") == "頭高"
        assert classify_pitch(2, 3, pos="動詞") == "中高"
        assert classify_pitch(0, 3, pos="動詞") == "平板"


class TestLoad:
    """Tests for loading pitch accent data."""

    def test_loads_valid_csv(self, tmp_path):
        """Test loading a valid Kanjium-format CSV."""
        csv_file = tmp_path / "pitch.csv"
        csv_file.write_text(
            "たべる,食べる,0\n" "のむ,飲む,1\n" "みる,見る,1\n",
            encoding="utf-8",
        )

        service = PitchAccentService(csv_file)
        assert service.load() is True
        assert service.is_available() is True

    def test_raises_setup_error_when_file_missing(self, tmp_path):
        """Test that SetupError is raised when file is missing."""
        service = PitchAccentService(tmp_path / "nonexistent.csv")
        with pytest.raises(SetupError):
            service.load()

    def test_handles_malformed_rows_gracefully(self, tmp_path):
        """Test that rows with fewer than 3 columns are skipped."""
        csv_file = tmp_path / "pitch.csv"
        csv_file.write_text(
            "たべる,食べる,0\n" "incomplete\n" "also,incomplete\n" "のむ,飲む,1\n",
            encoding="utf-8",
        )

        service = PitchAccentService(csv_file)
        service.load()
        assert service.lookup("食べる") == "0"
        assert service.lookup("飲む") == "1"

    def test_loads_tsv_format(self, tmp_path):
        """Test loading tab-separated pitch accent data."""
        tsv_file = tmp_path / "pitch.txt"
        tsv_file.write_text(
            "たべる\t食べる\t0\n" "のむ\t飲む\t1\n",
            encoding="utf-8",
        )

        service = PitchAccentService(tsv_file)
        service.load()
        assert service.lookup("食べる") == "0"
        assert service.lookup("飲む") == "1"

    def test_skips_header_row(self, tmp_path):
        """Test that a header row is automatically skipped."""
        csv_file = tmp_path / "pitch.csv"
        csv_file.write_text(
            "reading,kanji,frequency\n" "たべる,食べる,0\n" "のむ,飲む,1\n",
            encoding="utf-8",
        )

        service = PitchAccentService(csv_file)
        service.load()
        assert service.lookup("食べる") == "0"
        assert service.lookup("reading") is None

    def test_skips_header_row_tsv(self, tmp_path):
        """Test that a header row is skipped in TSV files."""
        tsv_file = tmp_path / "pitch.txt"
        tsv_file.write_text(
            "kana\tkanji\trank\n" "たべる\t食べる\t0\n" "のむ\t飲む\t1\n",
            encoding="utf-8",
        )

        service = PitchAccentService(tsv_file)
        service.load()
        assert service.lookup("食べる") == "0"
        assert service.lookup("kana") is None

    def test_loads_kanjium_column_order(self, tmp_path):
        """Test loading Kanjium format where columns are kanji, reading, pattern (swapped)."""
        tsv_file = tmp_path / "accents.txt"
        tsv_file.write_text(
            "食べる\tたべる\t0\n" "飲む\tのむ\t1\n",
            encoding="utf-8",
        )

        service = PitchAccentService(tsv_file)
        service.load()
        # Both kanji and reading stored as keys, so lookup works either way
        assert service.lookup("食べる") == "0"
        assert service.lookup("たべる") == "0"
        assert service.lookup("飲む") == "1"
        assert service.lookup("のむ") == "1"

    def test_entry_count_property(self, tmp_path):
        """Test that entry_count reflects number of loaded entries."""
        csv_file = tmp_path / "pitch.csv"
        csv_file.write_text(
            "たべる,食べる,0\nのむ,飲む,1\n",
            encoding="utf-8",
        )

        service = PitchAccentService(csv_file)
        assert service.entry_count == 0
        service.load()
        assert service.entry_count == 4  # 2 kanji + 2 reading entries

    def test_first_entry_wins_on_duplicate_key(self, tmp_path):
        """Test that the first entry wins when keys are duplicated."""
        csv_file = tmp_path / "pitch.csv"
        csv_file.write_text(
            "たべる,食べる,0\n" "たべる,食べる,2\n",
            encoding="utf-8",
        )

        service = PitchAccentService(csv_file)
        service.load()
        assert service.lookup("食べる") == "0"

    def test_empty_csv_file(self, tmp_path):
        """Test that an empty CSV file loads with zero entries."""
        csv_file = tmp_path / "pitch.csv"
        csv_file.write_text("", encoding="utf-8")

        service = PitchAccentService(csv_file)
        assert service.load() is True
        assert service.is_available() is True
        assert service.lookup("食べる") is None

    def test_generic_exception_raises_setup_error(self, tmp_path):
        """Test that a generic error during load raises SetupError."""
        csv_file = tmp_path / "pitch.csv"
        csv_file.write_bytes(b"\x80\x81\x82\xff\xfe")  # Invalid UTF-8

        service = PitchAccentService(csv_file)
        with pytest.raises(SetupError, match="Error loading pitch accent data"):
            service.load()


class TestLookup:
    """Tests for pitch accent lookup."""

    @pytest.fixture
    def loaded_service(self, tmp_path):
        """Create a loaded PitchAccentService."""
        csv_file = tmp_path / "pitch.csv"
        csv_file.write_text(
            "たべる,食べる,0\n" "のむ,飲む,1\n" "はしる,走る,2\n",
            encoding="utf-8",
        )
        service = PitchAccentService(csv_file)
        service.load()
        return service

    def test_returns_pattern_for_known_word(self, loaded_service):
        """Test lookup returns pattern for a known kanji word."""
        assert loaded_service.lookup("食べる") == "0"
        assert loaded_service.lookup("飲む") == "1"

    def test_returns_none_for_unknown_word(self, loaded_service):
        """Test lookup returns None for an unknown word."""
        assert loaded_service.lookup("存在しない") is None

    def test_falls_back_to_reading_when_kanji_not_found(self, tmp_path):
        """Test lookup falls back to reading when kanji is not found."""
        csv_file = tmp_path / "pitch.csv"
        csv_file.write_text(
            "たべる,食べる,0\n",
            encoding="utf-8",
        )
        service = PitchAccentService(csv_file)
        service.load()
        # Look up a word that doesn't exist, with a reading that does
        assert service.lookup("不明", reading="たべる") == "0"

    def test_returns_none_when_not_loaded(self, tmp_path):
        """Test lookup returns None when data hasn't been loaded."""
        service = PitchAccentService(tmp_path / "pitch.csv")
        assert service.lookup("食べる") is None


class TestLookupDetailed:
    """Tests for detailed lookup returning position + category."""

    @pytest.fixture
    def loaded_service(self, tmp_path):
        """Create a loaded PitchAccentService with known entries."""
        csv_file = tmp_path / "pitch.csv"
        csv_file.write_text(
            "たべる,食べる,0\n"  # 3 mora, position 0 → 平板
            "のむ,飲む,1\n"  # 2 mora, position 1 → 頭高
            "はしる,走る,2\n"  # 3 mora, position 2 → 中高 (not 3)
            "おとこ,男,3\n",  # 3 mora, position 3 → 尾高
            encoding="utf-8",
        )
        service = PitchAccentService(csv_file)
        service.load()
        return service

    def test_heiban(self, loaded_service):
        pos, cat = loaded_service.lookup_detailed("食べる", "たべる")
        assert pos == "0"
        assert cat == "平板"

    def test_atamadaka(self, loaded_service):
        pos, cat = loaded_service.lookup_detailed("飲む", "のむ")
        assert pos == "1"
        assert cat == "頭高"

    def test_nakadaka(self, loaded_service):
        pos, cat = loaded_service.lookup_detailed("走る", "はしる")
        assert pos == "2"
        assert cat == "中高"

    def test_odaka(self, loaded_service):
        pos, cat = loaded_service.lookup_detailed("男", "おとこ")
        assert pos == "3"
        assert cat == "尾高"

    def test_not_found(self, loaded_service):
        pos, cat = loaded_service.lookup_detailed("不明", "ふめい")
        assert pos is None
        assert cat is None

    def test_multi_pattern_emits_all_categories(self, tmp_path):
        # Use TSV to preserve comma in pattern (like real Kanjium file)
        tsv_file = tmp_path / "pitch.tsv"
        tsv_file.write_text("いちがつ\t１月\t4,0\n", encoding="utf-8")
        service = PitchAccentService(tsv_file)
        service.load()

        position, cat = service.lookup_detailed("１月", "いちがつ")
        assert position == "4,0"
        # いちがつ = 4 mora → 4 = 尾高, 0 = 平板
        assert cat == "尾高,平板"

    def test_multi_pattern_romaji(self, tmp_path):
        tsv_file = tmp_path / "pitch.tsv"
        tsv_file.write_text("いちがつ\t１月\t4,0\n", encoding="utf-8")
        service = PitchAccentService(tsv_file)
        service.load()

        position, cat = service.lookup_detailed("１月", "いちがつ", fmt="romaji")
        assert position == "4,0"
        assert cat == "odaka,heiban"

    def test_romaji_format_basic_categories(self, tmp_path):
        csv_file = tmp_path / "pitch.csv"
        csv_file.write_text(
            "たべる,食べる,0\n"  # heiban
            "おとこ,男,3\n"  # odaka (3 mora, pos=3, noun)
            "のむ,飲む,1\n",  # atamadaka
            encoding="utf-8",
        )
        service = PitchAccentService(csv_file)
        service.load()

        _, heiban = service.lookup_detailed("食べる", "たべる", fmt="romaji")
        _, odaka = service.lookup_detailed("男", "おとこ", pos="名詞", fmt="romaji")
        _, atamadaka = service.lookup_detailed("飲む", "のむ", fmt="romaji")
        assert heiban == "heiban"
        assert odaka == "odaka"
        assert atamadaka == "atamadaka"

    def test_kifuku_romaji_for_verb(self, tmp_path):
        # 走る is a 動詞 with drop at mora_count (3) → kifuku in romaji
        csv_file = tmp_path / "pitch.csv"
        csv_file.write_text("はしる,走る,3\n", encoding="utf-8")
        service = PitchAccentService(csv_file)
        service.load()

        _, cat = service.lookup_detailed("走る", "はしる", pos="動詞", fmt="romaji")
        assert cat == "kifuku"

    def test_batch_detailed_legacy_two_tuple(self, loaded_service):
        # Legacy callers passing 2-tuples (no pos) still work.
        results = loaded_service.lookup_batch_detailed(
            [
                ("食べる", "たべる"),
                ("unknown", ""),
                ("飲む", "のむ"),
            ]
        )
        assert results == [("0", "平板"), (None, None), ("1", "頭高")]

    def test_batch_detailed_with_pos_and_romaji(self, tmp_path):
        csv_file = tmp_path / "pitch.csv"
        csv_file.write_text(
            "たべる,食べる,2\n"  # 動詞, 3 mora → 中高/nakadaka
            "はしる,走る,3\n",  # 動詞, 3 mora → 起伏/kifuku
            encoding="utf-8",
        )
        service = PitchAccentService(csv_file)
        service.load()

        results = service.lookup_batch_detailed(
            [
                ("食べる", "たべる", "動詞"),
                ("走る", "はしる", "動詞"),
            ],
            fmt="romaji",
        )
        assert results == [("2", "nakadaka"), ("3", "kifuku")]


class TestIsAvailable:
    """Tests for is_available."""

    def test_false_before_load(self, tmp_path):
        """Test is_available returns False before loading."""
        service = PitchAccentService(tmp_path / "pitch.csv")
        assert service.is_available() is False

    def test_true_after_load(self, tmp_path):
        """Test is_available returns True after successful loading."""
        csv_file = tmp_path / "pitch.csv"
        csv_file.write_text("たべる,食べる,0\n", encoding="utf-8")
        service = PitchAccentService(csv_file)
        service.load()
        assert service.is_available() is True
