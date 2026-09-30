"""C09 (UI/UX audit 2026-09-29): an empty resource list says what is missing."""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.config import ChainEntry, FreqEntry
from anki_miner.gui.widgets.panels.audio_pack_settings_panel import AudioPackSettingsPanel
from anki_miner.gui.widgets.panels.dictionary_settings_panel import DictionarySettingsPanel
from anki_miner.gui.widgets.panels.frequency_settings_panel import FrequencySettingsPanel
from anki_miner.gui.widgets.panels.pitch_settings_panel import PitchSettingsPanel


@pytest.mark.parametrize(
    "panel_cls, text, kind",
    [
        (DictionarySettingsPanel, "No dictionaries yet.", "dict"),
        (FrequencySettingsPanel, "No frequency lists yet.", "freq"),
        (PitchSettingsPanel, "No pitch accent lists yet.", "pitch"),
    ],
)
def test_an_empty_chain_names_what_is_missing_and_downloads_that_family(qtbot, tmp_path, panel_cls, text, kind):
    panel = panel_cls(tmp_path)
    qtbot.addWidget(panel)
    panel.set_recommended_available(True)
    panel.set_chain(())

    assert panel._list.isHidden()
    assert not panel._empty_row.isHidden()
    assert panel._empty_label.text() == text
    assert not panel._download_recommended_btn.isHidden()
    with qtbot.waitSignal(panel.download_recommended_requested, timeout=1000) as blocker:
        panel._download_recommended_btn.click()
    assert blocker.args == [kind]


def test_no_download_button_when_the_catalogue_has_none_of_that_kind(qtbot, tmp_path):
    panel = PitchSettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    panel.set_recommended_available(False)
    panel.set_chain(())
    assert not panel._empty_row.isHidden()
    assert panel._download_recommended_btn.isHidden()


def test_word_audio_has_no_download_family(qtbot, tmp_path):
    panel = AudioPackSettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    panel.set_recommended_available(True)
    panel.set_chain(())
    assert panel._empty_label.text() == "No word audio sources yet."
    assert panel._download_recommended_btn.isHidden()


def test_a_chain_with_rows_shows_the_list(qtbot, tmp_path):
    panel = FrequencySettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    panel.set_chain((FreqEntry(source_id="jpdb", enabled=True),))
    assert not panel._list.isHidden()
    assert panel._empty_row.isHidden()


def test_remove_is_offered_only_for_a_selected_row(qtbot, tmp_path):
    panel = DictionarySettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    panel.set_chain((ChainEntry(kind="jisho", dict_id=None, enabled=True),))
    assert not panel._remove_btn.isEnabled()

    panel._list.setCurrentRow(0)
    assert panel._remove_btn.isEnabled()

    held = panel.hold_mutation("import")
    assert not panel._remove_btn.isEnabled()
    panel.release(held)
    assert panel._remove_btn.isEnabled()
