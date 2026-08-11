"""System Health: the permanent readiness screen (decision D26).

Two things are being pinned here.

*Unknown is not failure.* Validation skips the deck, note-type and field
checks entirely when AnkiConnect is unreachable, so their ``False`` booleans
mean "never asked", and painting them red turns one failure into four. The same
goes for the window before any sweep has reported and for a sweep that itself
errored.

*The window observes.* It is created once, hidden rather than destroyed on
close, seeded from the report the main window keeps, and owns no worker — so a
result landing while it is closed is still there when it reopens, and closing it
cancels nothing.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from anki_miner.gui.widgets.dialogs.system_health_window import (
    HEALTH_FAIL,
    HEALTH_FIX_ANCHORS,
    HEALTH_KEYS,
    HEALTH_OK,
    HEALTH_UNKNOWN,
    HEALTH_WARN,
    HealthReport,
    SystemHealthWindow,
    checks_from_validation,
)
from anki_miner.models import ValidationIssue, ValidationResult

CHECKED_AT = datetime(2026, 7, 27, 14, 32)


def _result(*, ankiconnect_ok=True, deck=True, note_type=True, issues=None, versions=None) -> ValidationResult:
    return ValidationResult(
        ankiconnect_ok=ankiconnect_ok,
        ffmpeg_ok=True,
        deck_exists=deck,
        note_type_exists=note_type,
        field_mapping_ok=ankiconnect_ok and note_type,
        issues=list(issues or []),
        tool_versions=dict(versions or {}),
    )


# ---------------------------------------------------------------------------
# Derivation
# ---------------------------------------------------------------------------


def test_healthy_sweep_marks_every_row_ready():
    checks = checks_from_validation(_result(), CHECKED_AT)

    assert {check.state for check in checks.values()} == {HEALTH_OK}
    assert all(check.checked_at == CHECKED_AT for check in checks.values())


def test_unreachable_anki_leaves_dependent_rows_unknown():
    """One failure, not four: deck/note type/fields were never asked."""
    result = _result(
        ankiconnect_ok=False,
        deck=False,
        note_type=False,
        issues=[ValidationIssue(component="AnkiConnect", severity="ERROR", message="Cannot connect to Anki.")],
    )

    checks = checks_from_validation(result, CHECKED_AT)

    assert checks["anki.connect"].state == HEALTH_FAIL
    assert checks["anki.deck"].state == HEALTH_UNKNOWN
    assert checks["anki.note_type"].state == HEALTH_UNKNOWN
    assert checks["anki.fields"].state == HEALTH_UNKNOWN
    # An unknown row carries no timestamp: nothing was learnt about it.
    assert checks["anki.deck"].checked_at is None


def test_missing_note_type_leaves_the_field_row_unknown():
    result = _result(
        note_type=False,
        issues=[ValidationIssue(component="Note Type", severity="ERROR", message="Note type 'X' not found.")],
    )

    checks = checks_from_validation(result, CHECKED_AT)

    assert checks["anki.note_type"].state == HEALTH_FAIL
    assert checks["anki.fields"].state == HEALTH_UNKNOWN


def test_warnings_and_errors_map_to_distinct_states():
    result = _result(
        issues=[
            ValidationIssue(component="ffmpeg", severity="ERROR", message="ffmpeg not found"),
            ValidationIssue(component="yt-dlp", severity="WARNING", message="yt-dlp not found"),
        ]
    )

    checks = checks_from_validation(result, CHECKED_AT)

    assert checks["tools.ffmpeg"].state == HEALTH_FAIL
    assert checks["tools.ytdlp"].state == HEALTH_WARN
    assert checks["tools.ytdlp"].detail == "yt-dlp not found"


def test_passing_tool_rows_carry_the_version_the_sweep_already_resolved():
    result = _result(versions={"yt-dlp": "2026.07.01 [bundled]", "offline-dictionary": "JMdict (200,000 entries)"})

    checks = checks_from_validation(result, CHECKED_AT)

    assert checks["tools.ytdlp"].detail == "2026.07.01 [bundled]"
    assert checks["resources.dictionary"].detail == "JMdict (200,000 entries)"


def test_only_the_first_message_per_component_is_shown():
    """yt-dlp can report both "missing" and "stale"; the row is not a log."""
    result = _result(
        issues=[
            ValidationIssue(component="yt-dlp", severity="WARNING", message="first"),
            ValidationIssue(component="yt-dlp", severity="WARNING", message="second"),
        ]
    )

    assert checks_from_validation(result, CHECKED_AT)["tools.ytdlp"].detail == "first"


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def test_initial_report_is_unknown_everywhere():
    report = HealthReport.unknown()

    assert [report.get(key).state for key in HEALTH_KEYS] == [HEALTH_UNKNOWN] * len(HEALTH_KEYS)
    assert report.error == ""


def test_a_starting_probe_returns_rows_to_unknown_but_keeps_the_update_row():
    report = HealthReport.unknown().with_validation(_result(), CHECKED_AT)
    report = report.with_update_check(state=HEALTH_OK, detail="Running 2.8.4.", checked_at=CHECKED_AT)

    checking = report.checking()

    assert checking.get("anki.connect").state == HEALTH_UNKNOWN
    # A validation sweep says nothing about updates, so it must not blank them.
    assert checking.get("app.updates").state == HEALTH_OK


def test_a_failed_sweep_reports_unknown_not_broken():
    report = HealthReport.unknown().with_validation(_result(), CHECKED_AT)

    failed = report.with_validation_error("worker exploded")

    assert failed.error == "worker exploded"
    assert failed.get("tools.ffmpeg").state == HEALTH_UNKNOWN


def test_every_fix_anchor_names_a_row_that_exists():
    assert set(HEALTH_FIX_ANCHORS) <= set(HEALTH_KEYS)


# ---------------------------------------------------------------------------
# Window
# ---------------------------------------------------------------------------


@pytest.fixture
def health_window(qtbot):
    window = SystemHealthWindow()
    qtbot.addWidget(window)
    return window


def test_window_starts_with_no_fix_buttons_offered(health_window):
    """Before anything is checked there is nothing to repair."""
    assert not any(row.fix_button.isVisible() for row in health_window._rows.values())


def test_fix_button_appears_only_for_rows_with_a_repair_route(health_window):
    result = _result(
        issues=[
            ValidationIssue(component="ffmpeg", severity="ERROR", message="ffmpeg not found"),
            ValidationIssue(component="Offline Dictionary", severity="WARNING", message="none configured"),
        ]
    )
    health_window.show()

    health_window.show_health(HealthReport.unknown().with_validation(result, CHECKED_AT))

    # ffmpeg is resolved from PATH or the bundle and has no setting to jump to.
    assert not health_window._rows["tools.ffmpeg"].fix_button.isVisible()
    assert health_window._rows["resources.dictionary"].fix_button.isVisible()
    # A healthy row offers nothing either.
    assert not health_window._rows["anki.deck"].fix_button.isVisible()


def test_fix_emits_the_stable_setting_anchor_id(health_window, qtbot):
    result = _result(
        ankiconnect_ok=False,
        deck=False,
        note_type=False,
        issues=[ValidationIssue(component="AnkiConnect", severity="ERROR", message="down")],
    )
    health_window.show_health(HealthReport.unknown().with_validation(result, CHECKED_AT))

    with qtbot.waitSignal(health_window.fix_requested) as blocker:
        health_window._rows["anki.connect"].fix_button.click()

    assert blocker.args == [HEALTH_FIX_ANCHORS["anki.connect"]]


def test_row_shows_when_it_was_checked(health_window):
    health_window.show_health(HealthReport.unknown().with_validation(_result(), CHECKED_AT))

    assert "14:32" in health_window._rows["anki.connect"].checked_label.text()


def test_unchecked_row_says_so_rather_than_showing_a_time(health_window):
    text = health_window._rows["app.updates"].checked_label.text()

    assert text == "Not checked yet"


def test_sweep_error_is_shown_and_then_cleared(health_window):
    health_window.show_health(HealthReport.unknown().with_validation_error("boom"))
    assert health_window.error_label.text() == "boom"

    health_window.show_health(HealthReport.unknown().with_validation(_result(), CHECKED_AT))
    assert health_window.error_label.text() == ""


# ---------------------------------------------------------------------------
# Main-window ownership
# ---------------------------------------------------------------------------


@pytest.fixture
def main_window(qtbot, patch_heavy_init, test_config):
    # _run_validation stays real: the "a probe in flight is not a failure" rule
    # is implemented there. Nothing calls it during construction.
    patch_heavy_init(test_config, stub_run_validation=False)
    from anki_miner.gui.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)
    yield window
    window.deleteLater()


def test_status_control_opens_system_health(main_window, qtbot):
    main_window.status_bar.system_status_clicked.emit()

    assert main_window._system_health_window is not None
    qtbot.addWidget(main_window._system_health_window)
    assert main_window._system_health_window.isVisible()


def test_reopening_reuses_the_same_modeless_window(main_window, qtbot):
    main_window.open_system_health()
    first = main_window._system_health_window
    qtbot.addWidget(first)
    assert first is not None
    assert not first.isModal()

    first.close()
    assert not first.isVisible()

    main_window.open_system_health()

    assert main_window._system_health_window is first
    assert first.isVisible()


def test_a_result_that_lands_while_closed_is_there_on_reopen(main_window, qtbot):
    result = _result(
        issues=[ValidationIssue(component="Offline Dictionary", severity="WARNING", message="none configured")]
    )

    main_window._on_validation_result(result)
    main_window.open_system_health()
    window = main_window._system_health_window
    assert window is not None
    qtbot.addWidget(window)

    assert window._rows["resources.dictionary"].detail_label.text() == "none configured"


def test_starting_a_probe_returns_rows_and_badges_to_checking(main_window, qtbot, monkeypatch):
    main_window._on_validation_result(_result())
    main_window.open_system_health()
    window = main_window._system_health_window
    assert window is not None
    qtbot.addWidget(window)
    monkeypatch.setattr(main_window.background_tasks, "start_validation", lambda service: True)

    main_window._run_validation()

    assert main_window._health_report.get("anki.connect").state == HEALTH_UNKNOWN
    assert main_window.status_bar.anki_status_badge.status == "checking"


def test_closing_the_app_takes_system_health_with_it(main_window, qtbot, monkeypatch):
    """Qt counts it as a window: left open, it keeps a dead app alive."""
    from PyQt6.QtGui import QCloseEvent

    main_window.open_system_health()
    window = main_window._system_health_window
    assert window is not None
    qtbot.addWidget(window)
    monkeypatch.setattr(main_window.background_tasks, "shutdown", lambda tabs: [])

    main_window.closeEvent(QCloseEvent())

    assert not window.isVisible()


def test_closing_the_screen_cancels_nothing(main_window, qtbot, monkeypatch):
    """The window observes a run; it never owns one."""
    monkeypatch.setattr(main_window.background_tasks, "start_validation", lambda service: True)
    cancelled: list[str] = []
    monkeypatch.setattr(
        main_window.background_tasks,
        "cancel_jmdict_migration",
        lambda: cancelled.append("jmdict"),
    )
    main_window.open_system_health()
    window = main_window._system_health_window
    assert window is not None
    qtbot.addWidget(window)
    main_window._run_validation()

    window.close()

    assert cancelled == []
    # The sweep it was watching is still in flight, and still says so.
    assert main_window._health_report.get("anki.connect").state == HEALTH_UNKNOWN


def test_reveal_setting_asks_settings_to_jump(main_window, qtbot):
    from anki_miner.gui.widgets.settings_tab import SettingsTab

    settings_tab = SettingsTab(main_window.get_config())
    qtbot.addWidget(settings_tab)
    main_window.tabs.addTab(settings_tab, "Settings")
    jumped: list[str] = []
    settings_tab.jump_to_setting = jumped.append  # type: ignore[method-assign]

    main_window.reveal_setting("dictionaries.chain")

    assert jumped == ["dictionaries.chain"]
    assert main_window.tabs.currentWidget() is settings_tab


def test_reveal_setting_is_a_no_op_without_a_settings_tab(main_window):
    main_window.reveal_setting("dictionaries.chain")  # must not raise


def test_every_fix_button_lands_on_a_real_control(qtbot, test_config):
    """A Fix deep link that Settings cannot resolve is a button that does nothing.

    ``jump_to_setting`` ignores unknown ids by design, so a typo or a renamed
    anchor degrades to silence rather than to a crash — which is exactly why it
    needs a test that the ids are real.
    """
    from anki_miner.gui.widgets.settings_tab import SettingsTab

    settings_tab = SettingsTab(test_config)
    qtbot.addWidget(settings_tab)
    known = {anchor.stable_id for anchor in settings_tab.setting_anchors()}

    assert set(HEALTH_FIX_ANCHORS.values()) <= known
