"""Tests for PitchSettingsPanel.

Structural twin of test_frequency_settings_panel.py; the shared reorder /
tombstone-remove / async-scan machinery lives in ChainSettingsPanelBase and is
exercised in depth there. These cover the pitch panel's own hooks: rendering,
chain round-trip, ownership-proved removal, and the add/reimport signals.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QMessageBox

from anki_miner.config import PitchSourceEntry
from anki_miner.gui.widgets.panels.pitch_settings_panel import PitchSettingsPanel
from anki_miner.services.pitch_accent.registry import PitchSourceMeta
from anki_miner.services.pitch_accent.storage import SCHEMA_VERSION, build_index


def _make_source_on_disk(
    root: Path,
    source_id: str,
    *,
    fmt: str = "yomitan-pitch",
    source_name: str | None = None,
    entry_count: int = 100,
) -> Path:
    """Materialize a minimal on-disk pitch source with current schema."""
    source_dir = root / source_id
    source_dir.mkdir(parents=True, exist_ok=True)
    build_index(
        source_dir / "index.sqlite",
        [("ねこ", "猫", "1", "", "")],
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
    fmt: str = "yomitan-pitch",
    source_name: str | None = None,
    entry_count: int = 100,
    schema_ok: bool = True,
    version: int = SCHEMA_VERSION,
) -> PitchSourceMeta:
    """Build a PitchSourceMeta without touching disk."""
    return PitchSourceMeta(
        source_id=source_id,
        source_name=source_name or source_id,
        format=fmt,
        entry_count=entry_count,
        schema_ok=schema_ok,
        version=version,
        db_path=Path("/fake/index.sqlite"),
    )


@pytest.fixture
def confirm_remove(monkeypatch):
    """Auto-accept the 'Remove pitch source' QMessageBox confirmation."""
    monkeypatch.setattr(
        "anki_miner.gui.widgets.panels.pitch_settings_panel.QMessageBox.question",
        lambda *a, **kw: QMessageBox.StandardButton.Yes,
    )


def test_set_chain_renders_correct_row_count(qapp, qtbot, tmp_path):
    panel = PitchSettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    panel.set_chain(
        (PitchSourceEntry("a"), PitchSourceEntry("b", enabled=False)),
        registry_meta={"a": _make_meta("a"), "b": _make_meta("b")},
    )
    assert panel._list.count() == 2


def test_row_shows_name_format_and_count(qapp, qtbot, tmp_path):
    panel = PitchSettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    panel.set_chain(
        (PitchSourceEntry("nhk"),),
        registry_meta={"nhk": _make_meta("nhk", source_name="NHK Accent", entry_count=1234)},
    )
    from PyQt6.QtWidgets import QLabel

    row = panel._row_widget(0)
    texts = [label.text() for label in row.findChildren(QLabel)]
    assert any("NHK Accent" in t for t in texts)
    assert any("yomitan-pitch" in t for t in texts)
    assert any("1,234" in t for t in texts)


def test_missing_and_stale_sources_flagged_differently(qapp, qtbot, tmp_path):
    """Two failures, two repairs, two rows.

    Stale is present on disk and rebuildable from the copy saved at import, so
    it gets a Re-import button; missing has nothing to rebuild from, so its
    row says so rather than offering a button that only opens a file picker.
    """
    panel = PitchSettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    panel.set_chain(
        (PitchSourceEntry("gone"), PitchSourceEntry("stale")),
        registry_meta={"stale": _make_meta("stale", schema_ok=False, version=99)},
    )

    missing_row = panel._row_widget(0)
    stale_row = panel._row_widget(1)
    assert "missing" in missing_row.warning_text
    assert missing_row.repair_button is None
    assert "upgrade" in stale_row.warning_text
    assert stale_row.repair_button is not None


def test_row_re_import_button_requests_that_source(qapp, qtbot, tmp_path):
    panel = PitchSettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    requested: list[str] = []
    panel.reimport_source_requested.connect(requested.append)
    panel.set_chain(
        (PitchSourceEntry("stale"),),
        registry_meta={"stale": _make_meta("stale", schema_ok=False, version=99)},
    )

    panel._row_widget(0).repair_button.click()

    assert requested == ["stale"]


def test_reimport_all_button_emits_its_request(qapp, qtbot, tmp_path):
    panel = PitchSettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    fired: list[int] = []
    panel.reimport_all_requested.connect(lambda: fired.append(1))

    panel._reimport_btn.click()

    assert fired == [1]


def test_set_get_chain_round_trip(qapp, qtbot, tmp_path):
    panel = PitchSettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    chain = (PitchSourceEntry("a"), PitchSourceEntry("b", enabled=False))
    panel.set_chain(chain, registry_meta={"a": _make_meta("a"), "b": _make_meta("b")})
    assert panel.get_chain() == chain


def test_enable_toggle_reflected_in_get_chain(qapp, qtbot, tmp_path):
    panel = PitchSettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    panel.set_chain((PitchSourceEntry("a"),), registry_meta={"a": _make_meta("a")})
    panel._row_widget(0).checkbox.setChecked(False)
    assert panel.get_chain() == (PitchSourceEntry("a", enabled=False),)


def test_move_up_changes_order(qapp, qtbot, tmp_path):
    panel = PitchSettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    panel.set_chain(
        (PitchSourceEntry("first"), PitchSourceEntry("second")),
        registry_meta={"first": _make_meta("first"), "second": _make_meta("second")},
    )
    panel.move_up(1)
    assert [e.source_id for e in panel.get_chain()] == ["second", "first"]


def test_remove_confirmed_deletes_dir_and_entry(qapp, qtbot, tmp_path, confirm_remove):
    _make_source_on_disk(tmp_path, "doomed")
    from anki_miner.services._sqlite_index import write_ownership_marker

    write_ownership_marker(tmp_path / "doomed", "doomed", "pitch")
    panel = PitchSettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    panel.set_chain((PitchSourceEntry("doomed"),), registry_meta={"doomed": _make_meta("doomed")})

    panel.remove(0)
    qtbot.waitUntil(lambda: not panel.has_active_mutation(), timeout=5000)
    qtbot.waitUntil(lambda: not (tmp_path / "doomed").exists(), timeout=5000)

    assert panel.get_chain() == ()


def test_remove_foreign_dir_is_chain_only(qapp, qtbot, tmp_path, confirm_remove, monkeypatch):
    """A dir that can't be ownership-proved as a pitch slot is never deleted."""
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    (foreign / "keep.txt").write_text("user data", encoding="utf-8")
    panel = PitchSettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    panel.set_chain((PitchSourceEntry("foreign"),), registry_meta={"foreign": _make_meta("foreign")})

    panel.remove(0)
    qtbot.waitUntil(lambda: not panel.has_active_mutation(), timeout=5000)

    assert (foreign / "keep.txt").is_file()
    assert panel.get_chain() == ()


def test_add_button_emits_add_requested(qapp, qtbot, tmp_path):
    panel = PitchSettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    fired: list[None] = []
    panel.add_source_requested.connect(lambda: fired.append(None))
    panel._add_btn.click()
    assert fired == [None]


def test_registry_scan_discovers_disk_sources(qapp, qtbot, tmp_path):
    _make_source_on_disk(tmp_path, "ondisk", source_name="On Disk", entry_count=7)
    panel = PitchSettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    panel.set_chain((PitchSourceEntry("ondisk"),))  # no registry_meta → async scan
    qtbot.waitUntil(lambda: not panel._scan_in_flight, timeout=5000)
    row = panel._row_widget(0)
    assert row is not None and row.warning_text == ""
