"""Strict card creation order: words reach phase 3 in first-appearance order."""

from collections import Counter
from dataclasses import replace
from unittest.mock import MagicMock, patch

import pytest

from anki_miner.models import TokenizedWord
from anki_miner.orchestration.episode_processor import EpisodeProcessor
from anki_miner.presenters import NullPresenter


def _make_word(lemma, start_time=1.0):
    return TokenizedWord(
        surface=f"{lemma}た",
        lemma=lemma,
        reading="タベル",
        sentence=f"{lemma}のテスト",
        start_time=start_time,
        end_time=start_time + 2.0,
        duration=2.0,
    )


@pytest.fixture
def mock_services():
    subtitle_parser = MagicMock()
    # Curation builds the line index too, so mirror parse_subtitle_file's
    # configured return through the with-index path (no candidates).
    subtitle_parser.parse_subtitle_file_with_index.side_effect = lambda f, offset=None: (
        subtitle_parser.parse_subtitle_file.return_value,
        [],
    )
    word_filter = MagicMock()
    word_filter.deduplicate_by_sentence.side_effect = lambda w: w
    # The reading path passes occurrence_counts, which turns on the in-document
    # occurrence floor; left as a bare MagicMock it swallows the word list.
    word_filter.filter_by_episode_count.side_effect = lambda w, counts, minimum: w
    media_extractor = MagicMock()
    definition_service = MagicMock()
    anki_service = MagicMock()
    return {
        "subtitle_parser": subtitle_parser,
        "word_filter": word_filter,
        "media_extractor": media_extractor,
        "definition_service": definition_service,
        "anki_service": anki_service,
    }


def _processor(test_config, mock_services, *, strict):
    return EpisodeProcessor(
        config=replace(test_config, strict_card_order=strict),
        presenter=NullPresenter(),
        **mock_services,
    )


def _run_with_curation(processor, mock_services, tmp_path, words, curated):
    mock_services["subtitle_parser"].parse_subtitle_file.return_value = words
    mock_services["anki_service"].get_existing_vocabulary.return_value = set()
    mock_services["word_filter"].filter_unknown.return_value = list(words)
    mock_services["media_extractor"].extract_media_batch.return_value = []
    processor.process_episode(
        tmp_path / "v.mkv",
        tmp_path / "s.ass",
        curation_callback=lambda _words: curated,
    )
    return [w.lemma for w in mock_services["media_extractor"].extract_media_batch.call_args[0][1]]


def test_strict_order_restores_appearance_order_after_curation(test_config, mock_services, tmp_path):
    words = [_make_word("食べる", 1.0), _make_word("走る", 5.0), _make_word("飲む", 9.0)]
    processor = _processor(test_config, mock_services, strict=True)

    extracted = _run_with_curation(processor, mock_services, tmp_path, words, [words[2], words[0]])

    assert extracted == ["食べる", "飲む"]


def test_default_leaves_the_curated_order_alone(test_config, mock_services, tmp_path):
    words = [_make_word("食べる", 1.0), _make_word("走る", 5.0), _make_word("飲む", 9.0)]
    processor = _processor(test_config, mock_services, strict=False)

    extracted = _run_with_curation(processor, mock_services, tmp_path, words, [words[2], words[0]])

    assert extracted == ["飲む", "食べる"]


def test_helper_restores_a_force_include_prepend(test_config, mock_services):
    """The whitelist prepend (forced + rest) is undone by the same sort."""
    all_words = [_make_word("食べる", 1.0), _make_word("走る", 5.0), _make_word("飲む", 9.0)]
    prepended = [all_words[2], all_words[0], all_words[1]]
    processor = _processor(test_config, mock_services, strict=True)

    ordered = processor._apply_strict_card_order(prepended, all_words)

    assert [w.lemma for w in ordered] == ["食べる", "走る", "飲む"]


def test_helper_is_a_no_op_when_disabled(test_config, mock_services):
    all_words = [_make_word("食べる", 1.0), _make_word("走る", 5.0)]
    prepended = [all_words[1], all_words[0]]
    processor = _processor(test_config, mock_services, strict=False)

    assert processor._apply_strict_card_order(prepended, all_words) is prepended


def test_reading_path_sorts_too(test_config, mock_services, tmp_path):
    words = [_make_word("食べる", 0.0), _make_word("走る", 1.0), _make_word("飲む", 2.0)]
    mock_services["subtitle_parser"].parse_text_units.return_value = (words, None, Counter())
    mock_services["anki_service"].get_existing_vocabulary.return_value = set()
    mock_services["word_filter"].filter_unknown.return_value = list(words)
    processor = _processor(test_config, mock_services, strict=True)

    document = MagicMock()
    document.kind = "manga"
    document.series = "テスト"
    document.episode = "1巻"
    document.title = "テスト 1巻"
    document.units = []
    document.warnings = []

    with patch.object(processor, "_phase3_reading_media", return_value=[]) as phase3:
        processor.process_reading(document, curation_callback=lambda _words: [words[2], words[0]])

    assert [w.lemma for w in phase3.call_args[0][2]] == ["食べる", "飲む"]
