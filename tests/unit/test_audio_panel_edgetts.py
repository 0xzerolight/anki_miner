"""Settings -> Word Audio labels the Edge read-aloud leg and offers it only for a language with a voice."""

from __future__ import annotations

import contextlib
import dataclasses

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QDialog, QDialogButtonBox

from anki_miner.config import AnkiMinerConfig, AudioSourceEntry
from anki_miner.gui.widgets.panels import audio_pack_settings_panel as asp_mod
from anki_miner.gui.widgets.panels.audio_pack_settings_panel import AudioPackSettingsPanel
from anki_miner.gui.widgets.settings_tab import SettingsTab
from anki_miner.languages.registry import get_profile
from tests.unit.languages.stub_registry import register_stub_profile

EDGE = AudioSourceEntry(kind="edgetts")


def _panel(qtbot, tmp_path) -> AudioPackSettingsPanel:
    panel = AudioPackSettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    return panel


def test_an_edgetts_row_is_labelled_microsoft_edge(qtbot, tmp_path):
    panel = _panel(qtbot, tmp_path)
    assert panel._describe_entry(EDGE, None) == ("Microsoft Edge (synthetic TTS)", "online", None, False, False)
    assert not panel._is_protected_entry(EDGE)


def test_the_dialog_offers_edge_only_when_asked(qtbot):
    plain = asp_mod._AddSourceDialog()
    qtbot.addWidget(plain)
    assert plain._kind_combo.findData("edgetts") == -1

    offered = asp_mod._AddSourceDialog(offer_edge_tts=True)
    qtbot.addWidget(offered)
    assert offered.selected_kind() == "custom_json"  # the default kind is unchanged
    offered._kind_combo.setCurrentIndex(offered._kind_combo.findData("edgetts"))
    assert offered.selected_kind() == "edgetts"
    assert offered.url_value() is None
    assert not offered._url_edit.isVisibleTo(offered)
    assert offered._buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()


def test_the_panel_offers_edge_for_a_voiced_language_until_it_is_in_the_chain(qtbot, tmp_path, monkeypatch):
    offers: list[bool] = []

    class _Dialog:
        def __init__(self, parent=None, *, offer_edge_tts: bool = False) -> None:
            offers.append(offer_edge_tts)

        def exec(self) -> QDialog.DialogCode:
            return QDialog.DialogCode.Accepted if offers[-1] else QDialog.DialogCode.Rejected

        def selected_kind(self) -> str:
            return "edgetts"

        def url_value(self) -> None:
            return None

    monkeypatch.setattr(asp_mod, "_AddSourceDialog", _Dialog)
    panel = _panel(qtbot, tmp_path)
    panel.set_chain((AudioSourceEntry(kind="googletts"),))

    panel._on_add_online_source()  # no Edge voice for this language
    panel.set_edge_tts_available(True)
    with qtbot.waitSignal(panel.chain_changed, timeout=1000):
        panel._on_add_online_source()
    panel._on_add_online_source()  # already in the chain

    assert offers == [False, True, False]
    assert [(e.kind, e.enabled) for e in panel.get_chain()] == [("googletts", True), ("edgetts", True)]


@pytest.fixture
def tab(test_config: AnkiMinerConfig, qtbot):
    widget = SettingsTab(test_config)
    qtbot.addWidget(widget)
    yield widget
    widget.shutdown()
    for worker in widget.iter_close_workers():
        if worker is not None:
            worker.wait(3000)
    qtbot.wait(10)
    with contextlib.suppress(RuntimeError):
        widget.deleteLater()


def test_settings_tells_the_panel_whether_the_language_has_an_edge_voice(tab, monkeypatch):
    assert tab.audio_panel._edge_tts_available is False  # ja names no Edge voice

    audio = dataclasses.replace(get_profile("ja").audio, edge_voice="zh-HK-HiuMaanNeural")
    register_stub_profile(monkeypatch, "zh", audio=audio)
    tab.reload_from_config(dataclasses.replace(tab.config, language="zh"))

    assert tab.audio_panel._edge_tts_available is True
