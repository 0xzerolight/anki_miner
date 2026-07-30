"""Tests for the machine-local UI session store (``ui_state.ini``)."""

from __future__ import annotations

from pathlib import Path

import pytest
from PyQt6.QtCore import QByteArray, QSettings

from anki_miner.gui.utils import session_state
from anki_miner.gui.utils.config_manager import GUIConfigManager


@pytest.fixture
def state_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ``GUIConfigManager.CONFIG_FILE`` at a throwaway home."""
    monkeypatch.setattr(GUIConfigManager, "CONFIG_FILE", tmp_path / "gui_config.json")
    return tmp_path


# ---------------------------------------------------------------------------
# Where the file lives
# ---------------------------------------------------------------------------


def test_state_file_follows_config_file_at_call_time(state_home: Path, tmp_path_factory) -> None:
    """The INI path is derived per call, never snapshotted at import."""
    assert session_state.state_file() == state_home / "ui_state.ini"

    moved = tmp_path_factory.mktemp("moved")
    GUIConfigManager.CONFIG_FILE = moved / "gui_config.json"
    assert session_state.state_file() == moved / "ui_state.ini"


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------


def test_geometry_round_trips(state_home: Path) -> None:
    blob = QByteArray(b"\x01\x02\x03geometry")
    session_state.save_geometry(blob)
    assert session_state.load_geometry() == blob


def test_geometry_absent_reads_none(state_home: Path) -> None:
    assert session_state.load_geometry() is None


def test_non_blob_geometry_reads_none(state_home: Path) -> None:
    """A hand-edited INI holding plain text is reported as absent, not raised."""
    (state_home / "ui_state.ini").write_text("[window]\ngeometry=not-a-blob\n", encoding="utf-8")
    assert session_state.load_geometry() is None


# ---------------------------------------------------------------------------
# Word curator layout
# ---------------------------------------------------------------------------


def test_curator_layout_round_trips(state_home: Path) -> None:
    session_state.save_curator_layout(
        QByteArray(b"geo"), QByteArray(b"main"), QByteArray(b"side"), side_key="player+dict"
    )
    assert session_state.load_curator_layout("player+dict") == (
        QByteArray(b"geo"),
        QByteArray(b"main"),
        QByteArray(b"side"),
    )


def test_curator_layout_absent_reads_none(state_home: Path) -> None:
    assert session_state.load_curator_layout("player+dict") == (None, None, None)


def test_a_side_split_only_restores_onto_the_same_pane_composition(state_home: Path) -> None:
    """QSplitter.restoreState applies a longer blob's prefix rather than
    rejecting it, so a video curator's three sizes would silently mis-size a
    manga curator's two. Keying by composition is what prevents that.
    """
    session_state.save_curator_layout(
        QByteArray(b"geo"), QByteArray(b"main"), QByteArray(b"side"), side_key="player+sentences+dict"
    )
    geometry, main_split, side_split = session_state.load_curator_layout("image+dict")
    assert side_split is None
    assert geometry == QByteArray(b"geo")
    assert main_split == QByteArray(b"main")


def test_curator_layout_saves_geometry_alone_when_there_are_no_splits(state_home: Path) -> None:
    """A table-only curator has no splitter to save."""
    session_state.save_curator_layout(QByteArray(b"geo"), None, None, side_key="")
    assert session_state.load_curator_layout("") == (QByteArray(b"geo"), None, None)


def test_non_blob_curator_state_reads_none(state_home: Path) -> None:
    (state_home / "ui_state.ini").write_text("[curator]\ngeometry=not-a-blob\nsplit_main=also-not\n", encoding="utf-8")
    geometry, main_split, _ = session_state.load_curator_layout("player+dict")
    assert geometry is None
    assert main_split is None


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


def test_route_round_trips(state_home: Path) -> None:
    session_state.save_route("reading", {"video": "batch", "reading": "novels"})
    main_tab, subtabs = session_state.load_route()
    assert main_tab == "reading"
    assert subtabs == {"video": "batch", "reading": "novels"}


def test_route_absent_reads_empty(state_home: Path) -> None:
    assert session_state.load_route() == (None, {})


def test_saving_route_replaces_stale_subtabs(state_home: Path) -> None:
    """A container that is gone must not linger in the file forever."""
    session_state.save_route("video", {"video": "batch", "gone": "nowhere"})
    session_state.save_route("video", {"video": "single"})
    assert session_state.load_route() == ("video", {"video": "single"})


def test_ini_holds_only_geometry_route_folders_and_curator(state_home: Path) -> None:
    """No scroll offset, no form draft, no field text ever reaches the file."""
    session_state.save_geometry(QByteArray(b"blob"))
    session_state.save_route("video", {"video": "single"})
    session_state.remember_accepted_path("video.single.inputs", str(state_home / "ep.mkv"), file_mode=True)
    session_state.save_curator_layout(
        QByteArray(b"geo"), QByteArray(b"main"), QByteArray(b"side"), side_key="player+dict"
    )

    settings = QSettings(str(state_home / "ui_state.ini"), QSettings.Format.IniFormat)
    assert set(settings.allKeys()) == {
        "window/geometry",
        "navigation/main_tab",
        "navigation/subtab/video",
        "directories/video.single.inputs",
        "curator/geometry",
        "curator/split_main",
        "curator/split_side/player+dict",
    }


# ---------------------------------------------------------------------------
# Remembered folders
# ---------------------------------------------------------------------------


def test_accepted_file_remembers_its_parent(state_home: Path) -> None:
    episode = state_home / "media" / "ep.mkv"
    episode.parent.mkdir()
    episode.touch()
    session_state.remember_accepted_path("video.single.inputs", str(episode), file_mode=True)
    assert session_state.remembered_directory("video.single.inputs") == str(episode.parent)


def test_accepted_folder_remembers_itself(state_home: Path) -> None:
    folder = state_home / "library"
    folder.mkdir()
    session_state.remember_accepted_path("reading.manga.inputs", str(folder), file_mode=False)
    assert session_state.remembered_directory("reading.manga.inputs") == str(folder)


def test_workflow_keys_do_not_overwrite_one_another(state_home: Path) -> None:
    manga = state_home / "manga"
    novels = state_home / "novels"
    manga.mkdir()
    novels.mkdir()
    session_state.remember_accepted_path("reading.manga.inputs", str(manga), file_mode=False)
    session_state.remember_accepted_path("reading.novels.inputs", str(novels), file_mode=False)

    assert session_state.remembered_directory("reading.manga.inputs") == str(manga)
    assert session_state.remembered_directory("reading.novels.inputs") == str(novels)


def test_unknown_and_empty_keys_read_none(state_home: Path) -> None:
    assert session_state.remembered_directory("never.written") is None
    assert session_state.remembered_directory(None) is None
    assert session_state.remembered_directory("") is None


def test_empty_path_is_not_recorded(state_home: Path) -> None:
    session_state.remember_accepted_path("video.single.inputs", "", file_mode=True)
    session_state.remember_accepted_path("video.single.inputs", "   ", file_mode=True)
    assert session_state.remembered_directory("video.single.inputs") is None


def test_trailing_space_folder_survives_the_round_trip(state_home: Path) -> None:
    """A path is never stripped on the way in or out (batch-mining core dump)."""
    folder = state_home / "spaced "
    folder.mkdir()
    session_state.remember_accepted_path("video.batch.inputs", str(folder), file_mode=False)
    assert session_state.remembered_directory("video.batch.inputs") == str(folder)


# ---------------------------------------------------------------------------
# Best effort: nothing here may raise
# ---------------------------------------------------------------------------


def test_writes_never_raise_when_the_home_is_unwritable(state_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a, **_kw):
        raise OSError("read-only home")

    monkeypatch.setattr(session_state.Path, "mkdir", boom)

    session_state.save_geometry(QByteArray(b"blob"))
    session_state.save_route("video", {"video": "single"})
    session_state.remember_accepted_path("video.single.inputs", "/tmp/x", file_mode=True)


def test_reads_never_raise_on_a_corrupt_file(state_home: Path) -> None:
    (state_home / "ui_state.ini").write_bytes(b"\x00\xff not ini at all \x00")
    assert session_state.load_geometry() is None
    assert session_state.load_route() == (None, {})
    assert session_state.remembered_directory("video.single.inputs") is None
