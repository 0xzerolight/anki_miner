"""Tests for FileSelector's ``optional`` flag (Issue #100).

An optional resource (e.g. the pitch accent CSV) whose path is absent on a
clean install must render the neutral "Not installed" state, not the red
error border — while still reporting invalid through ``path_validated`` /
``is_valid`` so caller logic is unchanged.
"""

from pathlib import Path

from anki_miner.gui.widgets.enhanced.file_selector import FileSelector


def _missing(tmp_path: Path) -> str:
    return str(tmp_path / "does_not_exist.csv")


def test_optional_missing_file_is_neutral_not_error(qtbot, tmp_path):
    sel = FileSelector(label="", file_mode=True, optional=True)
    qtbot.addWidget(sel)

    sel.set_path(_missing(tmp_path))

    assert sel.input.property("error") is False
    assert sel.input.property("success") is False
    assert sel.status_label.text() == "Not installed"


def test_optional_still_reports_invalid(qtbot, tmp_path):
    sel = FileSelector(label="", file_mode=True, optional=True)
    qtbot.addWidget(sel)

    validated: list[tuple[bool, str]] = []
    sel.path_validated.connect(lambda ok, path: validated.append((ok, path)))

    sel.set_path(_missing(tmp_path))

    assert validated and validated[-1][0] is False
    assert sel.is_valid() is False


def test_optional_existing_file_is_success(qtbot, tmp_path):
    existing = tmp_path / "pitch_accent.csv"
    existing.write_text("reading,kanji,pattern\n", encoding="utf-8")
    sel = FileSelector(label="", file_mode=True, optional=True)
    qtbot.addWidget(sel)

    sel.set_path(str(existing))

    assert sel.input.property("error") is False
    assert sel.input.property("success") is True
    assert sel.is_valid() is True


def test_default_selector_missing_file_stays_error(qtbot, tmp_path):
    # Regression: the flag must not soften the default (required) behavior.
    sel = FileSelector(label="", file_mode=True)
    qtbot.addWidget(sel)

    sel.set_path(_missing(tmp_path))

    assert sel.input.property("error") is True
    assert sel.status_label.text() == "File not found"
