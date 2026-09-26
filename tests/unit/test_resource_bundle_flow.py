"""Settings → Resources ▾: export and import a resource bundle through the real tab."""

import json
import zipfile
from pathlib import Path

import pytest
from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox

from anki_miner.config import ChainEntry, FreqEntry
from anki_miner.gui.controllers import resource_bundle_flow as flow_module
from anki_miner.gui.controllers.anki_probe_controller import AnkiProbeController
from anki_miner.gui.utils import file_dialogs
from anki_miner.gui.widgets import settings_tab as settings_tab_module
from anki_miner.gui.widgets.dialogs.resource_bundle_dialog import ResourceBundleDialog
from anki_miner.gui.widgets.settings_tab import SettingsTab
from anki_miner.languages.registry import language_display_name
from anki_miner.services.known_word_db import KnownWordDB
from anki_miner.services.resource_bundle import BundleInstallResult, read_bundle_manifest
from tests.fixtures.resource_bundle import FREQ_ID, build_resource_setup, empty_receiver, write_sender_bundle


def _make_tab(qtbot, config) -> SettingsTab:
    # The echo stands in for MainWindow.update_config's config_refreshed round trip.
    holder: dict[str, SettingsTab] = {}
    tab = SettingsTab(config, commit_config=lambda cfg: holder["tab"].update_config(cfg))
    holder["tab"] = tab
    qtbot.addWidget(tab)
    tab._debounce_timer.setInterval(60_000)
    return tab


def _close(qtbot, tab: SettingsTab) -> None:
    tab.shutdown()
    for worker in tab.iter_close_workers():
        if worker is not None:
            worker.wait(10_000)
    qtbot.wait(10)
    tab.deleteLater()


def _pick_open(monkeypatch, path: Path) -> None:
    monkeypatch.setattr(file_dialogs, "pick_open_file", lambda *a, on_done, **k: on_done(str(path)))


@pytest.fixture
def infos(monkeypatch):
    shown: list[tuple[str, str]] = []

    def _information(_parent, title, text, *_args, **_kwargs):
        shown.append((title, text))
        return QMessageBox.StandardButton.Ok

    monkeypatch.setattr(QMessageBox, "information", _information)
    return shown


@pytest.fixture(autouse=True)
def accept_every_checklist(monkeypatch):
    monkeypatch.setattr(ResourceBundleDialog, "exec", lambda self: QDialog.DialogCode.Accepted)


def test_the_import_menu_follows_the_export_menu_in_the_footer(qtbot, test_config, monkeypatch):
    # Shown, so the probe that would open an AnkiConnect socket is stubbed
    # (as test_settings_tab_profiles does); geometry, because the footer is a
    # nested QHBoxLayout with no handle of its own.
    monkeypatch.setattr(AnkiProbeController, "refresh_name_lists", lambda _self: None)
    tab = _make_tab(qtbot, test_config)
    tab.resize(1024, 768)
    tab.show()
    qtbot.waitExposed(tab)
    QApplication.processEvents()
    export, import_ = tab.export_button, tab.import_button
    assert import_.isVisible() and import_.width() > 0  # vacuity guard
    assert import_.y() == export.y(), "not on the same footer row"
    assert import_.x() > export.x(), "Import must follow Export"
    _close(qtbot, tab)


def test_export_writes_a_bundle_of_the_chosen_resources(qtbot, tmp_path, test_config, monkeypatch, infos):
    tab = _make_tab(qtbot, build_resource_setup(tmp_path / "sender", test_config))
    target = tmp_path / "bundle.zip"
    monkeypatch.setattr(file_dialogs, "pick_save_file", lambda *a, on_done, **k: on_done(str(target)))
    tab.export_resources_action.trigger()
    qtbot.waitUntil(lambda: bool(infos), timeout=15_000)
    assert infos[0][0] == "Resources Exported"
    assert [i.kind for i in read_bundle_manifest(target).items] == [
        "dictionary",
        "frequency",
        "pitch",
        "known_words",
        "blacklist",
    ]
    assert tab.export_resources_action.isEnabled() and not tab.dictionary_panel.has_active_mutation("bundle")
    _close(qtbot, tab)


def test_export_cancelled_at_the_picker_writes_nothing_and_unlocks(qtbot, tmp_path, test_config, monkeypatch, infos):
    tab = _make_tab(qtbot, build_resource_setup(tmp_path / "sender", test_config))
    picked: list[bool] = []

    def _cancel(*_args, on_done, **_kwargs):
        picked.append(True)
        on_done("")

    monkeypatch.setattr(file_dialogs, "pick_save_file", _cancel)
    tab.export_resources_action.trigger()
    qtbot.waitUntil(lambda: bool(picked), timeout=10_000)
    assert infos == [] and not list(tmp_path.glob("*.zip"))
    assert tab.export_resources_action.isEnabled() and not tab.dictionary_panel.has_active_mutation("bundle")
    _close(qtbot, tab)


def test_import_installs_and_chains_a_bundle(qtbot, tmp_path, test_config, monkeypatch, infos):
    sender, bundle = write_sender_bundle(tmp_path, test_config)
    receiver = empty_receiver(tmp_path / "receiver", test_config)
    tab = _make_tab(qtbot, receiver)
    _pick_open(monkeypatch, bundle)
    tab.import_resources_action.trigger()
    qtbot.waitUntil(lambda: bool(infos), timeout=30_000)

    assert infos[0][0] == "Resources Imported"
    dict_id = sender.dictionary_chain[0].dict_id
    assert tab.config.dictionary_chain[0] == ChainEntry(kind="indexed", dict_id=dict_id, enabled=True)
    assert FreqEntry(FREQ_ID) in tab.config.frequency_chain
    assert tab.config.blacklist_path == settings_tab_module.ANKI_MINER_HOME / "wordlists" / "ja" / "blacklist.txt"
    assert tab.config.use_blacklist is True
    assert KnownWordDB(receiver.known_words_db_path).get_words_by_source("user") == {"猫"}
    assert tab.import_resources_action.isEnabled() and not tab.dictionary_panel.has_active_mutation("bundle")
    _close(qtbot, tab)


def test_the_chain_panels_stay_locked_while_a_bundle_is_in_flight(qtbot, tmp_path, test_config, monkeypatch, infos):
    _sender, bundle = write_sender_bundle(tmp_path, test_config)
    tab = _make_tab(qtbot, empty_receiver(tmp_path / "receiver", test_config))
    locked_at_checklist: list[bool] = []

    def _exec(_dialog):
        locked_at_checklist.append(tab.frequency_panel.has_active_mutation("bundle"))
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(ResourceBundleDialog, "exec", _exec)
    _pick_open(monkeypatch, bundle)
    tab.import_resources_action.trigger()
    qtbot.waitUntil(lambda: bool(infos), timeout=30_000)
    assert locked_at_checklist == [True]
    # "bundle" specifically: the post-import refresh_registry() holds its own
    # "scan" token until the off-thread rescan lands, which can outlive infos.
    assert not tab.frequency_panel.has_active_mutation("bundle")
    _close(qtbot, tab)


def test_a_cancelled_import_still_chains_what_landed(qtbot, tmp_path, test_config, monkeypatch, infos):
    _sender, bundle = write_sender_bundle(tmp_path, test_config)
    receiver = empty_receiver(tmp_path / "receiver", test_config)
    tab = _make_tab(qtbot, receiver)
    dict_item = next(i for i in read_bundle_manifest(bundle).items if i.kind == "dictionary")
    landed = BundleInstallResult(
        installed=(dict_item,),
        wordlist_paths={},
        known_words_added=0,
        failures=(),
        cancelled=True,
        roots={"dictionary": receiver.dicts_root, "frequency": receiver.freqs_root, "pitch": receiver.pitch_root},
    )
    monkeypatch.setattr(flow_module, "install_resource_bundle", lambda *a, **k: landed)
    _pick_open(monkeypatch, bundle)
    tab.import_resources_action.trigger()
    qtbot.waitUntil(lambda: bool(infos), timeout=10_000)
    assert tab.config.dictionary_chain[0] == ChainEntry(kind="indexed", dict_id=dict_item.item_id)
    assert "cancelled" in infos[0][1]
    _close(qtbot, tab)


def _korean_bundle(path: Path) -> Path:
    manifest = {
        "anki_miner_resources": 1,
        "app_version": "test",
        "language": "ko",
        "items": [
            {
                "kind": "known_words",
                "id": "known_words",
                "name": "Known-words ignore list",
                "member": "known_words.txt",
                "enabled": True,
                "options": {},
            }
        ],
    }
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("known_words.txt", "고양이\n")
        zf.writestr("manifest.json", json.dumps(manifest))
    return path


def test_import_refuses_a_bundle_for_another_mining_language(qtbot, tmp_path, test_config, monkeypatch, infos):
    receiver = empty_receiver(tmp_path / "receiver", test_config)
    tab = _make_tab(qtbot, receiver)
    assert tab.issue_banner().current_issue() is None
    _pick_open(monkeypatch, _korean_bundle(tmp_path / "ko.zip"))
    tab.import_resources_action.trigger()
    qtbot.waitUntil(lambda: tab.issue_banner().current_issue() is not None, timeout=10_000)
    assert language_display_name("ko") in tab.issue_banner().current_issue().summary
    assert infos == [] and tab.config == receiver
    assert not receiver.known_words_db_path.exists()
    assert tab.import_resources_action.isEnabled() and not tab.dictionary_panel.has_active_mutation("bundle")
    _close(qtbot, tab)


def test_an_unreadable_file_is_reported_on_the_page(qtbot, tmp_path, test_config, monkeypatch, infos):
    tab = _make_tab(qtbot, empty_receiver(tmp_path / "receiver", test_config))
    assert tab.issue_banner().current_issue() is None
    junk = tmp_path / "junk.zip"
    junk.write_text("not a zip", encoding="utf-8")
    _pick_open(monkeypatch, junk)
    tab.import_resources_action.trigger()
    qtbot.waitUntil(lambda: tab.issue_banner().current_issue() is not None, timeout=10_000)
    issue = tab.issue_banner().current_issue()
    assert str(junk) not in issue.summary and issue.details
    assert infos == []
    assert tab.import_resources_action.isEnabled()
    _close(qtbot, tab)
