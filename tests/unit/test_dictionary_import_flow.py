"""Tests for DictionaryImportFlow dialog start-directory (F12).

The Add/Re-import Yomitan-zip dialogs should open at the dictionaries dir
(``config.dicts_root``) instead of falling back to the home directory.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from PyQt6.QtWidgets import QWidget

from anki_miner.config import AnkiMinerConfig, ChainEntry
from anki_miner.gui.controllers.dictionary_import_flow import DictionaryImportFlow
from anki_miner.gui.controllers.import_flow_common import _ChainedImportResult

MOD = "anki_miner.gui.controllers.dictionary_import_flow"
COMMON = "anki_miner.gui.controllers.import_flow_common"


def _run_scan_sync(work, on_done, on_error):
    try:
        on_done(work())
    except Exception as exc:  # noqa: BLE001
        on_error(str(exc))


def _make_flow(dicts_root: Path) -> DictionaryImportFlow:
    cfg = MagicMock()
    cfg.dicts_root = dicts_root
    flow = DictionaryImportFlow(
        parent=MagicMock(spec=QWidget),
        panel=MagicMock(),
        get_config=lambda: cfg,
        persist_chain=MagicMock(),
        notify_config_changed=MagicMock(),
    )
    flow._run_latest_scan = _run_scan_sync
    return flow


def test_add_dict_dialog_defaults_to_dicts_dir():
    dicts_root = Path("/home/u/.anki_miner/dicts")
    flow = _make_flow(dicts_root)

    with (
        patch(f"{MOD}.resolve_start_dir", return_value=str(dicts_root)) as rsd,
        patch(f"{MOD}.file_dialogs.pick_open_files", side_effect=lambda *a, on_done, **k: on_done([])),
    ):
        flow.add_dict()  # empty selection → early return after the dialog

    rsd.assert_called_once()
    assert rsd.call_args.kwargs.get("default_dir") == dicts_root


def test_add_dict_passes_every_selected_zip_to_one_batch(tmp_path: Path):
    flow = _make_flow(tmp_path / "dicts")
    selected = [str(tmp_path / "one.zip"), str(tmp_path / "two.zip")]
    flow._run_chained_imports = MagicMock()

    with patch(f"{MOD}.file_dialogs.pick_open_files", side_effect=lambda *a, on_done, **k: on_done(selected)):
        flow.add_dict()

    jobs = flow._run_chained_imports.call_args.kwargs["jobs"]
    assert jobs == [Path(path) for path in selected]


def test_new_dictionary_batch_preserves_picker_order(tmp_path: Path):
    flow = _make_flow(tmp_path / "dicts")
    flow._panel.get_chain.return_value = (ChainEntry(kind="indexed", dict_id="existing", enabled=True),)

    chain = flow._with_dicts_at_top(["first", "second"])

    assert [entry.dict_id for entry in chain] == ["first", "second", "existing"]


def test_single_failed_zip_reports_an_issue_instead_of_an_added_box(tmp_path: Path, monkeypatch):
    flow = _make_flow(tmp_path / "dicts")
    issues: list[tuple[str, str]] = []
    flow._report_import_issue = lambda summary, details="": issues.append((summary, details))
    boxes: list[str] = []
    monkeypatch.setattr(f"{MOD}.QMessageBox.information", lambda *a, **kw: boxes.append("shown"))

    captured: dict = {}
    flow._run_chained_imports = lambda **kwargs: captured.update(kwargs)
    with patch(
        f"{MOD}.file_dialogs.pick_open_files",
        side_effect=lambda *a, on_done, **k: on_done([str(tmp_path / "one.zip")]),
    ):
        flow.add_dict()

    captured["on_finished"](_ChainedImportResult(successes=(), failures=((Path("one.zip"), "boom"),), cancelled=False))

    assert boxes == []
    assert issues and issues[0][1] == "boom"


def test_corrupt_saved_jmdict_zip_falls_back_to_configured_xml(tmp_path: Path):
    dicts_root = tmp_path / "dicts"
    slot = dicts_root / "jmdict-english"
    slot.mkdir(parents=True)
    (slot / "index.sqlite").write_bytes(b"not sqlite")
    source_zip = slot / "source.zip"
    source_zip.write_bytes(b"PK\x03\x04")
    xml = tmp_path / "JMdict_e"
    xml.write_text("<JMdict/>", encoding="utf-8")
    flow = _make_flow(dicts_root)
    flow._get_config().jmdict_path = xml
    flow._panel.request_resource_release.return_value = True
    flow._run_modal_import = MagicMock()
    worker = MagicMock()

    with (
        patch(f"{MOD}.ImportWorker.for_yomitan_repair") as yomitan,
        patch(f"{MOD}.ImportWorker.for_jmdict_repair", return_value=worker) as jmdict,
        patch(f"{MOD}.file_dialogs.pick_open_file") as picker,
    ):
        flow.reimport_dict("jmdict-english")

    yomitan.assert_not_called()
    jmdict.assert_called_once_with(xml, dicts_root)
    picker.assert_not_called()
    assert flow._run_modal_import.call_args.kwargs["worker"] is worker


def test_unrelated_saved_zip_is_not_pinned_to_slot(tmp_path: Path):
    dicts_root = tmp_path / "dicts"
    slot = dicts_root / "expected"
    source_zip = build_yomitan_zip(slot / "source.zip", title="Other Dictionary")
    flow = _make_flow(dicts_root)

    with (
        patch(f"{COMMON}.report_screen_issue") as reported,
        patch(f"{MOD}.ImportWorker.for_yomitan_repair") as yomitan,
    ):
        flow.reimport_dict("expected")

    reported.assert_called_once()
    yomitan.assert_not_called()
    assert source_zip.is_file()


def test_saved_zip_with_exact_derived_id_routes_to_slot_pinned_yomitan(tmp_path: Path):
    dicts_root = tmp_path / "dicts"
    source_zip = build_yomitan_zip(
        dicts_root / "expected" / "source.zip",
        title="Expected",
        revision="",
    )
    flow = _make_flow(dicts_root)
    flow._panel.request_resource_release.return_value = True
    flow._run_modal_import = MagicMock()
    worker = MagicMock()

    with patch(f"{MOD}.ImportWorker.for_yomitan_repair", return_value=worker) as yomitan:
        flow.reimport_dict("expected")

    yomitan.assert_called_once_with(source_zip, dicts_root, dict_id="expected")
    assert flow._run_modal_import.call_args.kwargs["worker"] is worker


def test_saved_catalog_zip_with_matching_title_base_routes_to_pinned_slot(tmp_path: Path):
    dicts_root = tmp_path / "dicts"
    _seed_slot(dicts_root, "jitendex", "Jitendex.org [2025-11-05]")
    source_zip = build_yomitan_zip(
        dicts_root / "jitendex" / "source.zip",
        title="Jitendex.org [2026-06-06]",
    )
    flow = _make_flow(dicts_root)
    flow._panel.request_resource_release.return_value = True
    flow._run_modal_import = MagicMock()
    worker = MagicMock()

    with patch(f"{MOD}.ImportWorker.for_yomitan_repair", return_value=worker) as yomitan:
        flow.reimport_dict("jitendex")

    yomitan.assert_called_once_with(source_zip, dicts_root, dict_id="jitendex")
    assert flow._run_modal_import.call_args.kwargs["worker"] is worker


def test_jmdict_reimport_falls_back_to_configured_xml(tmp_path: Path):
    dicts_root = tmp_path / "dicts"
    flow = _make_flow(dicts_root)
    xml = tmp_path / "JMdict_e"
    xml.write_text("<JMdict/>", encoding="utf-8")
    flow._get_config().jmdict_path = xml
    flow._panel.request_resource_release.return_value = True
    flow._run_modal_import = MagicMock()
    worker = MagicMock()

    with (
        patch(f"{MOD}.ImportWorker.for_yomitan_repair") as yomitan,
        patch(f"{MOD}.ImportWorker.for_jmdict_repair", return_value=worker) as jmdict,
    ):
        flow.reimport_dict("jmdict-english")

    yomitan.assert_not_called()
    jmdict.assert_called_once_with(xml, dicts_root)
    assert flow._run_modal_import.call_args.kwargs["worker"] is worker


def test_reimport_without_recoverable_source_reports_dialog(tmp_path: Path):
    flow = _make_flow(tmp_path / "dicts")

    with (
        patch(f"{COMMON}.report_screen_issue") as reported,
        patch(f"{MOD}.ImportWorker.for_yomitan_repair") as yomitan,
        patch(f"{MOD}.ImportWorker.for_jmdict_repair") as jmdict,
    ):
        flow.reimport_dict("broken")

    reported.assert_called_once()
    assert "recoverable source" in reported.call_args.args[1].summary.lower()
    yomitan.assert_not_called()
    jmdict.assert_not_called()


def test_add_dict_persist_failure_reports_partial_success_after_chain_commit(wired_window, monkeypatch, tmp_path):
    from anki_miner.gui.utils.config_manager import GUIConfigManager

    _window, _titles, tabs = wired_window
    flow = tabs["Settings"]._dict_import_flow
    events: list[str] = []
    monkeypatch.setattr(flow._panel, "get_chain", lambda: ())
    monkeypatch.setattr(flow._panel, "refresh_registry", lambda: None)
    monkeypatch.setattr(flow._panel, "set_chain", lambda _chain: events.append("chain"))

    def fail_persist(_config: AnkiMinerConfig) -> None:
        events.append("persist")
        raise RuntimeError("disk full")

    original_save_config = GUIConfigManager.save_config
    monkeypatch.setattr(GUIConfigManager, "save_config", fail_persist)
    worker = MagicMock(name="ImportWorker")
    worker.progress = MagicMock()
    worker.import_finished = MagicMock()
    worker.failed = MagicMock()
    worker.cancelled = MagicMock()
    worker.finished = MagicMock()
    worker.isRunning.return_value = False
    dialog = MagicMock()

    try:
        with (
            patch(
                f"{MOD}.file_dialogs.pick_open_files",
                side_effect=lambda *a, on_done, **k: on_done([str(tmp_path / "picked.zip")]),
            ),
            patch(f"{MOD}.ImportWorker.for_yomitan", return_value=worker),
            patch("anki_miner.gui.controllers.import_flow_common.QProgressDialog", return_value=dialog),
            patch("anki_miner.gui.controllers.import_flow_common.QTimer", return_value=MagicMock()),
            patch(f"{MOD}.QMessageBox.information") as info,
            patch(
                f"{COMMON}.report_screen_issue",
                side_effect=lambda *a, **k: events.append("warning"),
            ) as reported,
        ):
            flow.add_dict()
            worker.import_finished.connect.call_args[0][0]("promoted", {"entry_count": 7})

            assert events == []
            assert info.call_count == 0
            assert reported.call_count == 0

            worker.finished.connect.call_args[0][0]()
    finally:
        monkeypatch.setattr(GUIConfigManager, "save_config", original_save_config)

    assert events == ["chain", "persist", "warning"]
    assert info.call_count == 0
    assert reported.call_count == 1
    issue = reported.call_args.args[1]
    assert issue.summary == "The import finished, but the settings could not be updated."
    assert "disk full" not in issue.summary, "the exception belongs in Details (D24)"
    assert "disk full" in issue.details
    assert flow._panel._add_btn.isEnabled()


def test_import_notes_empty_when_clean():
    """A clean import contributes no trailing note (plan 4.7/4.8)."""
    flow = _make_flow(Path("/x"))
    assert flow._import_notes({"skipped_malformed": 0, "media_warnings": []}) == ""
    assert flow._import_notes({}) == ""


def test_import_notes_reports_malformed_and_media():
    """Malformed-skip count and media-warning count surface in the note."""
    flow = _make_flow(Path("/x"))
    note = flow._import_notes({"skipped_malformed": 5, "media_warnings": ["w1", "w2"]})
    assert "5" in note
    assert "malformed" in note
    assert "2" in note
    assert "media" in note.lower()


# --- catalog-slot pinned re-import guard --------------------------------------

from anki_miner.services.dictionary.storage import create_index, write_meta  # noqa: E402
from tests.fixtures.dictionary.build_yomitan_fixture import build_yomitan_zip  # noqa: E402


def _seed_slot(dicts_root: Path, dict_id: str, source_name: str) -> None:
    db = dicts_root / dict_id / "index.sqlite"
    db.parent.mkdir(parents=True, exist_ok=True)
    create_index(db)
    write_meta(db, {"source_name": source_name})


class TestCatalogSlotBaseMatches:
    def test_matches_same_base_newer_date(self, tmp_path: Path):
        flow = _make_flow(tmp_path / "dicts")
        _seed_slot(tmp_path / "dicts", "jitendex", "Jitendex.org [2025-11-05]")
        fresh = build_yomitan_zip(tmp_path / "src" / "j.zip", title="Jitendex.org [2026-06-06]")
        assert flow._catalog_slot_base_matches("jitendex", fresh) is True

    def test_rejects_different_base(self, tmp_path: Path):
        flow = _make_flow(tmp_path / "dicts")
        _seed_slot(tmp_path / "dicts", "jitendex", "Jitendex.org [2025-11-05]")
        wrong = build_yomitan_zip(tmp_path / "src" / "d.zip", title="Daijirin [2026-01-01]")
        assert flow._catalog_slot_base_matches("jitendex", wrong) is False

    def test_rejects_when_slot_not_on_disk(self, tmp_path: Path):
        flow = _make_flow(tmp_path / "dicts")
        fresh = build_yomitan_zip(tmp_path / "src" / "j.zip", title="Jitendex.org [2026-06-06]")
        assert flow._catalog_slot_base_matches("jitendex", fresh) is False
