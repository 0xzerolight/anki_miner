"""Logging regressions for batched media extraction failures."""

import logging
from unittest.mock import patch

from anki_miner.services.media_extractor import MediaExtractorService

MODULE = "anki_miner.services.media_extractor"


def test_exception_failure_warning_names_lemma(test_config, make_tokenized_word, tmp_path, caplog):
    service = MediaExtractorService(test_config)
    word = make_tokenized_word(surface="失敗", lemma="失敗")

    with (
        patch.object(service, "extract_media", side_effect=RuntimeError("ffmpeg exploded")),
        caplog.at_level(logging.INFO, logger=MODULE),
    ):
        service.extract_media_batch(tmp_path / "episode.mkv", [word])

    record = next(record for record in caplog.records if record.getMessage().startswith("Media extraction exception:"))
    assert record.levelno == logging.WARNING
    assert record.name == MODULE
    assert "lemma=失敗" in record.getMessage()


def test_failure_warning_cap_holds_for_large_batch(test_config, make_tokenized_word, tmp_path, caplog):
    service = MediaExtractorService(test_config)
    words = [make_tokenized_word(surface=f"失敗{i}", lemma=f"失敗{i}", start_time=float(i)) for i in range(50)]

    with (
        patch.object(service, "_extract_screenshot", return_value=False),
        caplog.at_level(logging.INFO, logger=MODULE),
    ):
        service.extract_media_batch(tmp_path / "episode.mkv", words, include_audio=False)

    warnings = [
        record
        for record in caplog.records
        if record.levelno == logging.WARNING and record.getMessage().startswith("Media extraction failed:")
    ]
    summaries = [record for record in caplog.records if record.getMessage().startswith("Media extraction done:")]
    assert len(warnings) == 5
    assert len(summaries) == 1


def test_failure_summary_carries_counters(test_config, make_tokenized_word, tmp_path, caplog):
    service = MediaExtractorService(test_config)
    word = make_tokenized_word(surface="失敗", lemma="失敗")

    with (
        patch.object(service, "_extract_screenshot", return_value=False),
        caplog.at_level(logging.INFO, logger=MODULE),
    ):
        service.extract_media_batch(tmp_path / "episode.mkv", [word], include_audio=False)

    record = next(record for record in caplog.records if record.getMessage().startswith("Media extraction done:"))
    assert record.levelno == logging.INFO
    assert record.name == MODULE
    assert "screenshot_failures=1" in record.getMessage()
