"""Tests for word_filter service."""

import pytest

from anki_miner.models import LineLemmas
from anki_miner.models.word import TokenizedWord
from anki_miner.services.word_filter import WordFilterService
from anki_miner.services.word_list_service import WordListService


def create_word(
    lemma: str,
    surface: str = None,
    sentence: str = "Test sentence",
    pos: str | None = None,
) -> TokenizedWord:
    """Helper to create a TokenizedWord for testing."""
    return TokenizedWord(
        surface=surface or lemma,
        lemma=lemma,
        reading="",
        sentence=sentence,
        start_time=0.0,
        end_time=1.0,
        duration=1.0,
        pos=pos,
    )


class TestWordFilterService:
    """Tests for WordFilterService."""

    @pytest.fixture
    def service(self, test_config):
        """Create a WordFilterService instance."""
        return WordFilterService(test_config)

    class TestFilterUnknown:
        """Tests for filter_unknown method."""

        def test_filters_known_lemmas(self, test_config):
            """Should filter out words with known lemmas."""
            service = WordFilterService(test_config)
            words = [
                create_word("知る"),
                create_word("食べる"),
                create_word("新しい"),
            ]
            existing = {"知る", "食べる"}

            result = service.filter_unknown(words, existing)

            assert len(result) == 1
            assert result[0].lemma == "新しい"

        def test_does_not_filter_by_surface_form(self, test_config):
            """Legacy verb surface-form cards do not block re-mining as lemma.

            ``filter_unknown`` compares by ``mined_form`` (lemma for verbs).
            A legacy Anki card with Expression == 知った matches the surface
            string 知った, not the verb's lemma 知る; mining the verb under
            its dictionary form is allowed until that lemma itself enters
            the collection.
            """
            service = WordFilterService(test_config)
            words = [
                create_word("知る", "知った", pos="動詞"),
                create_word("食べる", "食べた", pos="動詞"),
            ]
            existing = {"知った"}  # legacy surface-form card

            result = service.filter_unknown(words, existing)

            # Both pass: mined_form for verbs == lemma, neither in `existing`.
            assert len(result) == 2
            assert {w.lemma for w in result} == {"知る", "食べる"}

        def test_filters_noun_when_lemma_differs_from_surface(self, test_config):
            """Regression: noun whose unidic lemma differs from its surface must
            be blocked when its mined_form (the surface) already exists in Anki.

            unidic-lite maps the noun surface 豪腕 to lemma 剛腕 (homograph
            quirk; Issue #5). The card's Expression field is mined_form
            (surface for nouns), so existing_vocabulary contains 豪腕, and
            filtering on lemma alone would let a duplicate through and
            trigger an AnkiConnect duplicate error at addNotes time.
            """
            service = WordFilterService(test_config)
            words = [create_word(lemma="剛腕", surface="豪腕", pos="名詞")]
            existing = {"豪腕"}

            result = service.filter_unknown(words, existing)

            assert result == []

        def test_empty_existing_vocabulary(self, test_config):
            """Should return all words when existing vocabulary is empty."""
            service = WordFilterService(test_config)
            words = [
                create_word("知る"),
                create_word("食べる"),
            ]

            result = service.filter_unknown(words, set())

            assert len(result) == 2

        def test_empty_words_list(self, test_config):
            """Should return empty list when no words provided."""
            service = WordFilterService(test_config)

            result = service.filter_unknown([], {"知る"})

            assert result == []

    class TestFilterByFrequency:
        """Tests for filter_by_frequency method."""

        def _word_with_freq(self, lemma, rank):
            """Helper to create a word with a frequency rank."""
            word = create_word(lemma)
            word.frequency_rank = rank
            return word

        def test_keeps_words_within_rank(self, test_config):
            """Should keep words within the max frequency rank."""
            service = WordFilterService(test_config)
            words = [
                self._word_with_freq("の", 1),
                self._word_with_freq("食べる", 500),
                self._word_with_freq("飲む", 1000),
            ]

            result = service.filter_by_frequency(words, max_rank=1000)
            assert len(result) == 3

        def test_removes_words_above_rank(self, test_config):
            """Should remove words ranked above the threshold."""
            service = WordFilterService(test_config)
            words = [
                self._word_with_freq("の", 1),
                self._word_with_freq("食べる", 500),
                self._word_with_freq("稀な単語", 50000),
            ]

            result = service.filter_by_frequency(words, max_rank=10000)
            assert len(result) == 2
            assert all(w.frequency_rank <= 10000 for w in result)

        def test_removes_words_with_no_rank_data(self, test_config):
            """Words without frequency data should be excluded when a cutoff is active (Issue #34)."""
            service = WordFilterService(test_config)
            words = [
                self._word_with_freq("の", 1),
                create_word("不明"),  # No frequency rank (None)
            ]

            result = service.filter_by_frequency(words, max_rank=5000)
            assert len(result) == 1
            assert result[0].lemma == "の"

        def test_no_filtering_keeps_unranked_words(self, test_config):
            """max_rank=0 disables filtering entirely; unranked words pass through."""
            service = WordFilterService(test_config)
            words = [self._word_with_freq("の", 1), create_word("不明")]
            assert len(service.filter_by_frequency(words, max_rank=0)) == 2

        def test_no_filtering_when_max_rank_zero(self, test_config):
            """Should return all words when max_rank is 0."""
            service = WordFilterService(test_config)
            words = [
                self._word_with_freq("の", 1),
                self._word_with_freq("稀", 99999),
            ]

            result = service.filter_by_frequency(words, max_rank=0)
            assert len(result) == 2

        def test_no_filtering_when_max_rank_none(self, test_config):
            """Should return all words when max_rank is None."""
            service = WordFilterService(test_config)
            words = [
                self._word_with_freq("の", 1),
                self._word_with_freq("稀", 99999),
            ]

            result = service.filter_by_frequency(words, max_rank=None)
            assert len(result) == 2

        def test_empty_list(self, test_config):
            """Should return empty list when no words provided."""
            service = WordFilterService(test_config)
            result = service.filter_by_frequency([], max_rank=5000)
            assert result == []

    class TestFilterByWordLists:
        """Tests for filter_by_word_lists method."""

        def test_removes_blacklisted_words(self, test_config, tmp_path):
            """Should remove words on the blacklist."""
            bl = tmp_path / "bl.txt"
            bl.write_text("食べる\n", encoding="utf-8")
            wls = WordListService(blacklist_path=bl)
            wls.load()

            service = WordFilterService(test_config)
            words = [create_word("食べる"), create_word("飲む")]

            result = service.filter_by_word_lists(words, wls)
            assert len(result) == 1
            assert result[0].lemma == "飲む"

        def test_keeps_whitelisted_words(self, test_config, tmp_path):
            """Whitelisted words should always be kept."""
            wl = tmp_path / "wl.txt"
            wl.write_text("食べる\n", encoding="utf-8")
            wls = WordListService(whitelist_path=wl)
            wls.load()

            service = WordFilterService(test_config)
            words = [create_word("食べる"), create_word("飲む")]

            result = service.filter_by_word_lists(words, wls)
            assert len(result) == 2

        def test_whitelist_overrides_blacklist(self, test_config, tmp_path):
            """If a word is on both lists, whitelist wins."""
            bl = tmp_path / "bl.txt"
            bl.write_text("食べる\n", encoding="utf-8")
            wl = tmp_path / "wl.txt"
            wl.write_text("食べる\n", encoding="utf-8")
            wls = WordListService(blacklist_path=bl, whitelist_path=wl)
            wls.load()

            service = WordFilterService(test_config)
            words = [create_word("食べる")]

            result = service.filter_by_word_lists(words, wls)
            assert len(result) == 1

        def test_empty_list(self, test_config, tmp_path):
            """Should return empty list for empty input."""
            wls = WordListService()
            wls.load()

            service = WordFilterService(test_config)
            result = service.filter_by_word_lists([], wls)
            assert result == []

    class TestFilterByScriptType:
        """Tests for filter_by_script_type method (Issue #57)."""

        def test_excludes_hiragana_only(self, test_config):
            """Hiragana-only words are dropped when the flag is set."""
            service = WordFilterService(test_config)
            words = [create_word("これ"), create_word("漢字"), create_word("コーヒー")]

            result = service.filter_by_script_type(words, exclude_hiragana_only=True)

            assert [w.lemma for w in result] == ["漢字", "コーヒー"]

        def test_excludes_katakana_only(self, test_config):
            """Katakana-only words (incl. prolonged mark) are dropped when set."""
            service = WordFilterService(test_config)
            words = [create_word("コーヒー"), create_word("漢字"), create_word("これ")]

            result = service.filter_by_script_type(words, exclude_katakana_only=True)

            assert [w.lemma for w in result] == ["漢字", "これ"]

        def test_excludes_both_scripts(self, test_config):
            """Both flags together drop all pure-kana words, keep mixed/kanji."""
            service = WordFilterService(test_config)
            words = [create_word("これ"), create_word("コーヒー"), create_word("漢字"), create_word("お茶")]

            result = service.filter_by_script_type(words, exclude_hiragana_only=True, exclude_katakana_only=True)

            assert [w.lemma for w in result] == ["漢字", "お茶"]

        def test_no_flags_is_noop(self, test_config):
            """With neither flag set, nothing is removed."""
            service = WordFilterService(test_config)
            words = [create_word("これ"), create_word("コーヒー"), create_word("漢字")]

            result = service.filter_by_script_type(words)

            assert len(result) == 3

        def test_tests_mined_form_verb_uses_lemma(self, test_config):
            """A verb is judged by its lemma (mined_form), not its surface.

            Surface ぬすんだ is hiragana-only, but the verb mines as lemma 盗む
            (has kanji) → kept under exclude_hiragana_only.
            """
            service = WordFilterService(test_config)
            words = [create_word("盗む", surface="ぬすんだ", pos="動詞")]

            result = service.filter_by_script_type(words, exclude_hiragana_only=True)

            assert len(result) == 1

        def test_tests_mined_form_noun_uses_surface(self, test_config):
            """A noun is judged by its surface (mined_form).

            Subtitle wrote 全部 as ぜんぶ; the noun mines as surface ぜんぶ
            (hiragana) → dropped under exclude_hiragana_only even though the
            lemma 全部 has kanji.
            """
            service = WordFilterService(test_config)
            words = [create_word("全部", surface="ぜんぶ", pos="名詞")]

            result = service.filter_by_script_type(words, exclude_hiragana_only=True)

            assert result == []

        def test_empty_list(self, test_config):
            """Empty input yields empty output."""
            service = WordFilterService(test_config)
            assert service.filter_by_script_type([], exclude_hiragana_only=True) == []

    class TestDeduplicateBySentence:
        """Tests for deduplicate_by_sentence method."""

        def test_removes_duplicate_sentences(self, test_config):
            """Should keep only the first word per sentence."""
            service = WordFilterService(test_config)
            words = [
                create_word("食べる", sentence="今日は良い天気です。"),
                create_word("飲む", sentence="今日は良い天気です。"),
                create_word("走る", sentence="別の文章です。"),
            ]

            result = service.deduplicate_by_sentence(words)
            assert len(result) == 2
            assert result[0].lemma == "食べる"
            assert result[1].lemma == "走る"

        def test_keeps_unique_sentences(self, test_config):
            """Should keep all words when sentences are unique."""
            service = WordFilterService(test_config)
            words = [
                create_word("食べる", sentence="文1"),
                create_word("飲む", sentence="文2"),
                create_word("走る", sentence="文3"),
            ]

            result = service.deduplicate_by_sentence(words)
            assert len(result) == 3

        def test_empty_list(self, test_config):
            """Should return empty list for empty input."""
            service = WordFilterService(test_config)
            result = service.deduplicate_by_sentence([])
            assert result == []

        def test_preserves_order(self, test_config):
            """Should preserve the order of first occurrences."""
            service = WordFilterService(test_config)
            words = [
                create_word("A", sentence="s1"),
                create_word("B", sentence="s2"),
                create_word("C", sentence="s1"),
                create_word("D", sentence="s3"),
                create_word("E", sentence="s2"),
            ]

            result = service.deduplicate_by_sentence(words)
            assert [w.lemma for w in result] == ["A", "B", "D"]

        def test_normalizes_whitespace_and_fullwidth(self, test_config):
            """Sentences differing only by trailing whitespace or NFKC-foldable width."""
            service = WordFilterService(test_config)
            words = [
                create_word("A", sentence="１２時に会う。"),  # full-width digits
                create_word("B", sentence="12時に会う。 "),  # NFKC-folded + trailing space
                create_word("C", sentence="別の文章です。"),
                create_word("D", sentence="別の文章です。"),  # exact duplicate
            ]

            result = service.deduplicate_by_sentence(words)
            assert [w.lemma for w in result] == ["A", "C"]

    class TestFilterBySentenceLength:
        """Tests for filter_by_sentence_length (Issue #33)."""

        def _make_word(self, sentence: str, duration: float):
            """Helper: TokenizedWord with controllable sentence + duration."""
            return TokenizedWord(
                surface="x",
                lemma="x",
                reading="",
                sentence=sentence,
                start_time=0.0,
                end_time=duration,
                duration=duration,
            )

        def test_returns_input_when_no_caps_set(self, test_config):
            service = WordFilterService(test_config)
            words = [self._make_word("any sentence", 99.0)]
            assert service.filter_by_sentence_length(words, max_duration=0.0, max_chars=0) == words

        def test_drops_words_exceeding_duration(self, test_config):
            service = WordFilterService(test_config)
            words = [
                self._make_word("short", 2.0),
                self._make_word("long", 10.0),
            ]
            result = service.filter_by_sentence_length(words, max_duration=5.0, max_chars=0)
            assert [w.duration for w in result] == [2.0]

        def test_drops_words_exceeding_chars(self, test_config):
            service = WordFilterService(test_config)
            words = [
                self._make_word("short", 1.0),
                self._make_word("a" * 60, 1.0),
            ]
            result = service.filter_by_sentence_length(words, max_duration=0.0, max_chars=40)
            assert [w.sentence for w in result] == ["short"]

        def test_both_caps_apply_independently(self, test_config):
            service = WordFilterService(test_config)
            words = [
                self._make_word("ok", 2.0),  # passes both
                self._make_word("ok", 99.0),  # fails duration
                self._make_word("a" * 99, 2.0),  # fails chars
                self._make_word("a" * 99, 99.0),  # fails both
            ]
            result = service.filter_by_sentence_length(words, max_duration=5.0, max_chars=40)
            assert len(result) == 1
            assert result[0].sentence == "ok" and result[0].duration == 2.0

        def test_boundary_inclusive(self, test_config):
            """Word with sentence/duration exactly at the cap is kept."""
            service = WordFilterService(test_config)
            words = [
                self._make_word("a" * 40, 5.0),  # exactly at both caps
            ]
            result = service.filter_by_sentence_length(words, max_duration=5.0, max_chars=40)
            assert result == words

    class TestFilterIPlusOne:
        """Tests for filter_i_plus_one method."""

        @staticmethod
        def _line(
            lemmas: set[str],
            text: str = "line text",
            start: float = 0.0,
            end: float = 1.0,
            sentence_furigana: str = "",
            sentence_reading: str = "",
        ) -> LineLemmas:
            return LineLemmas(
                line_text=text,
                lemmas=frozenset(lemmas),
                start_time=start,
                end_time=end,
                duration=end - start,
                sentence_furigana=sentence_furigana,
                sentence_reading=sentence_reading,
            )

        def test_single_i_plus_one_match(self, test_config):
            """One word, one line with only that lemma — word kept, sentence swapped."""
            service = WordFilterService(test_config)
            word = create_word("X", sentence="original")
            line = self._line({"X"}, text="i+1 sentence", start=10.0, end=12.0)

            result = service.filter_i_plus_one([word], [line])

            assert len(result) == 1
            assert result[0].lemma == "X"
            assert result[0].sentence == "i+1 sentence"
            assert result[0].start_time == 10.0
            assert result[0].end_time == 12.0
            assert result[0].duration == 2.0

        def test_earliest_i_plus_one_wins(self, test_config):
            """Two i+1 lines for the same lemma — earliest is selected."""
            service = WordFilterService(test_config)
            word = create_word("X")
            lines = [
                self._line({"X"}, text="first", start=0.0, end=1.0),
                self._line({"unrelated"}, text="filler1"),
                self._line({"other"}, text="filler2"),
                self._line({"another"}, text="filler3"),
                self._line({"more"}, text="filler4"),
                self._line({"X"}, text="later", start=50.0, end=51.0),
            ]

            result = service.filter_i_plus_one([word], lines)

            assert len(result) == 1
            assert result[0].sentence == "first"

        def test_prefers_i_plus_one_over_non_i_plus_one(self, test_config):
            """Line with i+2 is skipped in favour of a later i+1 line."""
            service = WordFilterService(test_config)
            word_x = create_word("X")
            word_y = create_word("Y")
            lines = [
                self._line({"X", "Y"}, text="i+2 sentence", start=0.0, end=1.0),
                self._line({"unrelated"}, text="filler"),
                self._line({"X"}, text="i+1 sentence", start=20.0, end=22.0),
            ]

            result = service.filter_i_plus_one([word_x, word_y], lines)

            # Y has no i+1 line and is dropped; X picks the i+1 sentence.
            assert [w.lemma for w in result] == ["X"]
            assert result[0].sentence == "i+1 sentence"
            assert result[0].start_time == 20.0

        def test_word_only_in_non_i_plus_one_dropped(self, test_config):
            """Word only present in a multi-unknown line is dropped."""
            service = WordFilterService(test_config)
            word_x = create_word("X")
            word_y = create_word("Y")
            lines = [self._line({"X", "Y"}, text="i+2")]

            result = service.filter_i_plus_one([word_x, word_y], lines)

            assert result == []

        def test_word_with_no_lines_dropped(self, test_config):
            """Word whose lemma never appears in line_index is dropped."""
            service = WordFilterService(test_config)
            word_x = create_word("X")
            lines = [
                self._line({"A"}, text="line a"),
                self._line({"B"}, text="line b"),
            ]

            result = service.filter_i_plus_one([word_x], lines)

            assert result == []

        def test_lemma_only_in_i_plus_2_and_i_plus_3(self, test_config):
            """Word only in i+2 / i+3 lines is dropped."""
            service = WordFilterService(test_config)
            word_x = create_word("X")
            word_y = create_word("Y")
            word_z = create_word("Z")
            lines = [
                self._line({"X", "Y"}, text="i+2"),
                self._line({"X", "Y", "Z"}, text="i+3"),
            ]

            result = service.filter_i_plus_one([word_x, word_y, word_z], lines)

            assert result == []

        def test_multiple_words_independent(self, test_config):
            """Independent words pick their own earliest i+1 lines.

            X is i+1 in line 0. Y co-occurs with Z (also a mineable unknown)
            in line 1, making that line i+2 for the filter — so Y picks
            line 2 instead. Z has no i+1 coverage and is dropped.
            """
            service = WordFilterService(test_config)
            word_x = create_word("X")
            word_y = create_word("Y")
            word_z = create_word("Z")
            lines = [
                self._line({"X"}, text="line0", start=0.0, end=1.0),
                self._line({"Y", "Z"}, text="line1", start=2.0, end=3.0),
                self._line({"Y"}, text="line2", start=4.0, end=5.0),
            ]

            result = service.filter_i_plus_one([word_x, word_y, word_z], lines)

            assert [w.lemma for w in result] == ["X", "Y"]
            assert result[0].sentence == "line0"
            assert result[1].sentence == "line2"
            assert result[1].start_time == 4.0

        def test_line_with_non_mineable_unknown_rejected(self, test_config):
            """Issue #74 repro: a line holding the target plus an unknown that
            optional filters removed (e.g. outside max_frequency_rank) is NOT
            i+1 — the learner still can't read that other word."""
            service = WordFilterService(test_config)
            word = create_word("心")
            # 拝謁 is unknown but was frequency-filtered out of mineable_unknowns.
            line = self._line({"心", "拝謁"}, text="拝謁させていただき 心より御礼申し上げます")

            result = service.filter_i_plus_one([word], [line], all_unknown_lemmas={"心", "拝謁"})

            assert result == []

        def test_genuine_i_plus_one_line_preferred_over_false_one(self, test_config):
            """Target's earliest line has a hidden (non-mineable) unknown; a
            later line with only known words wins instead."""
            service = WordFilterService(test_config)
            word = create_word("X")
            lines = [
                self._line({"X", "rare"}, text="false i+1", start=0.0, end=1.0),
                self._line({"X"}, text="true i+1", start=20.0, end=22.0),
            ]

            result = service.filter_i_plus_one([word], lines, all_unknown_lemmas={"X", "rare"})

            assert len(result) == 1
            assert result[0].sentence == "true i+1"
            assert result[0].start_time == 20.0

        def test_all_unknown_lemmas_none_falls_back_to_targets(self, test_config):
            """Without all_unknown_lemmas the check degrades to the target set
            (pre-#74 behavior): non-target lemmas on the line are ignored."""
            service = WordFilterService(test_config)
            word = create_word("X")
            line = self._line({"X", "rare"}, text="kept")

            result = service.filter_i_plus_one([word], [line])

            assert len(result) == 1
            assert result[0].sentence == "kept"

        def test_all_unknown_lemmas_missing_target_still_matches(self, test_config):
            """Defensive union: a caller-supplied set that omits a target lemma
            must not make that target unmatchable on its own line."""
            service = WordFilterService(test_config)
            word = create_word("X")
            line = self._line({"X"}, text="solo line")

            result = service.filter_i_plus_one([word], [line], all_unknown_lemmas={"unrelated"})

            assert len(result) == 1
            assert result[0].sentence == "solo line"

        def test_line_with_only_non_target_unknown_sets_no_entry(self, test_config):
            """A line whose single unknown is non-mineable produces no i+1
            entry for anyone — and the target still finds its later line."""
            service = WordFilterService(test_config)
            word = create_word("X")
            lines = [
                self._line({"rare"}, text="non-target solo", start=0.0, end=1.0),
                self._line({"X"}, text="target solo", start=5.0, end=6.0),
            ]

            result = service.filter_i_plus_one([word], lines, all_unknown_lemmas={"X", "rare"})

            assert len(result) == 1
            assert result[0].sentence == "target solo"

        def test_empty_mineable_unknowns(self, test_config):
            """No mineable unknowns — returns []."""
            service = WordFilterService(test_config)
            line = self._line({"X"}, text="anything")

            assert service.filter_i_plus_one([], [line]) == []

        def test_empty_line_index(self, test_config):
            """No line_index — returns []."""
            service = WordFilterService(test_config)
            word = create_word("X")

            assert service.filter_i_plus_one([word], []) == []

        def test_swap_preserves_word_fields(self, test_config):
            """Per-word fields survive the sentence/timing swap."""
            service = WordFilterService(test_config)
            word = TokenizedWord(
                surface="食べた",
                lemma="食べる",
                reading="タベル",
                sentence="original sentence",
                start_time=0.0,
                end_time=1.0,
                duration=1.0,
                expression_furigana="食[た]べる",
                expression_reading="たべる",
                sentence_furigana="original furigana",
                sentence_reading="original reading",
                frequency_rank=42,
            )
            line = self._line(
                {"食べる"},
                text="new sentence",
                start=10.0,
                end=12.5,
                sentence_furigana="new furigana",
                sentence_reading="new reading",
            )

            result = service.filter_i_plus_one([word], [line])

            assert len(result) == 1
            swapped = result[0]
            # Per-word fields preserved.
            assert swapped.surface == "食べた"
            assert swapped.lemma == "食べる"
            assert swapped.reading == "タベル"
            assert swapped.expression_furigana == "食[た]べる"
            assert swapped.expression_reading == "たべる"
            assert swapped.frequency_rank == 42
            # Sentence/timing/sentence_furigana/sentence_reading swapped.
            assert swapped.sentence == "new sentence"
            assert swapped.start_time == 10.0
            assert swapped.end_time == 12.5
            assert swapped.duration == 2.5
            assert swapped.sentence_furigana == "new furigana"
            assert swapped.sentence_reading == "new reading"

        def test_i_plus_one_swap_updates_surface_and_offsets_from_lemma_spans(self, test_config):
            """When LineLemmas carries lemma_spans, the swap replaces surface and offsets
            with the matched lemma's morpheme on the new line (Issue #20)."""
            service = WordFilterService(test_config)
            word = TokenizedWord(
                surface="食べた",
                lemma="食べる",
                reading="タベル",
                sentence="original",
                start_time=0.0,
                end_time=1.0,
                duration=1.0,
                surface_start=0,
                surface_end=3,
            )
            line = LineLemmas(
                line_text="今日も食べる",
                lemmas=frozenset({"食べる"}),
                start_time=10.0,
                end_time=12.0,
                duration=2.0,
                lemma_spans=(("食べる", "食べる", 3, 6),),
            )
            result = service.filter_i_plus_one([word], [line])

            assert len(result) == 1
            swapped = result[0]
            assert swapped.surface == "食べる"  # Replaced with the form that appears on the new line
            assert swapped.surface_start == 3
            assert swapped.surface_end == 6
            assert swapped.sentence == "今日も食べる"
            # Bolded fields are empty because config flag is off (default).
            assert swapped.sentence_bolded == ""
            assert swapped.sentence_furigana_bolded == ""

        def test_i_plus_one_swap_recomputes_noun_furigana_reading(self):
            """Regression for T-37: a noun (mined_form == surface) whose surface
            differs across lines must end up with Expression, furigana, and
            reading mutually consistent — furigana/reading recomputed from the
            swapped-in surface, not left stale from the original surface.
            """
            from unittest.mock import MagicMock

            from anki_miner.config import AnkiMinerConfig

            # Mock tagger: returns one token whose surface+kana matches the text
            # it is handed, so generate_furigana/reading reflect the NEW surface.
            kana_by_surface = {"取り引き": "トリヒキ", "取引": "トリヒキ"}

            def _tagger(text):
                token = MagicMock()
                token.surface = text
                token.feature.kana = kana_by_surface.get(text, "")
                return [token]

            config = AnkiMinerConfig()  # bold flag off; recompute must NOT depend on it
            service = WordFilterService(config, tagger=_tagger)

            # Original word: noun mined as surface 取り引き; furigana/reading were
            # computed from that original surface.
            word = TokenizedWord(
                surface="取り引き",
                lemma="取引",
                reading="トリヒキ",
                sentence="original",
                start_time=0.0,
                end_time=1.0,
                duration=1.0,
                pos="名詞",
                expression_furigana="取り引き[とりひき]",
                expression_reading="とりひき",
            )
            # New i+1 line writes the same lemma with a DIFFERENT surface (取引).
            line = LineLemmas(
                line_text="今日の取引",
                lemmas=frozenset({"取引"}),
                start_time=10.0,
                end_time=12.0,
                duration=2.0,
                lemma_spans=(("取引", "取引", 3, 5),),
            )

            result = service.filter_i_plus_one([word], [line])

            assert len(result) == 1
            swapped = result[0]
            # The Expression (mined_form == surface for a noun) is the new surface.
            assert swapped.mined_form == "取引"
            # ...and furigana/reading are consistent WITH that new surface, not
            # the stale 取り引き[とりひき] / とりひき from the original.
            assert swapped.expression_furigana == "取引[とりひき]"
            assert swapped.expression_reading == "とりひき"

        def test_i_plus_one_swap_recomputes_bolded_when_flag_on(self):
            """When the bold flag is on and a tagger is supplied, the swap rebuilds
            the bolded sentence + furigana against the new line."""
            from unittest.mock import MagicMock, PropertyMock

            from anki_miner.config import AnkiMinerConfig

            config = AnkiMinerConfig(bold_target_in_sentence=True)

            # Mock tagger that yields three tokens partitioning "今日も食べる".
            def _tok(surface, kana):
                token = MagicMock()
                token.surface = surface
                if kana is None:
                    token.feature = MagicMock(spec=[])
                    type(token.feature).kana = PropertyMock(side_effect=AttributeError)
                else:
                    token.feature.kana = kana
                return token

            tagger = MagicMock(return_value=[_tok("今日", "キョウ"), _tok("も", "モ"), _tok("食べる", "タベル")])
            service = WordFilterService(config, tagger=tagger)

            word = TokenizedWord(
                surface="食べた",
                lemma="食べる",
                reading="タベル",
                sentence="original",
                start_time=0.0,
                end_time=1.0,
                duration=1.0,
            )
            line = LineLemmas(
                line_text="今日も食べる",
                lemmas=frozenset({"食べる"}),
                start_time=10.0,
                end_time=12.0,
                duration=2.0,
                lemma_spans=(("食べる", "食べる", 3, 6),),
            )

            result = service.filter_i_plus_one([word], [line])
            assert len(result) == 1
            swapped = result[0]
            assert swapped.sentence == "今日も食べる"
            assert "<b>食べる</b>" in swapped.sentence_bolded
            assert "<b>" in swapped.sentence_furigana_bolded
            assert "食べる" in swapped.sentence_furigana_bolded

        def test_blacklisted_lemma_not_counted_as_unknown(self, test_config):
            """Lemmas absent from target_lemmas (blacklisted upstream) don't count.

            Y was filtered out by the blacklist upstream, so it isn't in
            ``mineable_unknowns``. From the i+1 filter's view, the line
            containing {X, Y} intersects target_lemmas only at X — that is
            i+1 for X, and X is kept.
            """
            service = WordFilterService(test_config)
            word_x = create_word("X")  # Y is NOT in mineable_unknowns
            line = self._line({"X", "Y"}, text="X plus blacklisted Y", start=5.0, end=6.0)

            result = service.filter_i_plus_one([word_x], [line])

            assert len(result) == 1
            assert result[0].lemma == "X"
            assert result[0].sentence == "X plus blacklisted Y"
            assert result[0].start_time == 5.0
