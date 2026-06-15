"""Tests: Browse dialog opens at a sensible start directory, never at '/' or ''."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtWidgets import QFileDialog

from anki_miner.config.paths import ANKI_MINER_HOME
from anki_miner.gui.widgets.enhanced.file_selector import FileSelector

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_selector(qtbot, **kwargs) -> FileSelector:
    w = FileSelector(**kwargs)
    qtbot.addWidget(w)
    return w


# ---------------------------------------------------------------------------
# file_mode=True, no path set, no default_dir → home
# ---------------------------------------------------------------------------


def test_file_mode_no_path_no_default_opens_at_home(qtbot, monkeypatch):
    w = _make_selector(qtbot, file_mode=True)
    captured: dict[str, str] = {}

    def fake_open(*a, **kw):
        captured["dir"] = a[2]
        return ("", "")

    monkeypatch.setattr(QFileDialog, "getOpenFileName", fake_open)
    w.browse()

    assert captured["dir"] == str(Path.home())


# ---------------------------------------------------------------------------
# file_mode=False, default_dir=tmp_path (existing), no path set → tmp_path
# ---------------------------------------------------------------------------


def test_folder_mode_with_default_dir_opens_at_default(qtbot, monkeypatch, tmp_path):
    w = _make_selector(qtbot, file_mode=False, default_dir=tmp_path)
    captured: dict[str, str] = {}

    def fake_existing(*a, **kw):
        captured["dir"] = a[2]
        return ""

    monkeypatch.setattr(QFileDialog, "getExistingDirectory", fake_existing)
    w.browse()

    assert captured["dir"] == str(tmp_path)


# ---------------------------------------------------------------------------
# file_mode=True, field has an existing file → parent dir
# ---------------------------------------------------------------------------


def test_file_mode_with_existing_file_opens_at_parent(qtbot, monkeypatch, tmp_path):
    the_file = tmp_path / "subtitle.srt"
    the_file.touch()

    w = _make_selector(qtbot, file_mode=True)
    w.set_path(str(the_file))

    captured: dict[str, str] = {}

    def fake_open(*a, **kw):
        captured["dir"] = a[2]
        return ("", "")

    monkeypatch.setattr(QFileDialog, "getOpenFileName", fake_open)
    w.browse()

    assert captured["dir"] == str(tmp_path)


# ---------------------------------------------------------------------------
# Start dir is never '' or '/'
# ---------------------------------------------------------------------------


def test_start_dir_never_empty_or_root_file_mode(qtbot, monkeypatch):
    w = _make_selector(qtbot, file_mode=True)
    captured: dict[str, str] = {}

    def fake_open(*a, **kw):
        captured["dir"] = a[2]
        return ("", "")

    monkeypatch.setattr(QFileDialog, "getOpenFileName", fake_open)
    w.browse()

    assert captured["dir"] != ""
    assert captured["dir"] != "/"


def test_start_dir_never_empty_or_root_folder_mode(qtbot, monkeypatch):
    w = _make_selector(qtbot, file_mode=False)
    captured: dict[str, str] = {}

    def fake_existing(*a, **kw):
        captured["dir"] = a[2]
        return ""

    monkeypatch.setattr(QFileDialog, "getExistingDirectory", fake_existing)
    w.browse()

    assert captured["dir"] != ""
    assert captured["dir"] != "/"


# ---------------------------------------------------------------------------
# Panel wiring: default_dir stored correctly on FileSelector
# ---------------------------------------------------------------------------


def test_default_dir_stored_on_widget(qtbot):
    expected = ANKI_MINER_HOME / "dicts"
    w = _make_selector(qtbot, file_mode=False, default_dir=expected)
    assert w._default_dir == expected
