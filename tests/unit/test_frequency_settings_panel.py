"""Tests for FrequencySettingsPanel."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QLabel, QMessageBox

from anki_miner.config import FreqEntry
from anki_miner.gui.widgets.panels import frequency_settings_panel as fsp_mod
from anki_miner.gui.widgets.panels.frequency_settings_panel import FrequencySettingsPanel
from anki_miner.services.frequency.registry import FreqSourceMeta
from anki_miner.services.frequency.storage import SCHEMA_VERSION, build_index

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_source_on_disk(
    root: Path,
    source_id: str,
    *,
    fmt: str = "yomitan-freq",
    source_name: str | None = None,
    entry_count: int = 100,
) -> Path:
    """Materialize a minimal on-disk frequency source with current schema."""
    source_dir = root / source_id
    source_dir.mkdir(parents=True, exist_ok=True)
    db_path = source_dir / "index.sqlite"
    build_index(
        db_path,
        [("食べる", None, 1)],
        {
            "schema_version": str(SCHEMA_VERSION),
            "format": fmt,
            "source_name": source_name or source_id,
            "entry_count": str(entry_count),
        },
    )
    return source_dir


def _make_meta(
    source_id: str,
    *,
    fmt: str = "yomitan-freq",
    source_name: str | None = None,
    entry_count: int = 100,
    schema_ok: bool = True,
) -> FreqSourceMeta:
    """Build a FreqSourceMeta without touching disk."""
    return FreqSourceMeta(
        source_id=source_id,
        source_name=source_name or source_id,
        format=fmt,
        entry_count=entry_count,
        schema_ok=schema_ok,
        db_path=Path("/fake/index.sqlite"),
    )


@pytest.fixture
def confirm_remove(monkeypatch):
    """Auto-accept the 'Remove frequency source' QMessageBox confirmation."""
    monkeypatch.setattr(
        "anki_miner.gui.widgets.panels.frequency_settings_panel.QMessageBox.question",
        lambda *a, **kw: QMessageBox.StandardButton.Yes,
    )


@pytest.fixture
def decline_remove(monkeypatch):
    monkeypatch.setattr(
        "anki_miner.gui.widgets.panels.frequency_settings_panel.QMessageBox.question",
        lambda *a, **kw: QMessageBox.StandardButton.No,
    )


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def test_set_chain_renders_correct_row_count(qapp, qtbot, tmp_path):
    panel = FrequencySettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    panel.set_chain(
        (
            FreqEntry(source_id="jpdb", enabled=True),
            FreqEntry(source_id="bccwj", enabled=False),
        )
    )
    assert panel._list.count() == 2


def test_row_shows_format_and_entry_count(qapp, qtbot, tmp_path):
    meta = _make_meta("jpdb", fmt="yomitan-freq", source_name="JPDB", entry_count=5000)
    panel = FrequencySettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    panel.set_chain(
        (FreqEntry(source_id="jpdb", enabled=True),),
        registry_meta={"jpdb": meta},
    )
    row = panel._row_widget(0)
    assert row is not None
    texts = [lbl.text() for lbl in row.findChildren(QLabel)]
    assert any("JPDB" in t for t in texts), texts
    assert any("yomitan-freq" in t for t in texts), texts
    assert any("5,000" in t for t in texts), texts


def test_missing_source_badge_shown(qapp, qtbot, tmp_path):
    panel = FrequencySettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    # No meta supplied for a referenced source → missing.
    panel.set_chain(
        (FreqEntry(source_id="gone", enabled=True),),
        registry_meta={},
    )
    row = panel._row_widget(0)
    assert row is not None
    assert row.missing is True
    texts = [lbl.text() for lbl in row.findChildren(QLabel)]
    assert any("missing" in t for t in texts), texts


def test_schema_mismatch_renders_missing(qapp, qtbot, tmp_path):
    meta = _make_meta("old", schema_ok=False)
    panel = FrequencySettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    panel.set_chain(
        (FreqEntry(source_id="old", enabled=True),),
        registry_meta={"old": meta},
    )
    row = panel._row_widget(0)
    assert row is not None
    assert row.missing is True


def test_present_source_no_missing_badge(qapp, qtbot, tmp_path):
    meta = _make_meta("good")
    panel = FrequencySettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    panel.set_chain(
        (FreqEntry(source_id="good", enabled=True),),
        registry_meta={"good": meta},
    )
    row = panel._row_widget(0)
    assert row is not None
    assert row.missing is False


# ---------------------------------------------------------------------------
# Round-trip / state
# ---------------------------------------------------------------------------


def test_set_get_chain_round_trip(qapp, qtbot, tmp_path):
    panel = FrequencySettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    chain = (
        FreqEntry(source_id="a", enabled=True),
        FreqEntry(source_id="b", enabled=False),
    )
    panel.set_chain(chain, registry_meta={"a": _make_meta("a"), "b": _make_meta("b")})
    assert panel.get_chain() == chain


def test_enable_toggle_reflected_in_get_chain(qapp, qtbot, tmp_path):
    panel = FrequencySettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    panel.set_chain(
        (FreqEntry(source_id="a", enabled=True),),
        registry_meta={"a": _make_meta("a")},
    )
    row = panel._row_widget(0)
    assert row is not None
    row.checkbox.setChecked(False)
    assert panel.get_chain()[0].enabled is False


def test_global_enable_checkbox_read(qapp, qtbot, tmp_path):
    panel = FrequencySettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    panel.use_frequency_checkbox.setChecked(True)
    assert panel.use_frequency_checkbox.isChecked() is True
    panel.use_frequency_checkbox.setChecked(False)
    assert panel.use_frequency_checkbox.isChecked() is False


# ---------------------------------------------------------------------------
# Reorder
# ---------------------------------------------------------------------------


def test_move_up_changes_order(qapp, qtbot, tmp_path):
    panel = FrequencySettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    panel.set_chain(
        (
            FreqEntry(source_id="a", enabled=True),
            FreqEntry(source_id="b", enabled=True),
        ),
        registry_meta={"a": _make_meta("a"), "b": _make_meta("b")},
    )
    panel.move_up(1)
    ids = [e.source_id for e in panel.get_chain()]
    assert ids == ["b", "a"]


def test_move_down_changes_order(qapp, qtbot, tmp_path):
    panel = FrequencySettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    panel.set_chain(
        (
            FreqEntry(source_id="a", enabled=True),
            FreqEntry(source_id="b", enabled=True),
        ),
        registry_meta={"a": _make_meta("a"), "b": _make_meta("b")},
    )
    panel.move_down(0)
    ids = [e.source_id for e in panel.get_chain()]
    assert ids == ["b", "a"]


def test_move_preserves_enabled_state(qapp, qtbot, tmp_path):
    panel = FrequencySettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    panel.set_chain(
        (
            FreqEntry(source_id="a", enabled=False),
            FreqEntry(source_id="b", enabled=True),
        ),
        registry_meta={"a": _make_meta("a"), "b": _make_meta("b")},
    )
    panel.move_up(1)
    by_id = {e.source_id: e.enabled for e in panel.get_chain()}
    assert by_id == {"a": False, "b": True}


# ---------------------------------------------------------------------------
# Remove
# ---------------------------------------------------------------------------


def test_remove_confirmed_deletes_dir_and_entry(qapp, qtbot, tmp_path, confirm_remove):
    _make_source_on_disk(tmp_path, "jpdb")
    panel = FrequencySettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    panel.set_chain((FreqEntry(source_id="jpdb", enabled=True),))

    removed: list[int] = []
    panel.source_removed.connect(lambda: removed.append(1))

    panel.remove(0)

    # rmtree now runs off the GUI thread.
    qtbot.waitUntil(lambda: removed == [1], timeout=3000)
    assert panel.get_chain() == ()
    assert not (tmp_path / "jpdb").exists()


def test_remove_declined_keeps_entry(qapp, qtbot, tmp_path, decline_remove):
    _make_source_on_disk(tmp_path, "jpdb")
    panel = FrequencySettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    panel.set_chain((FreqEntry(source_id="jpdb", enabled=True),))

    panel.remove(0)

    assert len(panel.get_chain()) == 1
    assert (tmp_path / "jpdb").exists()


def test_remove_emits_chain_changed(qapp, qtbot, tmp_path, confirm_remove):
    _make_source_on_disk(tmp_path, "jpdb")
    panel = FrequencySettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    panel.set_chain((FreqEntry(source_id="jpdb", enabled=True),))

    changed: list[int] = []
    panel.chain_changed.connect(lambda: changed.append(1))

    panel.remove(0)
    qtbot.waitUntil(lambda: changed == [1], timeout=3000)


def test_remove_invalid_index_noop(qapp, qtbot, tmp_path):
    panel = FrequencySettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    panel.set_chain((FreqEntry(source_id="a", enabled=True),))
    panel.remove(5)  # out of range
    assert len(panel.get_chain()) == 1


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------


def test_add_button_emits_add_requested(qapp, qtbot, tmp_path):
    panel = FrequencySettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    fired: list[int] = []
    panel.add_source_requested.connect(lambda: fired.append(1))
    panel._add_btn.click()
    assert fired == [1]


def test_release_callback_blocks_remove(qapp, qtbot, tmp_path, confirm_remove, monkeypatch):
    _make_source_on_disk(tmp_path, "jpdb")
    warned: list[int] = []
    monkeypatch.setattr(
        fsp_mod.QMessageBox,
        "warning",
        lambda *a, **kw: warned.append(1),
    )
    panel = FrequencySettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    panel.set_chain((FreqEntry(source_id="jpdb", enabled=True),))
    panel.set_release_callback(lambda: False)  # mining in flight

    panel.remove(0)

    # Refused: entry kept, dir kept, warning shown.
    assert len(panel.get_chain()) == 1
    assert (tmp_path / "jpdb").exists()
    assert warned == [1]


# ---------------------------------------------------------------------------
# OVH disk-scan-off-thread — registry scan + remove rmtree run off the GUI thread
# ---------------------------------------------------------------------------


class TestOffThreadDiskWork:
    """First-show scan and Remove rmtree must run on a worker thread."""

    def test_first_show_scan_runs_off_gui_thread(self, qapp, qtbot, tmp_path, monkeypatch):
        import threading

        main_id = threading.get_ident()
        scan_threads: list[int] = []
        real_load = fsp_mod.FrequencySourceRegistry.load

        def _spy_load(self):
            scan_threads.append(threading.get_ident())
            return real_load(self)

        monkeypatch.setattr(fsp_mod.FrequencySourceRegistry, "load", _spy_load)

        _make_source_on_disk(tmp_path, "jpdb")
        panel = FrequencySettingsPanel(tmp_path)
        qtbot.addWidget(panel)
        panel.set_chain((FreqEntry(source_id="jpdb", enabled=True),))
        panel.show()

        qtbot.waitUntil(lambda: bool(scan_threads), timeout=3000)
        qtbot.waitUntil(lambda: not panel._scan_in_flight, timeout=3000)
        assert scan_threads and all(t != main_id for t in scan_threads), scan_threads

    def test_remove_rmtree_runs_off_gui_thread(self, qapp, qtbot, tmp_path, confirm_remove, monkeypatch):
        import threading

        main_id = threading.get_ident()
        rmtree_threads: list[int] = []
        real_rmtree = fsp_mod.shutil.rmtree

        def _spy_rmtree(path, *a, **kw):
            rmtree_threads.append(threading.get_ident())
            return real_rmtree(path, *a, **kw)

        monkeypatch.setattr(fsp_mod.shutil, "rmtree", _spy_rmtree)

        _make_source_on_disk(tmp_path, "jpdb")
        panel = FrequencySettingsPanel(tmp_path)
        qtbot.addWidget(panel)
        panel.set_chain((FreqEntry(source_id="jpdb", enabled=True),))

        panel.remove(0)
        qtbot.waitUntil(lambda: not (tmp_path / "jpdb").exists(), timeout=3000)
        assert rmtree_threads and all(t != main_id for t in rmtree_threads), rmtree_threads

    def test_remove_disables_then_reenables_button(self, qapp, qtbot, tmp_path, confirm_remove):
        _make_source_on_disk(tmp_path, "jpdb")
        panel = FrequencySettingsPanel(tmp_path)
        qtbot.addWidget(panel)
        panel.set_chain((FreqEntry(source_id="jpdb", enabled=True),))

        panel.remove(0)
        assert panel._remove_btn.isEnabled() is False
        qtbot.waitUntil(lambda: panel._remove_btn.isEnabled(), timeout=3000)
        assert not (tmp_path / "jpdb").exists()
