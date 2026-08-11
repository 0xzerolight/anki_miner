"""No two destinations may share a name (D46-B).

The app had three pairs where the same word named two different places: a main
"Audio" tab beside a Settings audio panel, a Reading "Subtitles" sub-tab beside
a Settings subtitles panel, and a main "Tools" tab beside a "Tools" menu. A user
asking for help could not say which one they meant, and following the wrong one
lands on a screen that does something else entirely.

This file is the ledger that keeps them apart. It also pins the stable internal
keys: the labels are allowed to move, the keys that resolve them are not.
"""

from __future__ import annotations

import unicodedata
from pathlib import Path

import pytest
from PyQt6.QtWidgets import QMenuBar

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.capabilities import MAIN_TABS, SETTINGS_SUBTABS, SUBTAB_KEYS
from anki_miner.gui.widgets.panels.subtitles_settings_panel import SubtitlesSettingsPanel
from anki_miner.gui.widgets.reading_tab import ReadingTab
from anki_miner.gui.workers import condense_worker
from anki_miner.gui.workers.condense_worker import CondenseItem
from anki_miner.utils import file_pairing


@pytest.fixture
def reading_tab(test_config: AnkiMinerConfig, qtbot):
    """A Reading container with its four sub-tabs built."""
    widget = ReadingTab(config=test_config, presenter=None)
    qtbot.addWidget(widget)
    return widget


def _menu_titles(window) -> list[str]:
    menu_bar = window.menuBar()
    assert isinstance(menu_bar, QMenuBar)
    return [action.text().replace("&", "") for action in menu_bar.actions()]


def test_the_audiobook_tab_says_what_it_mines(wired_window):
    """ "Audio" named both a mining destination and a Settings resource page."""
    _window, titles, _tabs = wired_window
    assert "Audiobooks" in titles
    assert "Audio" not in titles


def test_the_tools_tab_does_not_share_the_tools_menu_name(wired_window):
    """A "Tools" tab beside a "Tools" menu is an unanswerable question."""
    window, titles, _tabs = wired_window
    assert "Utilities" in titles
    assert "Tools" in _menu_titles(window)
    assert not set(titles) & set(_menu_titles(window))


def test_reading_subtitles_names_the_files_it_reads(reading_tab):
    """Reading→Subtitles mines existing files; Settings→Subtitles makes them."""
    labels = [reading_tab._inner_tabs.tabText(i) for i in range(reading_tab._inner_tabs.count())]
    assert "Subtitle Files" in labels
    assert "Subtitles" not in labels


def test_the_settings_subtitles_panel_matches_its_navigator_entry(qtbot):
    """The panel title and the navigator label must be the same words."""
    panel = SubtitlesSettingsPanel(suppress_optional_startup=True)
    qtbot.addWidget(panel)
    assert panel._title_label.text() == "Transcription & Alignment"


def test_stable_keys_did_not_move_with_the_labels():
    """A renamed label that shifts its key makes the destination unreachable."""
    assert set(MAIN_TABS) == {"video", "deckbuilder", "audiobook", "reading", "analytics", "subtitles", "settings"}
    assert SUBTAB_KEYS["reading"] == frozenset({"manga", "novels", "subtitles", "text"})
    assert SUBTAB_KEYS["subtitles"] == frozenset({"generate", "retime", "condense", "backfill"})
    assert "audio" in SETTINGS_SUBTABS
    assert "subtitles" in SETTINGS_SUBTABS


def test_condense_destination_reserves_fixed_suffix_bytes(tmp_path):
    media = tmp_path / ("界" * 81 + ".mkv")
    outputs = condense_worker.plan_condense_outputs([CondenseItem(media)], None, "mp3")

    assert len(outputs[0].name.encode("utf-8")) <= 255
    assert outputs[0].name.endswith("_condensed.mp3")


def test_distinct_truncated_condense_stems_keep_distinct_hashes(tmp_path):
    items = [CondenseItem(tmp_path / ("v" * 244 + marker + ".mkv")) for marker in ("a", "b")]

    outputs = condense_worker.plan_condense_outputs(items, None, "mp3")

    assert outputs[0] != outputs[1]
    assert all(len(path.name.encode("utf-8")) <= 255 for path in outputs)


def test_condense_planner_scans_each_output_directory_once(tmp_path, monkeypatch):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    items = [
        CondenseItem(first / "one.mkv"),
        CondenseItem(first / "two.mkv"),
        CondenseItem(first / "three.mkv"),
        CondenseItem(second / "four.mkv"),
        CondenseItem(second / "five.mkv"),
    ]
    scans: list[Path] = []
    real_iterdir = Path.iterdir

    def counted_iterdir(path: Path):
        scans.append(path)
        return real_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", counted_iterdir)

    condense_worker.plan_condense_outputs(items, None, "mp3")

    assert scans.count(first) == 1
    assert scans.count(second) == 1


def test_bulk_output_resolution_keeps_existing_exact_nfc_nfd_twins(tmp_path):
    decomposing_name = "が01_condensed.mp3"
    nfc_name = unicodedata.normalize("NFC", decomposing_name)
    nfd_name = unicodedata.normalize("NFD", decomposing_name)
    assert nfc_name.encode("utf-8") != nfd_name.encode("utf-8")
    nfc_path = tmp_path / nfc_name
    nfd_path = tmp_path / nfd_name
    nfc_path.write_bytes(b"nfc")
    nfd_path.write_bytes(b"nfd")

    resolved = file_pairing.resolve_output_paths(tmp_path, [nfc_name, nfd_name])

    assert resolved == [nfc_path, nfd_path]


def test_condense_planner_keeps_existing_exact_nfc_nfd_twins(tmp_path):
    decomposing_stem = "が01"
    nfc_stem = unicodedata.normalize("NFC", decomposing_stem)
    nfd_stem = unicodedata.normalize("NFD", decomposing_stem)
    assert nfc_stem.encode("utf-8") != nfd_stem.encode("utf-8")
    items = [
        CondenseItem(tmp_path / f"{nfc_stem}.mkv"),
        CondenseItem(tmp_path / f"{nfd_stem}.mp4"),
    ]
    nfc_output = tmp_path / f"{nfc_stem}_condensed.mp3"
    nfd_output = tmp_path / f"{nfd_stem}_condensed.mp3"
    nfc_output.write_bytes(b"nfc")
    nfd_output.write_bytes(b"nfd")

    outputs = condense_worker.plan_condense_outputs(items, None, "mp3")

    assert outputs == [nfc_output, nfd_output]


def test_bulk_output_resolution_keeps_posix_case_distinct_plans(tmp_path, monkeypatch):
    monkeypatch.setattr(file_pairing, "_CASE_INSENSITIVE_FS", False)

    resolved = file_pairing.resolve_output_paths(
        tmp_path,
        ["Episode_condensed.mp3", "episode_condensed.mp3"],
    )

    assert resolved == [
        tmp_path / "Episode_condensed.mp3",
        tmp_path / "episode_condensed.mp3",
    ]
