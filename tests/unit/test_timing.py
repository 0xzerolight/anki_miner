"""Tests for utils.timing.timed_phase (per-phase wall-clock instrumentation)."""

from __future__ import annotations

import logging

import pytest

from anki_miner.utils.timing import timed_phase


def test_timed_phase_logs_duration_at_info(caplog: pytest.LogCaptureFixture):
    with caplog.at_level(logging.INFO, logger="anki_miner.utils.timing"), timed_phase("parse"):
        pass
    records = [r for r in caplog.records if "[timing]" in r.getMessage()]
    assert len(records) == 1
    assert records[0].levelno == logging.INFO
    msg = records[0].getMessage()
    assert msg.startswith("[timing] parse: ")
    assert msg.endswith("s")


def test_timed_phase_logs_on_exception(caplog: pytest.LogCaptureFixture):
    with (
        caplog.at_level(logging.INFO, logger="anki_miner.utils.timing"),
        pytest.raises(RuntimeError),
        timed_phase("extract"),
    ):
        raise RuntimeError("boom")
    assert any("[timing] extract:" in r.getMessage() for r in caplog.records)


def test_timed_phase_uses_given_logger(caplog: pytest.LogCaptureFixture):
    other = logging.getLogger("anki_miner.test.other_module")
    with caplog.at_level(logging.INFO, logger="anki_miner.test.other_module"), timed_phase("cards", other):
        pass
    records = [r for r in caplog.records if "[timing] cards:" in r.getMessage()]
    assert len(records) == 1
    assert records[0].name == "anki_miner.test.other_module"
