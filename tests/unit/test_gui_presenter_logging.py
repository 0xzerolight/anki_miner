"""Log-record tests for the Qt presenter and progress-callback bridges.

Both bridges are the last hop before a message reaches a screen, so a presenter
line in the log with no matching activity line proves the signal was emitted but
never rendered. The levels encode who the message is for: a warning or an error
changes what the user gets and is logged as such, while informational and stage
chatter is DEBUG because it is cosmetic narration of a run that is otherwise
already summarized.

``NullPresenter`` deliberately has no counterpart here: it is a silent test
double with no production caller, and logging from it would attribute run
diagnostics to tests.
"""

from __future__ import annotations

import logging

from anki_miner.gui.presenters.gui_presenter import GUIPresenter
from anki_miner.gui.presenters.gui_progress_callback import GUIProgressCallback

_PRESENTER_LOGGER = "anki_miner.gui.presenters.gui_presenter"
_CALLBACK_LOGGER = "anki_miner.gui.presenters.gui_progress_callback"


# ===========================================================================
# GUIPresenter
# ===========================================================================


def test_show_warning_logs_at_warning(qapp, caplog):
    p = GUIPresenter()

    with caplog.at_level(logging.DEBUG, logger=_PRESENTER_LOGGER):
        p.show_warning("No audio track matched ja")

    (record,) = [r for r in caplog.records if r.name == _PRESENTER_LOGGER]
    assert record.levelno == logging.WARNING
    assert record.getMessage() == 'Presenter warning: message="No audio track matched ja"'


def test_show_error_logs_at_error(qapp, caplog):
    p = GUIPresenter()

    with caplog.at_level(logging.DEBUG, logger=_PRESENTER_LOGGER):
        p.show_error("AnkiConnect refused the note")

    (record,) = [r for r in caplog.records if r.name == _PRESENTER_LOGGER]
    assert record.levelno == logging.ERROR
    assert record.getMessage() == 'Presenter error: message="AnkiConnect refused the note"'


def test_show_info_logs_at_debug(qapp, caplog):
    p = GUIPresenter()

    with caplog.at_level(logging.DEBUG, logger=_PRESENTER_LOGGER):
        p.show_info("Loaded 3 dictionaries")

    (record,) = [r for r in caplog.records if r.name == _PRESENTER_LOGGER]
    assert record.levelno == logging.DEBUG
    assert record.getMessage() == 'Presenter info: message="Loaded 3 dictionaries"'


def test_show_success_logs_at_debug(qapp, caplog):
    p = GUIPresenter()

    with caplog.at_level(logging.DEBUG, logger=_PRESENTER_LOGGER):
        p.show_success("Created 12 cards")

    (record,) = [r for r in caplog.records if r.name == _PRESENTER_LOGGER]
    assert record.levelno == logging.DEBUG
    assert record.getMessage() == 'Presenter success: message="Created 12 cards"'


def test_show_stage_logs_index_total_and_name(qapp, caplog):
    p = GUIPresenter()

    with caplog.at_level(logging.DEBUG, logger=_PRESENTER_LOGGER):
        p.show_stage(2, 5, "Extracting media")

    (record,) = [r for r in caplog.records if r.name == _PRESENTER_LOGGER]
    assert record.levelno == logging.DEBUG
    assert record.getMessage() == 'Presenter stage: index=2 total=5 name="Extracting media"'


def test_show_stage_does_not_also_log_a_presenter_info_line(qapp, caplog):
    """The stage line rides ``info_signal``, but must not log twice.

    ``show_stage`` emits the signal itself rather than calling ``show_info``;
    a second ``Presenter info:`` record here would mean the two were wired
    together and every stage would be double-counted in the log.
    """
    p = GUIPresenter()

    with caplog.at_level(logging.DEBUG, logger=_PRESENTER_LOGGER):
        p.show_stage(1, 5, "Parsing subtitles")

    messages = [r.getMessage() for r in caplog.records if r.name == _PRESENTER_LOGGER]
    assert len(messages) == 1
    assert not any(m.startswith("Presenter info:") for m in messages)


def test_presenter_records_are_attributed_to_the_caller_line(qapp, caplog):
    """``log_summary(stacklevel=2)`` must resolve to the presenter method."""
    p = GUIPresenter()

    with caplog.at_level(logging.DEBUG, logger=_PRESENTER_LOGGER):
        p.show_warning("boom")

    (record,) = [r for r in caplog.records if r.name == _PRESENTER_LOGGER]
    assert record.funcName == "show_warning"


# ===========================================================================
# GUIProgressCallback
# ===========================================================================


def test_on_error_logs_item_and_error_at_warning(qapp, caplog):
    cb = GUIProgressCallback()

    with caplog.at_level(logging.DEBUG, logger=_CALLBACK_LOGGER):
        cb.on_error("ep01", "boom")

    (record,) = [r for r in caplog.records if r.name == _CALLBACK_LOGGER]
    assert record.levelno == logging.WARNING
    assert record.getMessage() == "Pipeline item error: item=ep01 error=boom"


def test_on_error_quotes_values_containing_whitespace(qapp, caplog):
    cb = GUIProgressCallback()

    with caplog.at_level(logging.DEBUG, logger=_CALLBACK_LOGGER):
        cb.on_error("Show S01E02", "ffmpeg exited 1")

    (record,) = [r for r in caplog.records if r.name == _CALLBACK_LOGGER]
    assert record.getMessage() == ('Pipeline item error: item="Show S01E02" error="ffmpeg exited 1"')


def test_progress_callback_success_path_stays_silent(qapp, caplog):
    """Per-item progress is a hot loop; only failures earn a record."""
    cb = GUIProgressCallback()

    with caplog.at_level(logging.DEBUG, logger=_CALLBACK_LOGGER):
        cb.on_start(3, "Mining")
        cb.on_progress(1, "ep01")
        cb.on_stage(1, 5, "Parsing subtitles")
        cb.on_complete()

    assert [r for r in caplog.records if r.name == _CALLBACK_LOGGER] == []


def test_on_error_still_emits_its_signal(qapp, caplog):
    """Logging is additive: the Qt signal contract is unchanged."""
    cb = GUIProgressCallback()
    seen: list[tuple[str, str]] = []
    cb.error_signal.connect(lambda item, msg: seen.append((item, msg)))

    with caplog.at_level(logging.DEBUG, logger=_CALLBACK_LOGGER):
        cb.on_error("ep01", "boom")

    assert seen == [("ep01", "boom")]
