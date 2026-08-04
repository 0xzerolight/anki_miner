"""Tests for the shared structured logging helpers."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from anki_miner.exceptions import OperationCancelled
from anki_miner.utils.logging_ext import log_summary, suppressed

LOGGER_NAME = "anki_miner.tests.logging_ext"


def test_log_summary_renders_fields_in_insertion_order(caplog: pytest.LogCaptureFixture):
    log = logging.getLogger(LOGGER_NAME)

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        log_summary(log, "Backfill scan", matched=412, scanned=412, notes=88, enabled=True)

    records = [record for record in caplog.records if record.getMessage().startswith("Backfill scan:")]
    assert len(records) == 1
    assert records[0].getMessage() == "Backfill scan: matched=412 scanned=412 notes=88 enabled=True"


def test_log_summary_renders_empty_collections_lists_and_path_names(caplog: pytest.LogCaptureFixture):
    log = logging.getLogger(LOGGER_NAME)

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        log_summary(
            log,
            "Empty values",
            none=None,
            empty_string="",
            empty_list=[],
            empty_tuple=(),
            empty_set=set(),
            empty_dict={},
            items=["one", "two"],
            path=Path("/private/media/example.srt"),
        )

    line = next(record.getMessage() for record in caplog.records if record.getMessage().startswith("Empty values:"))
    assert line == (
        "Empty values: none=- empty_string=- empty_list=- empty_tuple=- "
        "empty_set=- empty_dict=- items=one,two path=example.srt"
    )


def test_log_summary_collapses_value_whitespace(caplog: pytest.LogCaptureFixture):
    log = logging.getLogger(LOGGER_NAME)

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        log_summary(log, "Whitespace", value="two \t\n words")

    line = next(record.getMessage() for record in caplog.records if record.getMessage().startswith("Whitespace:"))
    assert line == "Whitespace: value=two_words"


def test_log_summary_accepts_warning_level(caplog: pytest.LogCaptureFixture):
    log = logging.getLogger(LOGGER_NAME)

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        log_summary(log, "Degraded import", level=logging.WARNING, skipped=2)

    record = next(record for record in caplog.records if record.getMessage().startswith("Degraded import:"))
    assert record.levelno == logging.WARNING
    assert record.getMessage() == "Degraded import: skipped=2"


def test_log_summary_with_no_fields_has_no_trailing_space(caplog: pytest.LogCaptureFixture):
    log = logging.getLogger(LOGGER_NAME)

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        log_summary(log, "No changes")

    line = next(record.getMessage() for record in caplog.records if record.getMessage().startswith("No changes:"))
    assert line == "No changes:"


def test_log_summary_uses_the_callers_logger(caplog: pytest.LogCaptureFixture):
    log = logging.getLogger(LOGGER_NAME)

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        log_summary(log, "Caller attribution", count=1)

    record = next(record for record in caplog.records if record.getMessage().startswith("Caller attribution:"))
    assert record.name == LOGGER_NAME


def test_suppressed_logs_at_debug_and_continues(caplog: pytest.LogCaptureFixture):
    log = logging.getLogger(LOGGER_NAME)
    continued = False

    with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
        with suppressed(log, "reading generation"):
            raise ValueError("bad value")
        continued = True

    assert continued
    record = next(record for record in caplog.records if record.getMessage().startswith("Ignored failure during"))
    assert record.levelno == logging.DEBUG
    assert "reading generation: ValueError: bad value" in record.getMessage()
    assert record.exc_info is None


def test_suppressed_reraises_operation_cancelled_without_logging(caplog: pytest.LogCaptureFixture):
    log = logging.getLogger(LOGGER_NAME)

    with (
        caplog.at_level(logging.DEBUG, logger=LOGGER_NAME),
        pytest.raises(OperationCancelled),
        suppressed(log, "reading generation"),
    ):
        raise OperationCancelled("cancelled")

    assert not any(record.getMessage().startswith("Ignored failure during") for record in caplog.records)


def test_suppressed_logs_nothing_when_body_succeeds(caplog: pytest.LogCaptureFixture):
    log = logging.getLogger(LOGGER_NAME)

    with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME), suppressed(log, "reading generation"):
        pass

    assert not any(record.getMessage().startswith("Ignored failure during") for record in caplog.records)
