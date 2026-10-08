"""The startup check opens Anki when the user turned that on (auto_open_anki)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from anki_miner.models import ValidationIssue, ValidationResult


def _result(*, ankiconnect_ok: bool) -> ValidationResult:
    issues = [] if ankiconnect_ok else [ValidationIssue(component="AnkiConnect", severity="ERROR", message="down")]
    return ValidationResult(
        ankiconnect_ok=ankiconnect_ok,
        ffmpeg_ok=True,
        deck_exists=True,
        note_type_exists=True,
        field_mapping_ok=True,
        issues=issues,
    )


@pytest.fixture
def make_window(patch_heavy_init, test_config, qtbot, monkeypatch):
    def _make(*, auto_open: bool):
        patch_heavy_init(replace(test_config, auto_open_anki=auto_open))
        from anki_miner.gui.main_window import MainWindow

        window = MainWindow()
        qtbot.addWidget(window)
        opened: list[bool] = []
        monkeypatch.setattr(window._anki_auto_opener, "open", lambda: opened.append(True))
        return window, opened

    return _make


def test_startup_check_opens_anki_when_it_is_closed(make_window):
    window, opened = make_window(auto_open=True)
    window._validation_silent = True

    window._on_validation_result(_result(ankiconnect_ok=False))

    assert opened == [True]


def test_setting_off_never_opens_anki(make_window):
    window, opened = make_window(auto_open=False)
    window._validation_silent = True

    window._on_validation_result(_result(ankiconnect_ok=False))

    assert opened == []


def test_user_requested_check_never_opens_anki(make_window):
    window, opened = make_window(auto_open=True)
    window._validation_silent = False  # Refresh / Re-check

    window._on_validation_result(_result(ankiconnect_ok=False))

    assert opened == []


def test_reachable_anki_is_left_alone(make_window):
    window, opened = make_window(auto_open=True)
    window._validation_silent = True

    window._on_validation_result(_result(ankiconnect_ok=True))

    assert opened == []


def test_anki_answering_reruns_the_checks_quietly(make_window, monkeypatch):
    window, _opened = make_window(auto_open=True)
    window._validation_silent = False
    runs: list[bool] = []
    monkeypatch.setattr(window, "_run_validation", lambda: runs.append(window._validation_silent))

    window._on_anki_auto_opened()

    assert runs == [True]
