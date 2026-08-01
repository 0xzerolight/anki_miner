"""mpv warn-level log dedup on SubtitlePlayerWidget (Issue #100 log spam)."""

import logging

from anki_miner.gui.widgets.subtitle_player_widget import SubtitlePlayerWidget

LOGGER = "anki_miner.gui.widgets.subtitle_player_widget"


def _widget(qtbot):
    w = SubtitlePlayerWidget()
    qtbot.addWidget(w)
    return w


def test_identical_error_burst_collapses(qtbot, caplog):
    w = _widget(qtbot)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        for _ in range(100):
            w._on_mpv_log("error", "libmpv_render", "shader link failed")

    records = [r for r in caplog.records if "shader link failed" in r.message]
    assert len(records) == 1


def test_benign_texture_error_is_debug_not_warning(qtbot, caplog):
    """The stale-glGetError line is measured-benign; it must not read as a warning.

    Fires once per video load on plane 0 only, identically on 8-bit and 10-bit
    and under an explicit CoreProfile context. Still logged, at DEBUG.
    """
    w = _widget(qtbot)
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        w._on_mpv_log("error", "libmpv_render", "after creating texture: OpenGL error INVALID_ENUM.")

    records = [r for r in caplog.records if "INVALID_ENUM" in r.message]
    assert [r.levelno for r in records] == [logging.DEBUG]


def test_other_libmpv_render_errors_still_warn(qtbot, caplog):
    """The suppression is one exact message, not the whole component."""
    w = _widget(qtbot)
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        w._on_mpv_log("error", "libmpv_render", "after creating texture: OpenGL error OUT_OF_MEMORY.")

    records = [r for r in caplog.records if "OUT_OF_MEMORY" in r.message]
    assert [r.levelno for r in records] == [logging.WARNING]


def test_repeat_count_reported_after_window(qtbot, caplog, monkeypatch):
    w = _widget(qtbot)
    clock = iter([0.0] + [1.0] * 99 + [1000.0])
    monkeypatch.setattr("anki_miner.gui.widgets.subtitle_player_widget.time.monotonic", lambda: next(clock))
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        for _ in range(101):
            w._on_mpv_log("error", "libmpv_render", "boom")

    records = [r for r in caplog.records if "boom" in r.message]
    assert len(records) == 2
    assert "repeated 99 times" in records[1].message


def test_different_messages_unaffected(qtbot, caplog):
    w = _widget(qtbot)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        w._on_mpv_log("error", "libmpv_render", "error A")
        w._on_mpv_log("error", "libmpv_render", "error B")
        w._on_mpv_log("error", "other_component", "error A")

    assert len([r for r in caplog.records if "error A" in r.message or "error B" in r.message]) == 3


def test_debug_passthrough_untouched(qtbot, caplog):
    w = _widget(qtbot)
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        for _ in range(5):
            w._on_mpv_log("warn", "cplayer", "harmless")

    assert len([r for r in caplog.records if "harmless" in r.message]) == 5
