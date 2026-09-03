"""Operational logging tests for the five-stage episode processor."""

from __future__ import annotations

import collections
import logging
import threading
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from anki_miner.exceptions import SetupError
from anki_miner.models import MediaData, TokenizedWord
from anki_miner.models.reading import ImageRef, ReadingDocument, ReadingUnit
from anki_miner.models.youtube import FetchedMedia
from tests.conftest import build_processor

_LOGGER = "anki_miner.orchestration.episode_processor"


def _word(index: int) -> TokenizedWord:
    lemma = f"word{index}"
    return TokenizedWord(
        surface=lemma,
        lemma=lemma,
        reading="ワード",
        sentence=f"sentence {index}",
        start_time=float(index),
        end_time=float(index + 1),
        duration=1.0,
        pos="名詞",
    )


def _processor_for_words(test_config, words: list[TokenizedWord]):
    subtitle_parser = MagicMock(name="SubtitleParser")
    subtitle_parser.parse_subtitle_file.return_value = words
    subtitle_parser.count_lemmas.return_value = collections.Counter(word.lemma for word in words)
    subtitle_parser.parse_raw_entries.return_value = [(word.start_time, word.end_time, word.sentence) for word in words]

    word_filter = MagicMock(name="WordFilter")
    word_filter.filter_unknown.side_effect = lambda candidates, _known: list(candidates)
    word_filter.deduplicate_by_sentence.side_effect = lambda candidates: list(candidates)
    word_filter.filter_by_episode_count.side_effect = lambda candidates, counts, floor: [
        word for word in candidates if counts.get(word.lemma, 0) >= floor
    ]

    media_extractor = MagicMock(name="MediaExtractor")
    media_extractor.extract_media_batch.side_effect = lambda _video, candidates, *_args, **_kwargs: [
        (word, MediaData(audio_filename=f"audio-{index}.mp3")) for index, word in enumerate(candidates)
    ]

    definition_service = MagicMock(name="DefinitionService")
    definition_service.has_usable_offline_provider.return_value = True
    definition_service.has_offline_definitions.side_effect = lambda terms: dict.fromkeys(terms, True)
    definition_service.offline_term_identities.return_value = {}

    def _definitions(pairs, *_args, is_cancelled):
        assert is_cancelled() is False
        return ["definition"] * len(pairs)

    definition_service.get_definitions_batch.side_effect = _definitions

    anki_service = MagicMock(name="AnkiService")
    anki_service.get_existing_vocabulary.return_value = set()
    anki_service.last_created_note_ids = []
    anki_service.last_media_store_failures = 0
    anki_service.last_skipped_duplicates = 0

    def _create_cards(card_data, _progress_callback=None):
        note_ids = list(range(1, len(card_data) + 1))
        anki_service.last_created_note_ids = note_ids
        return note_ids

    anki_service.create_cards_batch.side_effect = _create_cards

    processor = build_processor(
        test_config,
        subtitle_parser=subtitle_parser,
        word_filter=word_filter,
        media_extractor=media_extractor,
        definition_service=definition_service,
        anki_service=anki_service,
    )
    return processor, subtitle_parser


def _run_episode(processor, tmp_path: Path):
    return processor.process_episode(tmp_path / "episode.mkv", tmp_path / "episode.ass")


def _summary_record(caplog, prefix: str):
    return next(
        record for record in caplog.records if record.name == _LOGGER and record.getMessage().startswith(prefix)
    )


def test_definition_double_requires_live_cancel_predicate(test_config, tmp_path):
    processor, _ = _processor_for_words(test_config, [_word(0)])

    result = _run_episode(processor, tmp_path)

    assert result.cards_created == 1
    lookup_call = processor.definition_service.get_definitions_batch.call_args
    is_cancelled = lookup_call.kwargs["is_cancelled"]
    assert is_cancelled() is False
    with pytest.raises(TypeError, match="is_cancelled"):
        processor.definition_service.get_definitions_batch.side_effect(*lookup_call.args)
    processor.cancel()
    assert is_cancelled() is True


def test_full_fake_run_emits_one_count_summary_per_phase(test_config, tmp_path, caplog):
    words = [_word(index) for index in range(5)]
    processor, subtitle_parser = _processor_for_words(test_config, words)
    subtitle_parser.count_lemmas.return_value = collections.Counter(
        {words[0].lemma: 3, words[1].lemma: 2, words[2].lemma: 2, words[3].lemma: 1, words[4].lemma: 1}
    )
    subtitle_parser.parse_raw_entries.return_value = [
        (float(index), float(index + 1), f"line {index}") for index in range(7)
    ]

    with caplog.at_level(logging.INFO, logger=_LOGGER):
        result = _run_episode(processor, tmp_path)

    assert result.cards_created == 5
    expected = {
        "Phase 1 parse:": "lines=7",
        "Phase 2 filter:": "in=5",
        "Phase 3 extract:": "attempted=5",
        "Phase 4 lookup:": "looked_up=5",
        "Phase 5 create:": "attempted=5",
    }
    for prefix, count in expected.items():
        record = _summary_record(caplog, prefix)
        assert count in record.getMessage()
        assert record.levelno == logging.INFO
    parse_record = _summary_record(caplog, "Phase 1 parse:")
    assert "tokens=9" in parse_record.getMessage()
    assert "unique=5" in parse_record.getMessage()


def test_typed_phase_failure_logs_one_run_failed_receipt(test_config, tmp_path, caplog):
    processor, subtitle_parser = _processor_for_words(test_config, [])
    subtitle_parser.parse_subtitle_file.side_effect = SetupError("test setup failed")

    with caplog.at_level(logging.INFO, logger=_LOGGER):
        result = _run_episode(processor, tmp_path)

    warnings = [record for record in caplog.records if record.name == _LOGGER and record.levelno == logging.WARNING]
    assert result.success is False
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert message.startswith("EpisodeProcessor run failed:")
    assert "kind=episode" in message
    assert "episode=episode" in message
    assert "SetupError: test setup failed" in message
    assert warnings[0].exc_info is None


def test_log_record_count_does_not_scale_with_word_count(test_config, tmp_path, caplog):
    with caplog.at_level(logging.INFO, logger=_LOGGER):
        small, _ = _processor_for_words(test_config, [_word(index) for index in range(5)])
        _run_episode(small, tmp_path)
        small_count = sum(record.name == _LOGGER for record in caplog.records)

        caplog.clear()
        large, _ = _processor_for_words(test_config, [_word(index) for index in range(50)])
        _run_episode(large, tmp_path)
        large_count = sum(record.name == _LOGGER for record in caplog.records)

    assert large_count == small_count


def test_reading_run_emits_reading_parse_and_media_summaries(test_config, caplog):
    words = [_word(0), _word(1)]
    processor, subtitle_parser = _processor_for_words(replace(test_config, reading_min_occurrence=2), words)
    subtitle_parser.parse_text_units.return_value = (
        words,
        None,
        collections.Counter({words[0].lemma: 2, words[1].lemma: 1}),
    )
    archive = Path("/volume.cbz")
    document = ReadingDocument(
        title="Volume",
        kind="book",
        series="Series",
        episode="Volume 1",
        units=[
            ReadingUnit(
                text="first",
                index=0,
                location_label="p.1",
                image_ref=ImageRef(archive, "page1.jpg"),
            ),
            ReadingUnit(text="second", index=1, location_label="p.2"),
        ],
    )

    with (
        patch("anki_miner.orchestration.episode_processor.prepare_card_image", side_effect=SetupError("unsafe")),
        caplog.at_level(logging.INFO, logger=_LOGGER),
    ):
        result = processor.process_reading(document)

    assert result.cards_created == 1
    parse_record = _summary_record(caplog, "Phase 1 parse:")
    assert "tokens=3" in parse_record.getMessage()
    filter_record = _summary_record(caplog, "Phase 2 filter:")
    assert "out=1" in filter_record.getMessage()
    assert "episode_rejects=1" in filter_record.getMessage()
    media_record = _summary_record(caplog, "Phase 3 reading media:")
    assert "attempted=1" in media_record.getMessage()
    assert "produced=1" in media_record.getMessage()
    assert "failures=0" in media_record.getMessage()
    assert "archive_failures=1" in media_record.getMessage()


def _records(caplog, prefix: str):
    return [record for record in caplog.records if record.name == _LOGGER and record.getMessage().startswith(prefix)]


def test_episode_run_stamps_exactly_one_pipeline_receipt(test_config, tmp_path, caplog):
    processor, _ = _processor_for_words(test_config, [_word(0)])

    with caplog.at_level(logging.DEBUG, logger=_LOGGER):
        result = _run_episode(processor, tmp_path)

    starts = _records(caplog, "Pipeline start:")
    ends = _records(caplog, "Pipeline end:")
    assert len(starts) == 1
    assert len(ends) == 1
    start = starts[0].getMessage()
    assert starts[0].levelno == logging.INFO
    for field in (
        "kind=episode",
        "episode=episode",
        f"video={tmp_path / 'episode.mkv'}",
        f"subtitle={tmp_path / 'episode.ass'}",
        "secondary=-",
        "deck=test_deck",
        "note_type=test_note_type",
        "language=ja",
        "offset=0.0",
        "curation=False",
        "filters=",
    ):
        assert field in start, start
    end = ends[0].getMessage()
    assert "kind=episode" in end
    assert "outcome=success" in end
    assert "cards=1" in end
    assert "elapsed=" in end
    assert result.cards_created == 1


def test_cancelled_episode_reports_a_cancelled_outcome(test_config, tmp_path, caplog):
    processor, subtitle_parser = _processor_for_words(test_config, [_word(0)])
    cancel_event = threading.Event()

    def _parse(*_args, **_kwargs):
        cancel_event.set()
        return [_word(0)]

    subtitle_parser.parse_subtitle_file.side_effect = _parse

    with caplog.at_level(logging.INFO, logger=_LOGGER):
        processor.process_episode(
            tmp_path / "episode.mkv",
            tmp_path / "episode.ass",
            cancel_event=cancel_event,
        )

    ends = _records(caplog, "Pipeline end:")
    assert len(ends) == 1
    assert "outcome=cancelled" in ends[0].getMessage()


def test_youtube_run_stamps_one_receipt_with_fetch_identity(test_config, tmp_path, caplog):
    video_file = tmp_path / "abc123.mp4"
    subtitle_file = tmp_path / "abc123.ja.srt"
    video_file.touch()
    subtitle_file.touch()

    processor, _ = _processor_for_words(test_config, [_word(0)])
    fetcher = MagicMock(name="YouTubeFetcher")
    fetcher.fetch_video.return_value = FetchedMedia(
        video_file=video_file,
        subtitle_file=subtitle_file,
        sub_source="manual",
    )
    processor._youtube_fetcher = fetcher

    with caplog.at_level(logging.INFO, logger=_LOGGER):
        processor.process_youtube_url(
            url="https://www.youtube.com/watch?v=abc123&si=TRACKING",
            video_id="abc123",
            workspace=tmp_path,
            sub_mode="manual_only",
            cancel_event=threading.Event(),
        )

    starts = _records(caplog, "Pipeline start:")
    ends = _records(caplog, "Pipeline end:")
    assert len(starts) == 1
    assert len(ends) == 1
    start = starts[0].getMessage()
    assert "kind=youtube" in start
    assert "url=https://www.youtube.com/watch?v=abc123" in start
    assert "TRACKING" not in start
    assert "video_id=abc123" in start
    assert "sub_mode=manual_only" in start
    assert f"workspace={tmp_path}" in start
    assert "align_captions=False" in start
    assert "kind=youtube" in ends[0].getMessage()
    assert "outcome=success" in ends[0].getMessage()


def test_youtube_cancelled_before_fetch_still_closes_the_receipt(test_config, tmp_path, caplog):
    processor, _ = _processor_for_words(test_config, [])
    processor._youtube_fetcher = MagicMock(name="YouTubeFetcher")
    cancel_event = threading.Event()
    cancel_event.set()

    with caplog.at_level(logging.INFO, logger=_LOGGER):
        processor.process_youtube_url(
            url="https://youtu.be/abc123",
            video_id="abc123",
            workspace=tmp_path,
            sub_mode="manual_only",
            cancel_event=cancel_event,
        )

    assert len(_records(caplog, "Pipeline start:")) == 1
    ends = _records(caplog, "Pipeline end:")
    assert len(ends) == 1
    assert "outcome=cancelled" in ends[0].getMessage()


def test_words_dropped_for_missing_definitions_are_listed_at_debug(test_config, tmp_path, caplog):
    words = [_word(index) for index in range(60)]
    processor, _ = _processor_for_words(test_config, words)
    processor.definition_service.has_offline_definitions.side_effect = lambda terms: {}
    processor.definition_service.offline_deinflection_terms_exist.return_value = set()

    with caplog.at_level(logging.DEBUG, logger=_LOGGER):
        _run_episode(processor, tmp_path)

    records = _records(caplog, "Definitions missing:")
    assert len(records) == 1
    message = records[0].getMessage()
    assert records[0].levelno == logging.DEBUG
    assert "count=60" in message
    assert "word0" in message
    assert "+10 more" in message


def test_inert_frequency_cutoff_logs_a_warning(test_config, tmp_path, caplog):
    config = replace(test_config, max_frequency_rank=10000, min_frequency_rank=500)
    processor, _ = _processor_for_words(config, [_word(0)])

    with caplog.at_level(logging.INFO, logger=_LOGGER):
        _run_episode(processor, tmp_path)

    records = _records(caplog, "Frequency cutoff ignored:")
    assert len(records) == 1
    assert records[0].levelno == logging.WARNING
    message = records[0].getMessage()
    assert "low=500" in message
    assert "high=10000" in message
    assert "sources=-" in message


def _reading_document(archive: Path, warnings: list[str] | None = None) -> ReadingDocument:
    return ReadingDocument(
        title="Volume",
        kind="book",
        series="Series",
        episode="Volume 1",
        units=[
            ReadingUnit(
                text="first",
                index=0,
                location_label="p.1",
                image_ref=ImageRef(archive, "page1.jpg"),
            ),
        ],
        warnings=list(warnings or []),
    )


def test_reading_archive_failure_names_the_archive_and_exception(test_config, caplog):
    words = [_word(0)]
    processor, subtitle_parser = _processor_for_words(test_config, words)
    subtitle_parser.parse_text_units.return_value = (words, None, collections.Counter({words[0].lemma: 1}))
    archive = Path("/volume.cbz")

    with (
        patch("anki_miner.orchestration.episode_processor.prepare_card_image", side_effect=SetupError("unsafe zip")),
        caplog.at_level(logging.INFO, logger=_LOGGER),
    ):
        processor.process_reading(_reading_document(archive))

    records = _records(caplog, "Reading image failed:")
    assert len(records) == 1
    assert records[0].levelno == logging.WARNING
    message = records[0].getMessage()
    assert "archive=/volume.cbz" in message
    assert "ref=page1.jpg" in message
    assert "SetupError" in message
    assert "unsafe zip" in message


def test_reading_document_warnings_are_logged_once(test_config, caplog):
    words = [_word(0)]
    processor, subtitle_parser = _processor_for_words(test_config, words)
    subtitle_parser.parse_text_units.return_value = (words, None, collections.Counter({words[0].lemma: 1}))
    document = _reading_document(Path("/volume.cbz"), warnings=[f"warning {i}" for i in range(60)])

    with (
        patch("anki_miner.orchestration.episode_processor.prepare_card_image", return_value=Path("/img.png")),
        caplog.at_level(logging.INFO, logger=_LOGGER),
    ):
        processor.process_reading(document)

    records = _records(caplog, "Reading document warnings:")
    assert len(records) == 1
    assert records[0].levelno == logging.WARNING
    message = records[0].getMessage()
    assert "count=60" in message
    assert "+10 more" in message


def test_reading_run_stamps_a_reading_receipt(test_config, caplog):
    words = [_word(0)]
    processor, subtitle_parser = _processor_for_words(test_config, words)
    subtitle_parser.parse_text_units.return_value = (words, None, collections.Counter({words[0].lemma: 1}))

    with (
        patch("anki_miner.orchestration.episode_processor.prepare_card_image", return_value=Path("/img.png")),
        caplog.at_level(logging.INFO, logger=_LOGGER),
    ):
        processor.process_reading(_reading_document(Path("/volume.cbz")))

    starts = _records(caplog, "Pipeline start:")
    ends = _records(caplog, "Pipeline end:")
    assert len(starts) == 1
    assert len(ends) == 1
    assert "kind=reading" in starts[0].getMessage()
    assert "kind=reading" in ends[0].getMessage()
