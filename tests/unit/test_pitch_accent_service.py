"""Tests for PitchAccentService."""

import pytest

from anki_miner.exceptions import SetupError
from anki_miner.services.pitch_accent_service import (
    PitchAccentService,
    PitchEntry,
    classify_pitch,
    count_mora,
    downstep_positions,
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
    """NHK-convention pitch category truth table.

    Standard convention (see Yomitan getPitchCategory): after heiban, *any*
    downstep on a verb/adjective is 起伏 (kifuku); 頭高/中高/尾高 apply to
    nominals only. Cross-checked against the ``'heiban,kifuku'`` rows in
    Yomitan's ``test/data/anki-note-builder-test-results.json`` sanity anchor.
    """

    # --- Nominals: the four positional categories -------------------------

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

    def test_nominal_categories_explicit_noun_pos(self):
        # An explicit 名詞 POS never triggers 起伏 — positional rules apply.
        assert classify_pitch(0, 3, pos="名詞") == "平板"
        assert classify_pitch(1, 3, pos="名詞") == "頭高"
        assert classify_pitch(2, 3, pos="名詞") == "中高"
        assert classify_pitch(3, 3, pos="名詞") == "尾高"  # odaka

    # --- Verbs / adjectives: any downstep → 起伏 --------------------------

    def test_kifuku_verb_final_mora(self):
        # 動詞 with drop on last mora → 起伏 (kifuku)
        assert classify_pitch(3, 3, pos="動詞") == "起伏"

    def test_kifuku_verb_medial_downstep(self):
        # 食べる[2] of 3 morae: a medial drop on a verb is 起伏, NOT 中高.
        # This is the mislabel the fix corrects.
        assert classify_pitch(2, 3, pos="動詞") == "起伏"

    def test_kifuku_verb_head_downstep(self):
        # A head drop on a verb is 起伏, NOT 頭高.
        assert classify_pitch(1, 3, pos="動詞") == "起伏"

    def test_kifuku_i_adjective_final(self):
        # 形容詞 with drop on last mora → 起伏 (kifuku)
        assert classify_pitch(2, 2, pos="形容詞") == "起伏"

    def test_kifuku_i_adjective_head(self):
        # 高い[2] etc.: any accented i-adjective is 起伏.
        assert classify_pitch(1, 2, pos="形容詞") == "起伏"

    def test_verb_heiban_stays_heiban(self):
        # Unaccented verbs are still 平板 — only downsteps become 起伏.
        assert classify_pitch(0, 3, pos="動詞") == "平板"
        assert classify_pitch(0, 2, pos="形容詞") == "平板"


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
        # entry_count is now distinct (surface, reading) pairs, not doubled by
        # separate kanji/reading keys (service was re-keyed for reading-scoping).
        assert service.entry_count == 2

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

    def test_missing_reading_kanji_word_declines_category(self, tmp_path):
        """Empty reading + kanji surface must NOT feed the kanji into count_mora.

        count_mora("学校")==2 (not 4), so position 2 would mislabel as 尾高.
        The pattern is still returned; the category is declined (None)."""
        csv_file = tmp_path / "pitch.csv"
        csv_file.write_text("がっこう,学校,2\n", encoding="utf-8")
        service = PitchAccentService(csv_file)
        service.load()

        pos, cat = service.lookup_detailed("学校", "")
        assert pos == "2"
        assert cat is None

    def test_missing_reading_kana_word_still_categorized(self, tmp_path):
        """Empty reading + an all-kana surface may safely fall back to the surface
        for mora counting (4 mora → position 2 → 中高)."""
        csv_file = tmp_path / "pitch.csv"
        csv_file.write_text("がっこう,学校,2\n", encoding="utf-8")
        service = PitchAccentService(csv_file)
        service.load()

        pos, cat = service.lookup_detailed("がっこう", "")
        assert pos == "2"
        assert cat == "中高"

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
            "たべる,食べる,2\n"  # 動詞, 3 mora, medial drop → 起伏/kifuku
            "はしる,走る,3\n",  # 動詞, 3 mora, final drop → 起伏/kifuku
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
        # Both are accented verbs → kifuku (NHK convention); 食べる[2] is no
        # longer mislabeled nakadaka.
        assert results == [("2", "kifuku"), ("3", "kifuku")]


class TestDownstepPositions:
    """Port of Yomitan getDownstepPositions — H/L mora string → downstep index."""

    def test_single_downstep(self):
        # LHL: H→L at index 2.
        assert downstep_positions("LHL") == [2]

    def test_atamadaka_hl(self):
        # HLLL: H→L at index 1.
        assert downstep_positions("HLLL") == [1]

    def test_heiban_starts_low_no_downstep(self):
        # LHHH: no H→L transition, starts Low → heiban (position 0).
        assert downstep_positions("LHHH") == [0]

    def test_all_high_unresolvable(self):
        # HHHH: no downstep and does not start Low → -1 (no resolvable downstep).
        assert downstep_positions("HHHH") == [-1]

    def test_multiple_downsteps(self):
        # LHLHL: H→L at index 2 and index 4.
        assert downstep_positions("LHLHL") == [2, 4]


class TestReadingScopedLookup:
    """Homographs resolve by the reading passed in, not load order (弾く)."""

    @pytest.fixture
    def homograph_service(self, tmp_path):
        csv_file = tmp_path / "pitch.csv"
        # 弾く: ひく[0] loaded FIRST, はじく[2] second. Old code returned whichever
        # loaded first for any reading; the fix scopes by reading.
        csv_file.write_text("ひく,弾く,0\nはじく,弾く,2\n", encoding="utf-8")
        service = PitchAccentService(csv_file)
        service.load()
        return service

    def test_hiku_reading(self, homograph_service):
        assert homograph_service.lookup("弾く", "ひく") == "0"

    def test_hajiku_reading_not_collided(self, homograph_service):
        # The bug: this used to return "0" (ひく) because kanji matched first.
        assert homograph_service.lookup("弾く", "はじく") == "2"

    def test_both_entries_kept(self, homograph_service):
        assert homograph_service.entry_count == 2

    def test_unknown_reading_does_not_guess(self, homograph_service):
        # Multiple candidates + a reading matching none exactly → no guess.
        assert homograph_service.lookup("弾く", "へんな") is None

    def test_no_reading_falls_back_first_wins(self, homograph_service):
        # With nothing to disambiguate, legacy first-wins behavior applies.
        assert homograph_service.lookup("弾く") == "0"

    def test_single_candidate_kana_variant_fallback(self, tmp_path):
        # Only one entry for the surface → pragmatic fallback even if the reading
        # is a variant that doesn't match exactly.
        csv_file = tmp_path / "pitch.csv"
        csv_file.write_text("たべる,食べる,0\n", encoding="utf-8")
        service = PitchAccentService(csv_file)
        service.load()
        assert service.lookup("食べる", "たべ") == "0"


class TestFiveColumnCsv:
    """The enriched 5-column CSV (reading,kanji,pattern,nasal,devoice)."""

    def test_five_column_round_trip(self, tmp_path):
        csv_file = tmp_path / "pitch.csv"
        # csv.writer would quote "1,3"; write it quoted here to mirror that.
        csv_file.write_text(
            'reading,kanji,pattern,nasal,devoice\nほんばこ,本箱,3,"1,3",2\n',
            encoding="utf-8",
        )
        service = PitchAccentService(csv_file)
        service.load()
        entry = service.lookup_entry("本箱", "ほんばこ")
        assert entry == PitchEntry(pattern="3", nasal=(1, 3), devoice=(2,))

    def test_five_column_empty_nasal_devoice(self, tmp_path):
        csv_file = tmp_path / "pitch.csv"
        csv_file.write_text(
            "reading,kanji,pattern,nasal,devoice\nねこ,猫,1,,\n",
            encoding="utf-8",
        )
        service = PitchAccentService(csv_file)
        service.load()
        entry = service.lookup_entry("猫", "ねこ")
        assert entry == PitchEntry(pattern="1", nasal=(), devoice=())

    def test_hl_string_pattern_categorized(self, tmp_path):
        csv_file = tmp_path / "pitch.csv"
        csv_file.write_text(
            "reading,kanji,pattern,nasal,devoice\nはし,箸,LHL,,\n",
            encoding="utf-8",
        )
        service = PitchAccentService(csv_file)
        service.load()
        pos, cat = service.lookup_detailed("箸", "はし")
        assert pos == "LHL"
        assert cat == "尾高"  # LHL → downstep 2; はし 2 mora → odaka


class TestLegacyCompatibility:
    """Legacy 3-column files (headerless / headered / comma-delimited) keep working."""

    def test_headerless_three_column(self, tmp_path):
        csv_file = tmp_path / "pitch.csv"
        csv_file.write_text("たべる,食べる,0\nのむ,飲む,1\n", encoding="utf-8")
        service = PitchAccentService(csv_file)
        service.load()
        assert service.lookup("食べる") == "0"
        assert service.lookup("飲む") == "1"
        # Legacy rows have no nasal/devoice.
        assert service.lookup_entry("食べる", "たべる") == PitchEntry("0", (), ())

    def test_headered_three_column(self, tmp_path):
        csv_file = tmp_path / "pitch.csv"
        csv_file.write_text(
            "reading,kanji,pattern\nたべる,食べる,0\nのむ,飲む,1\n",
            encoding="utf-8",
        )
        service = PitchAccentService(csv_file)
        service.load()
        assert service.lookup("食べる") == "0"
        assert service.lookup("reading") is None

    def test_comma_delimited_legacy_multi_position_pattern(self, tmp_path):
        # A HAND-EDITED legacy comma file whose pattern is "0,2" splits into 4 raw
        # fields. It must be treated as legacy (not the 5-col format) and the
        # pattern tail rejoined so it reads back as "0,2".
        csv_file = tmp_path / "pitch.csv"
        csv_file.write_text("いちがつ,１月,0,2\n", encoding="utf-8")
        service = PitchAccentService(csv_file)
        service.load()
        entry = service.lookup_entry("１月", "いちがつ")
        assert entry is not None
        assert entry.pattern == "0,2"
        # The trailing "2" must NOT be misread as a nasal/devoice column.
        assert entry.nasal == ()
        assert entry.devoice == ()

    def test_anomalous_six_field_treated_as_legacy(self, tmp_path):
        # >= 6 fields (e.g. a hand-edited pattern with 4 positions) → legacy,
        # tail-rejoined; nasal/devoice stay empty.
        csv_file = tmp_path / "pitch.csv"
        csv_file.write_text("よん,四,0,1,2,3\n", encoding="utf-8")
        service = PitchAccentService(csv_file)
        service.load()
        entry = service.lookup_entry("四", "よん")
        assert entry is not None
        assert entry.pattern == "0,1,2,3"
        assert entry.nasal == ()
        assert entry.devoice == ()


class TestLookupEntry:
    """lookup_entry exposes PitchEntry fidelity (nasal/devoice) for downstream render."""

    def test_returns_none_when_not_loaded(self, tmp_path):
        service = PitchAccentService(tmp_path / "pitch.csv")
        assert service.lookup_entry("食べる") is None

    def test_returns_none_for_unknown(self, tmp_path):
        csv_file = tmp_path / "pitch.csv"
        csv_file.write_text("たべる,食べる,0\n", encoding="utf-8")
        service = PitchAccentService(csv_file)
        service.load()
        assert service.lookup_entry("存在しない", "ぞんざい") is None


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
