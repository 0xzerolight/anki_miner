"""The Activity Log mirrors every line it shows into the file log.

A support report arrives as ``anki_miner.log``, never as a screenshot of the
Activity panel, so what the user read on screen has to be recoverable from the
file. The mirror runs once per appended entry — rotation re-renders the widget
but must not re-emit, and ``clear_log`` erases the panel without erasing the
record.
"""

import logging

import pytest

from anki_miner.gui.widgets.log_widget import ACTIVITY_LOGGER_NAME, LogWidget


@pytest.fixture
def log(qtbot):
    widget = LogWidget(source="run.single")
    qtbot.addWidget(widget)
    return widget


def _records(caplog) -> list[tuple[int, str]]:
    return [(record.levelno, record.getMessage()) for record in caplog.records if record.name == ACTIVITY_LOGGER_NAME]


class TestMirroredLines:
    def test_info_and_error_reach_the_file_log_with_the_source(self, log, caplog):
        with caplog.at_level(logging.DEBUG, logger=ACTIVITY_LOGGER_NAME):
            log.append_info("Mining 3 items")
            log.append_error("Failed: ep01")

        assert _records(caplog) == [
            (logging.INFO, "[run.single] Mining 3 items"),
            (logging.ERROR, "[run.single] Failed: ep01"),
        ]

    def test_success_is_info_and_warning_is_warning(self, log, caplog):
        with caplog.at_level(logging.DEBUG, logger=ACTIVITY_LOGGER_NAME):
            log.append_success("created 12 cards")
            log.append_warning("no pitch accent for 走る")

        assert _records(caplog) == [
            (logging.INFO, "[run.single] created 12 cards"),
            (logging.WARNING, "[run.single] no pitch accent for 走る"),
        ]

    def test_a_sourceless_widget_logs_the_bare_text(self, qtbot, caplog):
        widget = LogWidget()
        qtbot.addWidget(widget)

        with caplog.at_level(logging.DEBUG, logger=ACTIVITY_LOGGER_NAME):
            widget.append_info("parsed subtitles")

        assert _records(caplog) == [(logging.INFO, "parsed subtitles")]

    def test_set_log_source_applies_to_later_lines(self, qtbot, caplog):
        widget = LogWidget()
        qtbot.addWidget(widget)
        widget.set_log_source("tools.condense")

        with caplog.at_level(logging.DEBUG, logger=ACTIVITY_LOGGER_NAME):
            widget.append_info("condensing")

        assert _records(caplog) == [(logging.INFO, "[tools.condense] condensing")]


class TestNoDuplicateEmission:
    def test_rotation_does_not_re_emit_retained_entries(self, log, caplog):
        total = LogWidget.MAX_LINES + 50

        with caplog.at_level(logging.DEBUG, logger=ACTIVITY_LOGGER_NAME):
            for index in range(total):
                log.append_info(f"line {index}")

        records = _records(caplog)
        assert len(records) == total
        assert records[0] == (logging.INFO, "[run.single] line 0")
        assert records[-1] == (logging.INFO, f"[run.single] line {total - 1}")
        # The widget itself dropped the oldest entries; the file log kept them.
        assert len(log._entries) <= LogWidget.MAX_LINES

    def test_clear_log_emits_nothing(self, log, caplog):
        log.append_info("Mining 3 items")

        with caplog.at_level(logging.DEBUG, logger=ACTIVITY_LOGGER_NAME):
            log.clear_log()

        assert _records(caplog) == []
