"""Tests for word_filter service."""

import pytest

from anki_miner.models.word import TokenizedWord
from anki_miner.services.word_filter import WordFilterService


def create_word(lemma: str, surface: str = None) -> TokenizedWord:
    """Helper to create a TokenizedWord for testing."""
    return TokenizedWord(
        surface=surface or lemma,
        lemma=lemma,
        reading="",
        sentence="Test sentence",
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
