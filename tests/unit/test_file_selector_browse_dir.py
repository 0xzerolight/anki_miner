"""Tests: Browse dialog opens at a sensible start directory, never at '/' or ''."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QMimeData, QPointF, Qt, QUrl
from PyQt6.QtGui import QDropEvent
from PyQt6.QtWidgets import QFileDialog

from anki_miner.config.paths import ANKI_MINER_HOME
from anki_miner.gui.utils import session_state
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


# ---------------------------------------------------------------------------
# history_key: only an ACCEPTED dialog moves the remembered folder (D7)
# ---------------------------------------------------------------------------


def _accept_file(monkeypatch, path: Path) -> None:
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **kw: (str(path), ""))


def _accept_folder(monkeypatch, path: Path) -> None:
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **kw: str(path))


def test_no_history_key_records_nothing(qtbot, monkeypatch, tmp_path):
    """Call sites without a key keep today's behaviour exactly."""
    chosen = tmp_path / "ep.mkv"
    chosen.touch()
    w = _make_selector(qtbot, file_mode=True)
    _accept_file(monkeypatch, chosen)

    w.browse()

    assert session_state.remembered_directory("video.single.inputs") is None


def test_accepting_a_file_remembers_its_parent(qtbot, monkeypatch, tmp_path):
    chosen = tmp_path / "season" / "ep.mkv"
    chosen.parent.mkdir()
    chosen.touch()
    w = _make_selector(qtbot, file_mode=True, history_key="video.single.inputs")
    _accept_file(monkeypatch, chosen)

    w.browse()

    assert session_state.remembered_directory("video.single.inputs") == str(chosen.parent)


def test_accepting_a_folder_remembers_that_folder(qtbot, monkeypatch, tmp_path):
    chosen = tmp_path / "library"
    chosen.mkdir()
    w = _make_selector(qtbot, file_mode=False, history_key="reading.manga.inputs")
    _accept_folder(monkeypatch, chosen)

    w.browse()

    assert session_state.remembered_directory("reading.manga.inputs") == str(chosen)


def test_cancelling_records_nothing(qtbot, monkeypatch, tmp_path):
    seed = tmp_path / "seed"
    seed.mkdir()
    session_state.remember_accepted_path("reading.manga.inputs", str(seed), file_mode=False)
    w = _make_selector(qtbot, file_mode=False, history_key="reading.manga.inputs")
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **kw: "")

    w.browse()

    assert session_state.remembered_directory("reading.manga.inputs") == str(seed)


def test_typing_a_path_records_nothing(qtbot, tmp_path):
    typed = tmp_path / "typed"
    typed.mkdir()
    w = _make_selector(qtbot, file_mode=False, history_key="reading.novels.inputs")

    w.input.setText(str(typed))

    assert session_state.remembered_directory("reading.novels.inputs") is None


def test_set_path_records_nothing(qtbot, tmp_path):
    """Auto-fill (episode pairing, a recents pick) is not a choice of folder."""
    filled = tmp_path / "auto"
    filled.mkdir()
    w = _make_selector(qtbot, file_mode=False, history_key="reading.novels.inputs")

    w.set_path(str(filled))

    assert session_state.remembered_directory("reading.novels.inputs") is None


def test_dropping_a_path_records_nothing(qtbot, tmp_path):
    dropped = tmp_path / "dropped"
    dropped.mkdir()
    w = _make_selector(qtbot, file_mode=False, history_key="reading.novels.inputs")
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(dropped))])
    event = QDropEvent(
        QPointF(1.0, 1.0),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )

    w.dropEvent(event)

    assert w.get_path() == str(dropped)  # vacuity guard: the drop did land
    assert session_state.remembered_directory("reading.novels.inputs") is None


def test_browse_reopens_in_the_remembered_folder(qtbot, monkeypatch, tmp_path):
    remembered = tmp_path / "deep" / "library"
    remembered.mkdir(parents=True)
    session_state.remember_accepted_path("reading.manga.inputs", str(remembered), file_mode=False)
    w = _make_selector(qtbot, file_mode=False, history_key="reading.manga.inputs")
    captured: dict[str, str] = {}

    def fake_existing(*a, **kw):
        captured["dir"] = a[2]
        return ""

    monkeypatch.setattr(QFileDialog, "getExistingDirectory", fake_existing)
    w.browse()

    assert captured["dir"] == str(remembered)


def test_a_field_path_still_outranks_the_remembered_folder(qtbot, monkeypatch, tmp_path):
    remembered = tmp_path / "old"
    current = tmp_path / "current"
    remembered.mkdir()
    current.mkdir()
    session_state.remember_accepted_path("reading.manga.inputs", str(remembered), file_mode=False)
    w = _make_selector(qtbot, file_mode=False, history_key="reading.manga.inputs")
    w.set_path(str(current))
    captured: dict[str, str] = {}

    def fake_existing(*a, **kw):
        captured["dir"] = a[2]
        return ""

    monkeypatch.setattr(QFileDialog, "getExistingDirectory", fake_existing)
    w.browse()

    assert captured["dir"] == str(current)


def test_a_trailing_space_folder_is_never_stripped(qtbot, monkeypatch, tmp_path):
    """.strip() on a filesystem-bound path is the batch-mining core dump."""
    chosen = tmp_path / "spaced "
    chosen.mkdir()
    w = _make_selector(qtbot, file_mode=False, history_key="video.batch.inputs")
    _accept_folder(monkeypatch, chosen)

    w.browse()

    assert session_state.remembered_directory("video.batch.inputs") == str(chosen)
