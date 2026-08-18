"""SourceChainImportFlow.reimport_all — the frequency/pitch batch repair.

The schema-bump migration path: rebuild every chained source from the
``source.<ext>`` copy saved when it was imported, in place, without a file
picker. Structural twin of test_settings_tab_reimport_all.py (dictionaries),
which owns the deeper worker-chaining and cancellation cases; both families
run the same base implementation, so these cover what is specific to it -
the scan's skip rules, the summary, and the ``on_complete`` contract the
startup prompt chains three families with.

Worker instantiation is stubbed; completion callbacks are driven directly so
no real QThread runs.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QMessageBox

from anki_miner.config import AnkiMinerConfig, FreqEntry, PitchSourceEntry
from anki_miner.gui.widgets.settings_tab import SettingsTab
from anki_miner.services.frequency import storage as freq_storage
from anki_miner.services.pitch_accent import storage as pitch_storage


def _run_scan_sync(work, on_done, on_error):
    try:
        on_done(work())
    except Exception as exc:  # noqa: BLE001
        on_error(str(exc))


def _make_freq_source(root: Path, source_id: str, *, name: str, with_copy: bool = True) -> None:
    freq_storage.build_index(
        root / source_id / "index.sqlite",
        [("猫", "ねこ", 100, None)],
        {
            "schema_version": str(freq_storage.SCHEMA_VERSION),
            "format": "csv",
            "source_name": name,
            "entry_count": "1",
        },
    )
    if with_copy:
        (root / source_id / "source.csv").write_text("猫,100\n", encoding="utf-8")


def _make_pitch_source(root: Path, source_id: str, *, name: str, with_copy: bool = True) -> None:
    pitch_storage.build_index(
        root / source_id / "index.sqlite",
        [("ねこ", "猫", "1", "", "")],
        {
            "schema_version": str(pitch_storage.SCHEMA_VERSION),
            "format": "csv",
            "source_name": name,
            "source_revision": "",
            "import_date": "2026-01-01T00:00:00+00:00",
            "entry_count": "1",
        },
    )
    if with_copy:
        (root / source_id / "source.csv").write_text("ねこ,猫,1\n", encoding="utf-8")


#: Everything that differs between the two families, so each test body is
#: written once. ``repair`` is the ImportWorker classmethod the flow's
#: ``_make_repair_worker`` reaches for.
FAMILIES = {
    "frequency": {
        "make": _make_freq_source,
        "entry": FreqEntry,
        "repair": "for_source_repair",
        "panel": "frequency_panel",
        "flow": "_frequency_import_flow",
        "root": "freqs_root",
        "chain": "frequency_chain",
        "empty_message": "No frequency sources in the chain",
    },
    "pitch": {
        "make": _make_pitch_source,
        "entry": PitchSourceEntry,
        "repair": "for_pitch_source_repair",
        "panel": "pitch_panel",
        "flow": "_pitch_import_flow",
        "root": "pitch_root",
        "chain": "pitch_chain",
        "empty_message": "No pitch sources in the chain",
    },
}


@pytest.fixture
def tab(test_config: AnkiMinerConfig, tmp_path: Path, qtbot):
    cfg = replace(
        test_config,
        freqs_root=tmp_path / "freqs",
        pitch_root=tmp_path / "pitch",
        frequency_chain=(),
        pitch_chain=(),
    )
    widget = SettingsTab(cfg, suppress_optional_startup=True)
    for flow in (widget._frequency_import_flow, widget._pitch_import_flow):
        flow._run_latest_scan = _run_scan_sync
    qtbot.addWidget(widget)
    yield widget
    widget.deleteLater()


@pytest.fixture
def workers(monkeypatch):
    """Capture every ImportWorker the repair path builds."""
    instances: list[MagicMock] = []

    def _make_instance(*args, **kwargs):
        inst = MagicMock(name="ImportWorker")
        for signal in ("progress", "import_finished", "failed", "cancelled", "finished"):
            setattr(inst, signal, MagicMock())
        inst.is_cancelled = False
        inst.isRunning = MagicMock(return_value=True)
        instances.append(inst)
        return inst

    factories = {}
    for family, spec in FAMILIES.items():
        factory = MagicMock(name=spec["repair"], side_effect=_make_instance)
        monkeypatch.setattr(f"anki_miner.gui.workers.import_worker.ImportWorker.{spec['repair']}", factory)
        factories[family] = factory
    return {"factories": factories, "instances": instances}


def _silence_dialogs(monkeypatch) -> list[tuple[str, str]]:
    captured: list[tuple[str, str]] = []
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda parent, title, body, *a, **kw: captured.append((title, body)) or 0,
    )
    return captured


def _finish_worker(workers, idx: int = -1) -> None:
    """Emit the domain result, then the worker's native-finished barrier."""
    worker = workers["instances"][idx]
    worker.import_finished.connect.call_args.args[0]("source_id_ignored", {"entry_count": 1})
    worker.isRunning.return_value = False
    for connect_call in tuple(worker.finished.connect.call_args_list):
        connect_call.args[0]()


def _setup(tab, family, sources):
    """Materialize ``sources`` on disk and put them in the panel's chain."""
    spec = FAMILIES[family]
    root = getattr(tab.config, spec["root"])
    for source_id, name, with_copy in sources:
        spec["make"](root, source_id, name=name, with_copy=with_copy)
    panel = getattr(tab, spec["panel"])
    panel.set_chain(tuple(spec["entry"](source_id) for source_id, _name, _copy in sources))
    return getattr(tab, spec["flow"]), panel


@pytest.mark.parametrize("family", list(FAMILIES))
class TestReimportAll:
    def test_rebuilds_each_slot_in_place_from_its_saved_copy(self, tab, workers, monkeypatch, family):
        flow, _panel = _setup(tab, family, [("jpdb", "JPDB", True), ("bccwj", "BCCWJ", True)])
        summaries = _silence_dialogs(monkeypatch)
        emissions: list[object] = []
        tab.config_changed.connect(emissions.append)

        flow.reimport_all()

        factory = workers["factories"][family]
        # Strictly sequential: a domain result alone must not launch the next.
        assert factory.call_count == 1
        _finish_worker(workers)
        assert factory.call_count == 2
        _finish_worker(workers)

        for call in factory.call_args_list:
            args, kwargs = call
            assert Path(args[0]).name == "source.csv"
            # Pinned to the slot being rebuilt, so the repair lands in place
            # rather than forking a new directory the chain does not reference.
            assert kwargs["source_id"] == Path(args[0]).parent.name

        # The stored display name survives the rebuild.
        assert {call.kwargs["source_name"] for call in factory.call_args_list} == {"JPDB", "BCCWJ"}
        # One refresh for the batch, so cached services rebuild once.
        assert len(emissions) == 1
        title, body = summaries[-1]
        assert title == "Reimport All"
        assert "Reimported 2" in body
        assert "JPDB" in body and "BCCWJ" in body

    def test_copyless_slot_is_named_not_prompted_for(self, tab, workers, monkeypatch, family):
        """A batch that stopped on a file picker would strand the upgrade."""
        flow, _panel = _setup(tab, family, [("jpdb", "JPDB", True), ("legacy", "Legacy", False)])
        summaries = _silence_dialogs(monkeypatch)
        picked: list[object] = []
        monkeypatch.setattr(
            "anki_miner.gui.utils.file_dialogs.pick_open_file",
            lambda *a, **kw: picked.append(a) or None,
        )

        flow.reimport_all()
        _finish_worker(workers)

        assert workers["factories"][family].call_count == 1
        assert picked == []
        _title, body = summaries[-1]
        assert "Reimported 1" in body
        assert "Legacy" in body
        assert "Skipped" in body

    def test_empty_chain_says_so_and_starts_nothing(self, tab, workers, monkeypatch, family):
        spec = FAMILIES[family]
        flow, _panel = _setup(tab, family, [])
        summaries = _silence_dialogs(monkeypatch)

        flow.reimport_all()

        workers["factories"][family].assert_not_called()
        title, body = summaries[-1]
        assert title == "Nothing to reimport"
        assert spec["empty_message"] in body

    def test_only_ids_scopes_the_batch(self, tab, workers, monkeypatch, family):
        """The startup prompt repairs the stale slots its scan found, not the chain."""
        flow, _panel = _setup(tab, family, [("jpdb", "JPDB", True), ("bccwj", "BCCWJ", True)])
        summaries = _silence_dialogs(monkeypatch)

        flow.reimport_all(only_ids=frozenset({"bccwj"}))
        _finish_worker(workers)

        factory = workers["factories"][family]
        assert factory.call_count == 1
        assert factory.call_args.kwargs["source_id"] == "bccwj"
        _title, body = summaries[-1]
        assert "BCCWJ" in body
        assert "JPDB" not in body


@pytest.mark.parametrize("family", list(FAMILIES))
class TestOnCompleteContract:
    """``on_complete`` fires exactly once on every terminal path.

    The startup prompt runs one family's batch off the previous one's
    completion; a path that never fires strands the remaining families, and a
    double fire runs one of them twice.
    """

    def test_fires_once_after_a_finished_batch(self, tab, workers, monkeypatch, family):
        flow, _panel = _setup(tab, family, [("jpdb", "JPDB", True)])
        _silence_dialogs(monkeypatch)
        completions: list[int] = []

        flow.reimport_all(on_complete=lambda: completions.append(1))
        assert completions == []
        _finish_worker(workers)

        assert completions == [1]

    def test_fires_when_there_is_nothing_to_do(self, tab, workers, monkeypatch, family):
        flow, _panel = _setup(tab, family, [])
        _silence_dialogs(monkeypatch)
        completions: list[int] = []

        flow.reimport_all(on_complete=lambda: completions.append(1))

        assert completions == [1]

    def test_fires_when_the_resource_release_is_refused(self, tab, workers, monkeypatch, family):
        flow, panel = _setup(tab, family, [("jpdb", "JPDB", True)])
        _silence_dialogs(monkeypatch)
        monkeypatch.setattr(panel, "request_resource_release", lambda: False)
        monkeypatch.setattr(
            "anki_miner.gui.controllers.import_flow_common.report_screen_issue",
            lambda origin, issue: True,
        )
        completions: list[int] = []

        flow.reimport_all(on_complete=lambda: completions.append(1))

        assert completions == [1]
        workers["factories"][family].assert_not_called()


class TestTriggerReimportAll:
    """The startup prompt's entry point picks a family and its subtab."""

    @pytest.mark.parametrize(
        ("kind", "subtab", "flow_attr"),
        [
            ("dictionary", "dictionaries", "_dict_import_flow"),
            ("frequency", "frequency", "_frequency_import_flow"),
            ("pitch", "pitch", "_pitch_import_flow"),
            ("audio", "audio", "_audio_pack_import_flow"),
        ],
    )
    def test_dispatches_to_the_right_flow_and_subtab(self, tab, monkeypatch, kind, subtab, flow_attr):
        opened: list[str] = []
        monkeypatch.setattr(tab, "open_subtab", opened.append)
        called: list[dict] = []
        monkeypatch.setattr(
            getattr(tab, flow_attr),
            "reimport_all",
            lambda **kwargs: called.append(kwargs),
        )
        done = object()

        tab.trigger_reimport_all(frozenset({"x"}), kind=kind, on_complete=done)  # type: ignore[arg-type]

        assert opened == [subtab]
        assert called == [{"only_ids": frozenset({"x"}), "on_complete": done}]

    @pytest.mark.parametrize(
        "panel_attr",
        ["dictionary_panel", "frequency_panel", "pitch_panel", "audio_panel"],
    )
    def test_every_resource_panel_has_a_wired_reimport_all_button(self, tab, panel_attr):
        """The manual button and the startup prompt share one implementation.

        The connection itself is the assertion: Qt binds the slot at connect
        time, so patching the flow afterwards cannot observe the emission.
        """
        panel = getattr(tab, panel_attr)

        assert panel._reimport_btn.text() == "Reimport All"
        assert panel.receivers(panel.reimport_all_requested) == 1
