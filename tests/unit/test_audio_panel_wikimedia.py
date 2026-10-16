"""Settings -> Word Audio: the Wikimedia Commons row (F-16, R12)."""

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

WIKI = AudioSourceEntry(kind="wikimedia")
GOOGLE = AudioSourceEntry(kind="googletts")
EDGE = AudioSourceEntry(kind="edgetts")


def _panel(qtbot, tmp_path) -> AudioPackSettingsPanel:
    panel = AudioPackSettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    return panel


def test_a_wikimedia_row_is_labelled_commons(qtbot, tmp_path):
    panel = _panel(qtbot, tmp_path)

    assert panel._describe_entry(WIKI, None) == ("Wikimedia Commons (native recordings)", "online", None, False, False)
    assert not panel._is_protected_entry(WIKI)


def test_the_dialog_offers_wikimedia_only_when_asked(qtbot):
    plain = asp_mod._AddSourceDialog()
    qtbot.addWidget(plain)
    assert plain._kind_combo.findData("wikimedia") == -1

    offered = asp_mod._AddSourceDialog(offer_wikimedia=True)
    qtbot.addWidget(offered)
    assert offered.selected_kind() == "custom_json"  # the default kind is unchanged
    offered._kind_combo.setCurrentIndex(offered._kind_combo.findData("wikimedia"))
    assert offered.selected_kind() == "wikimedia"
    assert offered.url_value() is None
    assert not offered._url_edit.isVisibleTo(offered)
    assert offered._buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()


@pytest.mark.parametrize(
    ("chain", "expected"),
    [
        ((GOOGLE, EDGE), ["wikimedia", "googletts", "edgetts"]),
        ((AudioSourceEntry(kind="googletts", enabled=False), EDGE), ["googletts", "wikimedia", "edgetts"]),
        ((AudioSourceEntry(kind="jpod101"),), ["jpod101", "wikimedia"]),
    ],
)
def test_a_new_row_goes_above_the_first_synthetic_voice(qtbot, tmp_path, chain, expected):
    panel = _panel(qtbot, tmp_path)
    panel.set_chain(chain)

    with qtbot.waitSignal(panel.chain_changed, timeout=1000):
        panel.add_source_entry(WIKI)

    assert [entry.kind for entry in panel.get_chain()] == expected


def test_the_panel_offers_wikimedia_until_it_is_in_the_chain(qtbot, tmp_path, monkeypatch):
    offers: list[bool] = []

    class _Dialog:
        def __init__(self, parent=None, *, offer_edge_tts: bool = False, offer_wikimedia: bool = False) -> None:
            offers.append(offer_wikimedia)

        def exec(self) -> QDialog.DialogCode:
            return QDialog.DialogCode.Accepted if offers[-1] else QDialog.DialogCode.Rejected

        def selected_kind(self) -> str:
            return "wikimedia"

        def url_value(self) -> None:
            return None

    monkeypatch.setattr(asp_mod, "_AddSourceDialog", _Dialog)
    panel = _panel(qtbot, tmp_path)
    panel.set_chain((GOOGLE,))

    panel._on_add_online_source()  # this language names no Commons prefixes
    panel.set_wikimedia_available(True)
    with qtbot.waitSignal(panel.chain_changed, timeout=1000):
        panel._on_add_online_source()
    panel._on_add_online_source()  # already in the chain

    assert offers == [False, True, False]
    assert [(e.kind, e.enabled) for e in panel.get_chain()] == [("wikimedia", True), ("googletts", True)]


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


def test_settings_offers_wikimedia_only_for_a_language_with_prefixes(tab, monkeypatch):
    assert tab.audio_panel._wikimedia_available is False  # ja names no Commons prefixes

    audio = dataclasses.replace(get_profile("ja").audio, wikimedia_prefixes=("Zh-",))
    register_stub_profile(monkeypatch, "zh", audio=audio)
    tab.reload_from_config(dataclasses.replace(tab.config, language="zh"))

    assert tab.audio_panel._wikimedia_available is True
