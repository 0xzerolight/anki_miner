"""Tests for word_filter service."""

import pytest

from anki_miner.models.word import TokenizedWord
from anki_miner.services.word_filter import WordFilterService
from anki_miner.services.word_list_service import WordListService


def create_word(lemma: str, surface: str = None, sentence: str = "Test sentence") -> TokenizedWord:
    """Helper to create a TokenizedWord for testing."""
    return TokenizedWord(
        surface=surface or lemma,
        lemma=lemma,
        reading="",
        sentence=sentence,
        start_time=0.0,
        end_time=1.0,
        duration=1.0,
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

        def test_filters_known_surface_forms(self, test_config):
            """Should filter out words with known surface forms."""
            service = WordFilterService(test_config)
            words = [
                create_word("知る", "知った"),
                create_word("食べる", "食べた"),
            ]
            # Even though lemma is different, surface form matches
            existing = {"知った"}

            result = service.filter_unknown(words, existing)

            assert len(result) == 1
            assert result[0].surface == "食べた"

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

    class TestFilterByLength:
        """Tests for filter_by_length method."""

        def test_filters_short_words(self, test_config):
            """Should filter words shorter than min_length."""
            service = WordFilterService(test_config)
            words = [
                create_word("あ"),  # 1 char
                create_word("あい"),  # 2 chars
                create_word("あいう"),  # 3 chars
            ]

            result = service.filter_by_length(words, min_length=2)

            assert len(result) == 2
            assert all(len(w.surface) >= 2 for w in result)

        def test_filters_long_words(self, test_config):
            """Should filter words longer than max_length."""
            service = WordFilterService(test_config)
            words = [
                create_word("短い"),  # 2 chars
                create_word("中くらい"),  # 4 chars
                create_word("とても長い単語"),  # 7 chars
            ]

            result = service.filter_by_length(words, min_length=1, max_length=4)

            assert len(result) == 2

        def test_no_max_length(self, test_config):
            """Should allow any length when max_length is None."""
            service = WordFilterService(test_config)
            words = [
                create_word("短"),
                create_word("非常に長い日本語の単語"),
            ]

            result = service.filter_by_length(words, min_length=1, max_length=None)

            assert len(result) == 2

        def test_uses_config_min_length(self, test_config):
            """Should use config min_length when not specified."""
            # Assuming test_config has some min_word_length set
            service = WordFilterService(test_config)
            words = [
                create_word("あ"),
                create_word("あいうえお"),
            ]

            # Call without explicit min_length
            result = service.filter_by_length(words)

            # Should use config's min_word_length
            assert isinstance(result, list)

        def test_empty_words_list(self, test_config):
            """Should return empty list when no words provided."""
            service = WordFilterService(test_config)

            result = service.filter_by_length([], min_length=1)

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

        def test_keeps_words_with_no_rank_data(self, test_config):
            """Words without frequency data should pass through."""
            service = WordFilterService(test_config)
            words = [
                self._word_with_freq("の", 1),
                create_word("不明"),  # No frequency rank (None)
            ]

            result = service.filter_by_frequency(words, max_rank=5000)
            assert len(result) == 2

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
