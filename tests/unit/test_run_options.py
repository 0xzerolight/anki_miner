"""RunOptionsMixin: the seed guard and the persist-once contract."""

from __future__ import annotations

import dataclasses
import json
from dataclasses import replace
from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QWidget

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.utils.config_manager import GUIConfigManager
from anki_miner.gui.utils.run_options import RunOptionsMixin
from anki_miner.gui.widgets.condense_tab import CondenseTab
from anki_miner.gui.widgets.download_tab import DownloadTab
from anki_miner.gui.widgets.mokuro_tab import MokuroTab


class _Screen(RunOptionsMixin, QWidget):
    run_options_changed = pyqtSignal(object)

    def __init__(self) -> None:
        super().__init__()
        self.config = AnkiMinerConfig()


@pytest.fixture
def screen(qtbot):
    widget = _Screen()
    qtbot.addWidget(widget)
    return widget


def test_a_real_change_is_adopted_and_emitted_once(screen):
    seen: list[AnkiMinerConfig] = []
    screen.run_options_changed.connect(seen.append)

    assert screen.persist_run_options(review_words_before_mining=True) is True

    assert len(seen) == 1
    assert seen[0].review_words_before_mining is True
    # The screen adopts its own new config so the next compare is against it.
    assert screen.config.review_words_before_mining is True


def test_an_unchanged_value_emits_nothing(screen):
    seen: list[AnkiMinerConfig] = []
    screen.run_options_changed.connect(seen.append)

    assert screen.persist_run_options(review_words_before_mining=False) is False

    assert seen == []


def test_nothing_is_emitted_while_seeding(screen):
    """The programmatic setChecked in a re-seed must not write back."""
    seen: list[AnkiMinerConfig] = []
    screen.run_options_changed.connect(seen.append)

    with screen.seeding():
        assert screen.persist_run_options(review_words_before_mining=True) is False

    assert seen == []
    assert screen.config.review_words_before_mining is False


def test_the_seed_guard_is_released_even_when_the_body_raises(screen):
    with pytest.raises(RuntimeError), screen.seeding():
        raise RuntimeError("boom")
    assert screen._seeding is False
    assert screen.persist_run_options(review_words_before_mining=True) is True


def test_nested_seeding_stays_guarded_until_the_outermost_exit(screen):
    """update_config seeds, and the gate refresh it calls seeds again."""
    with screen.seeding():
        with screen.seeding():
            pass
        assert screen._seeding is True
    assert screen._seeding is False


def test_a_screen_whose_config_moved_underneath_still_compares_against_it(screen):
    screen.config = replace(screen.config, review_words_before_mining=True)
    assert screen.persist_run_options(review_words_before_mining=True) is False


# ---------------------------------------------------------------------------
# Condense / Download / Manga OCR: a saved gui_config.json reopens unchanged.
# Signal-name agnostic on purpose (it reads tab.config), so it guards those
# three screens' switch onto this mixin without being edited by it.
# ---------------------------------------------------------------------------


def _edit_condense(tab, tmp_path: Path) -> None:
    tab.padding_spinbox.setValue(750)
    tab.offset_spinbox.setValue(-200)
    tab.format_combo.setCurrentIndex(tab.format_combo.findData("flac"))
    tab.write_subs_checkbox.setChecked(True)
    tab.tag_outputs_checkbox.setChecked(True)
    tab.merge_checkbox.setChecked(True)


def _read_condense(tab) -> tuple:
    return (
        tab.padding_spinbox.value(),
        tab.offset_spinbox.value(),
        tab.format_combo.currentData(),
        tab.write_subs_checkbox.isChecked(),
        tab.tag_outputs_checkbox.isChecked(),
        tab.merge_checkbox.isChecked(),
    )


def _edit_download(tab, tmp_path: Path) -> None:
    tab.preset_combo.setCurrentIndex(tab.preset_combo.findData("720p"))
    tab.custom_format_edit.setText("bv*+ba")
    tab.custom_format_edit.editingFinished.emit()
    tab.write_subs_checkbox.setChecked(True)
    tab._set_sub_langs("ko,ja")
    tab.audio_lang_combo.setCurrentIndex(tab.audio_lang_combo.findData("ko"))
    tab.embed_thumbnail_checkbox.setChecked(True)
    tab.embed_metadata_checkbox.setChecked(True)


def _read_download(tab) -> tuple:
    return (
        tab.preset_combo.currentData(),
        tab.custom_format_edit.text(),
        tab.write_subs_checkbox.isChecked(),
        tab._sub_langs,
        tab.audio_lang_combo.currentData(),
        tab.embed_thumbnail_checkbox.isChecked(),
        tab.embed_metadata_checkbox.isChecked(),
    )


def _edit_mokuro(tab, tmp_path: Path) -> None:
    tab.gpu_checkbox.setChecked(False)
    tab.mokuro_selector.set_path(str(tmp_path / "bin" / "mokuro"))
    tab.flush_pending_edits()  # commit the debounced path edit now


def _read_mokuro(tab) -> tuple:
    return (tab.gpu_checkbox.isChecked(), tab.mokuro_selector.path_or_none())


#: name -> (tab class, edit, read, the exact JSON keys/values a saved file carries)
_TOOL_TAB_CASES = {
    "condense": (
        CondenseTab,
        _edit_condense,
        _read_condense,
        lambda tmp: {
            "condenser_padding_ms": 750,
            "condenser_offset_ms": -200,
            "condenser_output_format": "flac",
            "condenser_write_subtitles": True,
            "condenser_tag_outputs": True,
            "condenser_merge_output": True,
        },
    ),
    "download": (
        DownloadTab,
        _edit_download,
        _read_download,
        lambda tmp: {
            "downloader_format_preset": "720p",
            "downloader_custom_format": "bv*+ba",
            "downloader_write_subtitles": True,
            "downloader_subtitle_langs": "ko,ja",
            "downloader_audio_lang": "ko",
            "downloader_embed_thumbnail": True,
            "downloader_embed_metadata": True,
        },
    ),
    "mokuro": (
        MokuroTab,
        _edit_mokuro,
        _read_mokuro,
        lambda tmp: {"mokuro_use_gpu": False, "mokuro_location": str(tmp / "bin" / "mokuro")},
    ),
}


@pytest.mark.parametrize("case", sorted(_TOOL_TAB_CASES))
def test_a_tool_tabs_saved_run_options_reopen_unchanged(case, qtbot, tmp_path):
    """Edit every run option, save, reload, reopen: same JSON keys, same widgets."""
    tab_cls, edit, read, expected = _TOOL_TAB_CASES[case]
    base = AnkiMinerConfig(media_temp_folder=tmp_path / "tmp")
    tab = tab_cls(base, suppress_optional_startup=True)
    qtbot.addWidget(tab)

    edit(tab, tmp_path)

    written = expected(tmp_path)
    moved = {
        f.name for f in dataclasses.fields(AnkiMinerConfig) if getattr(tab.config, f.name) != getattr(base, f.name)
    }
    assert moved == set(written)  # exactly these fields, nothing leaks

    GUIConfigManager.save_config(tab.config)
    on_disk = json.loads(GUIConfigManager.CONFIG_FILE.read_text(encoding="utf-8"))
    assert {key: on_disk[key] for key in written} == written

    reopened = tab_cls(GUIConfigManager.load_config(), suppress_optional_startup=True)
    qtbot.addWidget(reopened)
    assert read(reopened) == read(tab)


def test_the_tool_tabs_are_discovered_and_wired_once(wired_window):
    """app.py wires these three only through its run_options_changed discovery
    loop, so each must be a child of the window (hidden Utilities sub-tabs
    included) with exactly one receiver."""
    window, _titles, _tabs = wired_window
    for tab_cls in (CondenseTab, DownloadTab, MokuroTab):
        found = [w for w in window.findChildren(QWidget) if type(w) is tab_cls]
        assert len(found) == 1, tab_cls.__name__
        assert found[0].receivers(found[0].run_options_changed) == 1, tab_cls.__name__
