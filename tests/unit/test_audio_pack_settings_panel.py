"""Tests for AudioPackSettingsPanel."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication, QLabel, QMessageBox

from anki_miner.config import AudioSourceEntry
from anki_miner.gui.widgets.panels import audio_pack_settings_panel as asp_mod
from anki_miner.gui.widgets.panels.audio_pack_settings_panel import AudioPackSettingsPanel
from anki_miner.services.audio_packs.registry import AudioPackMeta
from anki_miner.services.audio_packs.storage import SCHEMA_VERSION, create_index, write_meta

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pack_on_disk(
    root: Path,
    pack_id: str,
    *,
    fmt: str = "ajt",
    source: str | None = None,
    entry_count: int = 100,
    pack_dir_exists: bool = True,
) -> Path:
    """Materialize a minimal on-disk audio pack with current schema_version."""
    pack_dir = root / pack_id
    pack_dir.mkdir(parents=True, exist_ok=True)
    db_path = pack_dir / "index.sqlite"
    create_index(db_path)
    audio_dir = pack_dir / "audio"
    if pack_dir_exists:
        audio_dir.mkdir(exist_ok=True)
    write_meta(
        db_path,
        {
            "schema_version": str(SCHEMA_VERSION),
            "format": fmt,
            "source": source or pack_id,
            "pack_id": pack_id,
            "entry_count": str(entry_count),
            "pack_dir": str(audio_dir),
        },
    )
    return pack_dir


def _make_meta(
    pack_id: str,
    *,
    fmt: str = "ajt",
    source: str | None = None,
    entry_count: int = 100,
    pack_dir_exists: bool = True,
    pack_dir: Path | None = None,
) -> AudioPackMeta:
    """Build an AudioPackMeta without touching disk."""
    return AudioPackMeta(
        pack_id=pack_id,
        source=source or pack_id,
        format=fmt,
        entry_count=entry_count,
        pack_dir=pack_dir or Path("/fake/audio"),
        pack_dir_exists=pack_dir_exists,
        db_path=Path("/fake/index.sqlite"),
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def confirm_remove(monkeypatch):
    """Auto-accept the 'Remove audio pack' QMessageBox confirmation."""
    monkeypatch.setattr(
        "anki_miner.gui.widgets.panels.audio_pack_settings_panel.QMessageBox.question",
        lambda *a, **kw: QMessageBox.StandardButton.Yes,
    )


def _patch_menu_exec(monkeypatch, action_label: str | None):
    """Stub QMenu.exec to return the action matching action_label.

    Returns a list that accumulates constructed QMenu instances.
    """
    constructed: list[object] = []
    real_init = __import__("PyQt6.QtWidgets", fromlist=["QMenu"]).QMenu.__init__

    def tracking_init(self, *args, **kwargs):
        real_init(self, *args, **kwargs)
        constructed.append(self)

    monkeypatch.setattr("PyQt6.QtWidgets.QMenu.__init__", tracking_init)

    def fake_exec(self, *_args, **_kwargs):
        if action_label is None:
            return None
        for action in self.actions():
            if action.text() == action_label:
                return action
        return None

    monkeypatch.setattr("PyQt6.QtWidgets.QMenu.exec", fake_exec)
    return constructed


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def test_set_chain_renders_correct_row_count(qapp, tmp_path):
    panel = AudioPackSettingsPanel(tmp_path)
    panel.set_chain(
        (
            AudioSourceEntry(kind="pack", pack_id="ajt-pack", enabled=True),
            AudioSourceEntry(kind="jpod101", pack_id=None, enabled=True),
        )
    )
    assert panel._list.count() == 2


def test_jpod101_row_shows_online_display_name(qapp, tmp_path):
    panel = AudioPackSettingsPanel(tmp_path)
    panel.set_chain((AudioSourceEntry(kind="jpod101", pack_id=None, enabled=True),))
    row = panel._row_widget(0)
    assert row is not None
    labels = row.findChildren(QLabel)
    texts = [lbl.text() for lbl in labels]
    assert any("JapanesePod101" in t for t in texts)
    assert any("online" in t for t in texts)


def test_pack_row_shows_format_and_entry_count(qapp, tmp_path):
    meta = _make_meta("ajt-pack", fmt="ajt", source="AJT Japanese", entry_count=5000)
    panel = AudioPackSettingsPanel(tmp_path)
    panel.set_chain(
        (AudioSourceEntry(kind="pack", pack_id="ajt-pack", enabled=True),),
        registry_meta={"ajt-pack": meta},
    )
    row = panel._row_widget(0)
    assert row is not None
    labels = row.findChildren(QLabel)
    texts = [lbl.text() for lbl in labels]
    assert any("ajt" in t for t in texts), texts
    assert any("5,000" in t for t in texts), texts


def test_missing_folder_badge_shown(qapp, tmp_path):
    meta = _make_meta("missing-pack", pack_dir_exists=False)
    panel = AudioPackSettingsPanel(tmp_path)
    panel.set_chain(
        (AudioSourceEntry(kind="pack", pack_id="missing-pack", enabled=True),),
        registry_meta={"missing-pack": meta},
    )
    row = panel._row_widget(0)
    assert row is not None
    assert row.dir_missing is True
    labels = row.findChildren(QLabel)
    texts = [lbl.text() for lbl in labels]
    assert any("folder missing" in t for t in texts), texts


def test_present_folder_no_missing_badge(qapp, tmp_path):
    meta = _make_meta("good-pack", pack_dir_exists=True)
    panel = AudioPackSettingsPanel(tmp_path)
    panel.set_chain(
        (AudioSourceEntry(kind="pack", pack_id="good-pack", enabled=True),),
        registry_meta={"good-pack": meta},
    )
    row = panel._row_widget(0)
    assert row is not None
    assert row.dir_missing is False
    labels = row.findChildren(QLabel)
    texts = [lbl.text() for lbl in labels]
    assert not any("folder missing" in t for t in texts)


# ---------------------------------------------------------------------------
# get_chain round-trip
# ---------------------------------------------------------------------------


def test_get_chain_round_trips_after_toggle(qapp, tmp_path):
    panel = AudioPackSettingsPanel(tmp_path)
    panel.set_chain(
        (
            AudioSourceEntry(kind="pack", pack_id="a", enabled=True),
            AudioSourceEntry(kind="jpod101", pack_id=None, enabled=True),
        )
    )
    row = panel._row_widget(0)
    assert row is not None
    row.checkbox.setChecked(False)

    chain = panel.get_chain()
    assert chain[0].enabled is False
    assert chain[1].enabled is True


def test_get_chain_round_trips_after_reorder(qapp, tmp_path):
    panel = AudioPackSettingsPanel(tmp_path)
    panel.set_chain(
        (
            AudioSourceEntry(kind="pack", pack_id="a", enabled=True),
            AudioSourceEntry(kind="pack", pack_id="b", enabled=True),
            AudioSourceEntry(kind="jpod101", pack_id=None, enabled=True),
        )
    )
    panel.move_up(1)  # b to top
    chain = panel.get_chain()
    assert chain[0].pack_id == "b"
    assert chain[1].pack_id == "a"


# ---------------------------------------------------------------------------
# Reorder
# ---------------------------------------------------------------------------


def test_move_up_moves_row_and_emits_signal(qapp, tmp_path):
    panel = AudioPackSettingsPanel(tmp_path)
    panel.set_chain(
        (
            AudioSourceEntry(kind="pack", pack_id="a", enabled=True),
            AudioSourceEntry(kind="pack", pack_id="b", enabled=True),
        )
    )
    events: list[str] = []
    panel.chain_changed.connect(lambda: events.append("changed"))

    panel.move_up(1)
    chain = panel.get_chain()
    assert chain[0].pack_id == "b"
    assert chain[1].pack_id == "a"
    assert events == ["changed"]


def test_move_down_moves_row_and_emits_signal(qapp, tmp_path):
    panel = AudioPackSettingsPanel(tmp_path)
    panel.set_chain(
        (
            AudioSourceEntry(kind="pack", pack_id="a", enabled=True),
            AudioSourceEntry(kind="pack", pack_id="b", enabled=True),
        )
    )
    events: list[str] = []
    panel.chain_changed.connect(lambda: events.append("changed"))

    panel.move_down(0)
    chain = panel.get_chain()
    assert chain[0].pack_id == "b"
    assert chain[1].pack_id == "a"
    assert events == ["changed"]


def test_edge_reorder_calls_are_noops(qapp, tmp_path):
    panel = AudioPackSettingsPanel(tmp_path)
    panel.set_chain(
        (
            AudioSourceEntry(kind="pack", pack_id="a", enabled=True),
            AudioSourceEntry(kind="jpod101", pack_id=None, enabled=True),
        )
    )
    events: list[str] = []
    panel.chain_changed.connect(lambda: events.append("changed"))

    panel.move_up(0)
    panel.move_down(1)
    panel.move_up(-1)
    panel.move_down(-1)
    panel.remove(-1)

    assert events == []
    chain = panel.get_chain()
    assert chain[0].pack_id == "a"
    assert chain[1].kind == "jpod101"


def test_checkbox_toggle_preserved_on_reorder(qapp, tmp_path):
    """get_chain() re-sync before mutation must preserve toggle state."""
    panel = AudioPackSettingsPanel(tmp_path)
    panel.set_chain(
        (
            AudioSourceEntry(kind="pack", pack_id="a", enabled=True),
            AudioSourceEntry(kind="pack", pack_id="b", enabled=True),
            AudioSourceEntry(kind="jpod101", pack_id=None, enabled=True),
        )
    )

    row_b = panel._row_widget(1)
    assert row_b is not None
    row_b.checkbox.setChecked(False)

    panel.move_up(1)
    chain = panel.get_chain()
    assert chain[0].pack_id == "b"
    assert chain[0].enabled is False
    assert chain[1].pack_id == "a"
    assert chain[1].enabled is True


# ---------------------------------------------------------------------------
# Remove
# ---------------------------------------------------------------------------


def test_jpod101_row_not_removable(qapp, tmp_path):
    panel = AudioPackSettingsPanel(tmp_path)
    panel.set_chain(
        (
            AudioSourceEntry(kind="pack", pack_id="a", enabled=True),
            AudioSourceEntry(kind="jpod101", pack_id=None, enabled=True),
        )
    )
    events: list[str] = []
    panel.chain_changed.connect(lambda: events.append("changed"))

    panel.remove(1)  # jpod101 → no-op
    assert len(panel.get_chain()) == 2
    assert events == []


def test_pack_row_removable_emits_signals(qapp, tmp_path, confirm_remove):
    pack_dir = tmp_path / "a"
    pack_dir.mkdir()
    (pack_dir / "index.sqlite").write_bytes(b"placeholder")

    panel = AudioPackSettingsPanel(tmp_path)
    panel.set_chain(
        (
            AudioSourceEntry(kind="pack", pack_id="a", enabled=True),
            AudioSourceEntry(kind="jpod101", pack_id=None, enabled=True),
        )
    )

    changed: list[None] = []
    removed: list[None] = []
    panel.chain_changed.connect(lambda: changed.append(None))
    panel.pack_removed.connect(lambda: removed.append(None))

    panel.remove(0)

    chain = panel.get_chain()
    assert [e.kind for e in chain] == ["jpod101"]
    assert changed == [None]
    assert removed == [None]


def test_remove_deletes_index_dir_on_disk(qapp, tmp_path, confirm_remove):
    """remove() must delete packs_root/<pack_id>/ (the index dir)."""
    pack_dir = tmp_path / "a"
    pack_dir.mkdir()
    (pack_dir / "index.sqlite").write_bytes(b"placeholder")

    panel = AudioPackSettingsPanel(tmp_path)
    panel.set_chain(
        (
            AudioSourceEntry(kind="pack", pack_id="a", enabled=True),
            AudioSourceEntry(kind="jpod101", pack_id=None, enabled=True),
        )
    )

    panel.remove(0)

    assert not pack_dir.exists(), "remove() must rmtree the index folder"
    assert [e.kind for e in panel.get_chain()] == ["jpod101"]


def test_remove_cancelled_keeps_pack_and_chain(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr(
        "anki_miner.gui.widgets.panels.audio_pack_settings_panel.QMessageBox.question",
        lambda *a, **kw: QMessageBox.StandardButton.No,
    )
    pack_dir = tmp_path / "a"
    pack_dir.mkdir()
    (pack_dir / "index.sqlite").write_bytes(b"placeholder")

    panel = AudioPackSettingsPanel(tmp_path)
    panel.set_chain(
        (
            AudioSourceEntry(kind="pack", pack_id="a", enabled=True),
            AudioSourceEntry(kind="jpod101", pack_id=None, enabled=True),
        )
    )

    events: list[None] = []
    panel.chain_changed.connect(lambda: events.append(None))
    removed: list[None] = []
    panel.pack_removed.connect(lambda: removed.append(None))

    panel.remove(0)

    assert pack_dir.exists(), "cancel must not touch disk"
    assert [e.pack_id for e in panel.get_chain()[:1]] == ["a"]
    assert events == []
    assert removed == []


def test_remove_tolerates_missing_index_folder(qapp, tmp_path, confirm_remove):
    """If the index folder is already gone, remove() drops the in-memory entry."""
    panel = AudioPackSettingsPanel(tmp_path)
    panel.set_chain(
        (
            AudioSourceEntry(kind="pack", pack_id="ghost", enabled=True),
            AudioSourceEntry(kind="jpod101", pack_id=None, enabled=True),
        )
    )

    panel.remove(0)

    assert [e.kind for e in panel.get_chain()] == ["jpod101"]


def test_remove_failed_rmtree_does_not_emit_pack_removed(qapp, monkeypatch, tmp_path, confirm_remove):
    pack_dir = tmp_path / "a"
    pack_dir.mkdir()
    (pack_dir / "index.sqlite").write_bytes(b"placeholder")

    monkeypatch.setattr(
        "anki_miner.gui.widgets.panels.audio_pack_settings_panel.QMessageBox.warning",
        lambda *a, **kw: QMessageBox.StandardButton.Ok,
    )

    def _always_fail(*args, **kwargs):
        raise PermissionError("simulated locked file")

    monkeypatch.setattr(asp_mod, "_robust_rmtree", _always_fail)

    panel = AudioPackSettingsPanel(tmp_path)
    panel.set_chain(
        (
            AudioSourceEntry(kind="pack", pack_id="a", enabled=True),
            AudioSourceEntry(kind="jpod101", pack_id=None, enabled=True),
        )
    )

    removed: list[None] = []
    panel.pack_removed.connect(lambda: removed.append(None))

    panel.remove(0)
    assert removed == []
    assert [e.pack_id for e in panel.get_chain()[:1]] == ["a"], "failed remove must leave chain intact"


def test_remove_retries_on_transient_permission_error(qapp, monkeypatch, tmp_path, confirm_remove):
    """_robust_rmtree retry path: first call raises PermissionError, second succeeds."""
    pack_dir = tmp_path / "a"
    pack_dir.mkdir()
    (pack_dir / "index.sqlite").write_bytes(b"placeholder")

    call_count = [0]

    def _fail_once(target, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            raise PermissionError("simulated transient lock")
        # Second call: actually remove so pack_dir.exists() becomes False.
        import shutil as _shutil

        _shutil.rmtree(target)

    monkeypatch.setattr(asp_mod, "_robust_rmtree", _fail_once)
    monkeypatch.setattr(
        "anki_miner.gui.widgets.panels.audio_pack_settings_panel.QMessageBox.warning",
        lambda *a, **kw: QMessageBox.StandardButton.Ok,
    )

    panel = AudioPackSettingsPanel(tmp_path)
    panel.set_chain(
        (
            AudioSourceEntry(kind="pack", pack_id="a", enabled=True),
            AudioSourceEntry(kind="jpod101", pack_id=None, enabled=True),
        )
    )

    removed: list[None] = []
    panel.pack_removed.connect(lambda: removed.append(None))

    # First remove → _robust_rmtree raises → pack stays, no signal.
    panel.remove(0)
    assert removed == [], "first attempt raised — pack_removed must not fire"
    assert [e.pack_id for e in panel.get_chain()[:1]] == ["a"]

    # Second remove → _robust_rmtree succeeds → pack gone, signal fires.
    panel.remove(0)
    assert removed == [None], "second attempt succeeded — pack_removed must fire"
    assert [e.kind for e in panel.get_chain()] == ["jpod101"]


def test_remove_confirm_dialog_mentions_audio_files_untouched(qapp, monkeypatch, tmp_path):
    """The confirm dialog must reassure the user that audio files are untouched."""
    bodies: list[str] = []

    def _capture_question(_parent, _title, body, *args, **kwargs):
        bodies.append(body)
        return QMessageBox.StandardButton.No  # cancel so no actual deletion

    monkeypatch.setattr(
        "anki_miner.gui.widgets.panels.audio_pack_settings_panel.QMessageBox.question",
        _capture_question,
    )

    pack_dir = tmp_path / "a"
    pack_dir.mkdir()

    panel = AudioPackSettingsPanel(tmp_path)
    panel.set_chain((AudioSourceEntry(kind="pack", pack_id="a", enabled=True),))
    panel.remove(0)

    assert bodies, "confirm dialog should have been shown"
    body = bodies[0]
    assert (
        "audio" in body.lower() or "untouched" in body.lower()
    ), f"Dialog body should mention audio files are safe: {body!r}"


# ---------------------------------------------------------------------------
# Checkbox → chain_changed
# ---------------------------------------------------------------------------


def test_checkbox_toggle_emits_chain_changed(qapp, tmp_path):
    panel = AudioPackSettingsPanel(tmp_path)
    panel.set_chain(
        (
            AudioSourceEntry(kind="pack", pack_id="a", enabled=True),
            AudioSourceEntry(kind="jpod101", pack_id=None, enabled=True),
        )
    )
    events: list[None] = []
    panel.chain_changed.connect(lambda: events.append(None))

    row = panel._row_widget(0)
    assert row is not None
    row.checkbox.setChecked(False)

    assert events == [None]


def test_checkbox_reflected_in_get_chain(qapp, tmp_path):
    panel = AudioPackSettingsPanel(tmp_path)
    panel.set_chain((AudioSourceEntry(kind="pack", pack_id="a", enabled=True),))

    row = panel._row_widget(0)
    assert row is not None
    row.checkbox.setChecked(False)

    chain = panel.get_chain()
    assert chain[0].enabled is False


# ---------------------------------------------------------------------------
# Add button
# ---------------------------------------------------------------------------


def test_add_button_emits_add_pack_requested(qapp, tmp_path):
    panel = AudioPackSettingsPanel(tmp_path)
    fired: list[None] = []
    panel.add_pack_requested.connect(lambda: fired.append(None))

    panel._add_btn.click()

    assert fired == [None]


# ---------------------------------------------------------------------------
# Context menu
# ---------------------------------------------------------------------------


def test_right_click_pack_row_emits_reimport_signal(qapp, monkeypatch, tmp_path):
    _make_pack_on_disk(tmp_path, "ajt-pack", fmt="ajt", source="AJT Japanese")
    panel = AudioPackSettingsPanel(tmp_path)
    panel.set_chain(
        (
            AudioSourceEntry(kind="pack", pack_id="ajt-pack", enabled=True),
            AudioSourceEntry(kind="jpod101", pack_id=None, enabled=True),
        )
    )

    constructed = _patch_menu_exec(monkeypatch, "Re-import…")

    emitted: list[str] = []
    panel.reimport_pack_requested.connect(emitted.append)

    item = panel._list.item(0)
    pos = panel._list.visualItemRect(item).center()
    panel._on_row_context_menu(pos)

    assert len(constructed) == 1
    assert emitted == ["ajt-pack"]


def test_right_click_jpod101_row_shows_no_menu(qapp, monkeypatch, tmp_path):
    panel = AudioPackSettingsPanel(tmp_path)
    panel.set_chain((AudioSourceEntry(kind="jpod101", pack_id=None, enabled=True),))

    constructed = _patch_menu_exec(monkeypatch, "Re-import…")

    emitted: list[str] = []
    panel.reimport_pack_requested.connect(emitted.append)

    item = panel._list.item(0)
    pos = panel._list.visualItemRect(item).center()
    panel._on_row_context_menu(pos)

    assert constructed == [], "jpod101 row must not open a context menu"
    assert emitted == []


def test_right_click_remove_action_removes_pack(qapp, monkeypatch, tmp_path, confirm_remove):
    """Right-click → Remove delegates to self.remove()."""
    _make_pack_on_disk(tmp_path, "a", fmt="ajt", source="Pack A")
    panel = AudioPackSettingsPanel(tmp_path)
    panel.set_chain((AudioSourceEntry(kind="pack", pack_id="a", enabled=True),))

    _patch_menu_exec(monkeypatch, "Remove")

    removed: list[None] = []
    panel.pack_removed.connect(lambda: removed.append(None))

    item = panel._list.item(0)
    pos = panel._list.visualItemRect(item).center()
    panel._on_row_context_menu(pos)

    assert removed == [None]
    assert panel._list.count() == 0


def test_right_click_pack_row_no_meta_shows_no_menu(qapp, monkeypatch, tmp_path):
    """Context menu is skipped when registry meta lookup returns None for the pack."""
    # Use registry_meta={} so the pack_id has no entry — meta lookup returns None.
    panel = AudioPackSettingsPanel(tmp_path)
    panel.set_chain(
        (AudioSourceEntry(kind="pack", pack_id="unknown-pack", enabled=True),),
        registry_meta={},
    )

    constructed = _patch_menu_exec(monkeypatch, "Re-import…")

    emitted: list[str] = []
    panel.reimport_pack_requested.connect(emitted.append)

    item = panel._list.item(0)
    pos = panel._list.visualItemRect(item).center()
    panel._on_row_context_menu(pos)

    assert constructed == [], "no meta → context menu must not open"
    assert emitted == []


# ---------------------------------------------------------------------------
# set_chain with registry_meta
# ---------------------------------------------------------------------------


def test_set_chain_with_registry_meta_uses_injected_meta(qapp, tmp_path):
    """set_chain(registry_meta=...) must use the supplied meta, not scan disk."""
    meta = _make_meta("nhk", fmt="nhk16", source="NHK Daily", entry_count=999)
    panel = AudioPackSettingsPanel(tmp_path)
    panel.set_chain(
        (AudioSourceEntry(kind="pack", pack_id="nhk", enabled=True),),
        registry_meta={"nhk": meta},
    )

    row = panel._row_widget(0)
    assert row is not None
    labels = row.findChildren(QLabel)
    texts = [lbl.text() for lbl in labels]
    assert any("NHK Daily" in t for t in texts), texts
    assert any("nhk16" in t for t in texts), texts
    assert any("999" in t for t in texts), texts


# ---------------------------------------------------------------------------
# chain_changed on reorder + remove sequence
# ---------------------------------------------------------------------------


def test_chain_changed_emits_on_reorder_remove_and_toggle(qapp, monkeypatch, tmp_path, confirm_remove):
    panel = AudioPackSettingsPanel(tmp_path)
    panel.set_chain(
        (
            AudioSourceEntry(kind="pack", pack_id="a", enabled=True),
            AudioSourceEntry(kind="pack", pack_id="b", enabled=True),
            AudioSourceEntry(kind="jpod101", pack_id=None, enabled=True),
        )
    )

    events: list[str] = []
    panel.chain_changed.connect(lambda: events.append("changed"))

    panel.move_up(1)
    assert events == ["changed"]

    panel.move_down(0)
    assert events == ["changed", "changed"]

    panel.remove(0)
    assert events == ["changed", "changed", "changed"]

    row = panel._row_widget(0)
    assert row is not None
    row.checkbox.setChecked(not row.checkbox.isChecked())
    assert events[-1] == "changed"
    assert len(events) == 4
