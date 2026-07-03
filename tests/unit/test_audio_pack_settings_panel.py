"""Tests for AudioPackSettingsPanel."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QDialogButtonBox, QLabel, QMessageBox

from anki_miner.config import AnkiMinerConfig, AudioSourceEntry
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


def test_set_chain_renders_correct_row_count(qapp, qtbot, tmp_path):
    panel = AudioPackSettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    panel.set_chain(
        (
            AudioSourceEntry(kind="pack", pack_id="ajt-pack", enabled=True),
            AudioSourceEntry(kind="jpod101", pack_id=None, enabled=True),
        )
    )
    assert panel._list.count() == 2


def test_jpod101_row_shows_online_display_name(qapp, qtbot, tmp_path):
    panel = AudioPackSettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    panel.set_chain((AudioSourceEntry(kind="jpod101", pack_id=None, enabled=True),))
    row = panel._row_widget(0)
    assert row is not None
    labels = row.findChildren(QLabel)
    texts = [lbl.text() for lbl in labels]
    assert any("JapanesePod101" in t for t in texts)
    assert any("online" in t for t in texts)


def test_pack_row_shows_format_and_entry_count(qapp, qtbot, tmp_path):
    meta = _make_meta("ajt-pack", fmt="ajt", source="AJT Japanese", entry_count=5000)
    panel = AudioPackSettingsPanel(tmp_path)
    qtbot.addWidget(panel)
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


def test_missing_folder_badge_shown(qapp, qtbot, tmp_path):
    meta = _make_meta("missing-pack", pack_dir_exists=False)
    panel = AudioPackSettingsPanel(tmp_path)
    qtbot.addWidget(panel)
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


def test_present_folder_no_missing_badge(qapp, qtbot, tmp_path):
    meta = _make_meta("good-pack", pack_dir_exists=True)
    panel = AudioPackSettingsPanel(tmp_path)
    qtbot.addWidget(panel)
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
# Google Translate (googletts) built-in row
# ---------------------------------------------------------------------------


def test_googletts_row_shows_synthetic_tts_display_name(qapp, qtbot, tmp_path):
    panel = AudioPackSettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    panel.set_chain((AudioSourceEntry(kind="googletts", pack_id=None, enabled=True),))
    row = panel._row_widget(0)
    assert row is not None
    labels = row.findChildren(QLabel)
    texts = [lbl.text() for lbl in labels]
    assert any("Google Translate (synthetic TTS)" in t for t in texts), texts
    assert any("online" in t for t in texts), texts


def test_googletts_row_reflects_disabled_state(qapp, qtbot, tmp_path):
    panel = AudioPackSettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    panel.set_chain((AudioSourceEntry(kind="googletts", pack_id=None, enabled=False),))
    row = panel._row_widget(0)
    assert row is not None
    assert row.checkbox.isChecked() is False


def test_googletts_row_not_removable(qapp, qtbot, tmp_path):
    panel = AudioPackSettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    panel.set_chain(
        (
            AudioSourceEntry(kind="pack", pack_id="a", enabled=True),
            AudioSourceEntry(kind="googletts", pack_id=None, enabled=True),
        )
    )
    events: list[str] = []
    panel.chain_changed.connect(lambda: events.append("changed"))

    panel.remove(1)  # googletts → no-op
    chain = panel.get_chain()
    assert len(chain) == 2
    assert any(e.kind == "googletts" for e in chain)
    assert events == []


def test_googletts_toggle_round_trips_in_get_chain(qapp, qtbot, tmp_path):
    panel = AudioPackSettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    panel.set_chain((AudioSourceEntry(kind="googletts", pack_id=None, enabled=False),))

    row = panel._row_widget(0)
    assert row is not None
    row.checkbox.setChecked(True)

    chain = panel.get_chain()
    assert chain[0].kind == "googletts"
    assert chain[0].enabled is True


def test_googletts_reorder_works(qapp, qtbot, tmp_path):
    panel = AudioPackSettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    panel.set_chain(
        (
            AudioSourceEntry(kind="jpod101", pack_id=None, enabled=True),
            AudioSourceEntry(kind="googletts", pack_id=None, enabled=True),
        )
    )
    panel.move_up(1)  # googletts to top
    chain = panel.get_chain()
    assert chain[0].kind == "googletts"
    assert chain[1].kind == "jpod101"


def test_right_click_googletts_row_shows_no_menu(qapp, qtbot, monkeypatch, tmp_path):
    panel = AudioPackSettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    panel.set_chain((AudioSourceEntry(kind="googletts", pack_id=None, enabled=True),))

    constructed = _patch_menu_exec(monkeypatch, "Re-import…")

    emitted: list[str] = []
    panel.reimport_pack_requested.connect(emitted.append)

    item = panel._list.item(0)
    pos = panel._list.visualItemRect(item).center()
    panel._on_row_context_menu(pos)

    assert constructed == [], "googletts row must not open a context menu"
    assert emitted == []


# ---------------------------------------------------------------------------
# get_chain round-trip
# ---------------------------------------------------------------------------


def test_get_chain_round_trips_after_toggle(qapp, qtbot, tmp_path):
    panel = AudioPackSettingsPanel(tmp_path)
    qtbot.addWidget(panel)
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


def test_get_chain_round_trips_after_reorder(qapp, qtbot, tmp_path):
    panel = AudioPackSettingsPanel(tmp_path)
    qtbot.addWidget(panel)
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


def test_move_up_moves_row_and_emits_signal(qapp, qtbot, tmp_path):
    panel = AudioPackSettingsPanel(tmp_path)
    qtbot.addWidget(panel)
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


def test_move_down_moves_row_and_emits_signal(qapp, qtbot, tmp_path):
    panel = AudioPackSettingsPanel(tmp_path)
    qtbot.addWidget(panel)
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


def test_edge_reorder_calls_are_noops(qapp, qtbot, tmp_path):
    panel = AudioPackSettingsPanel(tmp_path)
    qtbot.addWidget(panel)
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


def test_checkbox_toggle_preserved_on_reorder(qapp, qtbot, tmp_path):
    """get_chain() re-sync before mutation must preserve toggle state."""
    panel = AudioPackSettingsPanel(tmp_path)
    qtbot.addWidget(panel)
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


def test_jpod101_row_not_removable(qapp, qtbot, tmp_path):
    panel = AudioPackSettingsPanel(tmp_path)
    qtbot.addWidget(panel)
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


def test_pack_row_removable_emits_signals(qapp, qtbot, tmp_path, confirm_remove):
    pack_dir = tmp_path / "a"
    pack_dir.mkdir()
    (pack_dir / "index.sqlite").write_bytes(b"placeholder")

    panel = AudioPackSettingsPanel(tmp_path)
    qtbot.addWidget(panel)
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

    # rmtree now runs off the GUI thread.
    qtbot.waitUntil(lambda: removed == [None], timeout=3000)
    chain = panel.get_chain()
    assert [e.kind for e in chain] == ["jpod101"]
    assert changed == [None]


def test_remove_deletes_index_dir_on_disk(qapp, qtbot, tmp_path, confirm_remove):
    """remove() must delete packs_root/<pack_id>/ (the index dir)."""
    pack_dir = tmp_path / "a"
    pack_dir.mkdir()
    (pack_dir / "index.sqlite").write_bytes(b"placeholder")

    panel = AudioPackSettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    panel.set_chain(
        (
            AudioSourceEntry(kind="pack", pack_id="a", enabled=True),
            AudioSourceEntry(kind="jpod101", pack_id=None, enabled=True),
        )
    )

    panel.remove(0)

    # rmtree now runs off the GUI thread.
    qtbot.waitUntil(lambda: not pack_dir.exists(), timeout=3000)
    assert [e.kind for e in panel.get_chain()] == ["jpod101"]


def test_remove_cancelled_keeps_pack_and_chain(qapp, qtbot, monkeypatch, tmp_path):
    monkeypatch.setattr(
        "anki_miner.gui.widgets.panels.audio_pack_settings_panel.QMessageBox.question",
        lambda *a, **kw: QMessageBox.StandardButton.No,
    )
    pack_dir = tmp_path / "a"
    pack_dir.mkdir()
    (pack_dir / "index.sqlite").write_bytes(b"placeholder")

    panel = AudioPackSettingsPanel(tmp_path)
    qtbot.addWidget(panel)
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


def test_remove_tolerates_missing_index_folder(qapp, qtbot, tmp_path, confirm_remove):
    """If the index folder is already gone, remove() drops the in-memory entry."""
    panel = AudioPackSettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    panel.set_chain(
        (
            AudioSourceEntry(kind="pack", pack_id="ghost", enabled=True),
            AudioSourceEntry(kind="jpod101", pack_id=None, enabled=True),
        )
    )

    panel.remove(0)

    assert [e.kind for e in panel.get_chain()] == ["jpod101"]


def test_remove_failed_rmtree_does_not_emit_pack_removed(qapp, qtbot, monkeypatch, tmp_path, confirm_remove):
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
    qtbot.addWidget(panel)
    panel.set_chain(
        (
            AudioSourceEntry(kind="pack", pack_id="a", enabled=True),
            AudioSourceEntry(kind="jpod101", pack_id=None, enabled=True),
        )
    )

    removed: list[None] = []
    panel.pack_removed.connect(lambda: removed.append(None))

    panel.remove(0)
    # The off-thread rmtree fails; wait for the error handler to re-enable the
    # Remove button (proof the error callback ran on the GUI thread).
    qtbot.waitUntil(lambda: panel._remove_btn.isEnabled(), timeout=3000)
    assert removed == []
    assert [e.pack_id for e in panel.get_chain()[:1]] == ["a"], "failed remove must leave chain intact"


def test_remove_retries_on_transient_permission_error(qapp, qtbot, monkeypatch, tmp_path, confirm_remove):
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
    qtbot.addWidget(panel)
    panel.set_chain(
        (
            AudioSourceEntry(kind="pack", pack_id="a", enabled=True),
            AudioSourceEntry(kind="jpod101", pack_id=None, enabled=True),
        )
    )

    removed: list[None] = []
    panel.pack_removed.connect(lambda: removed.append(None))

    # First remove → _robust_rmtree raises off-thread → pack stays, no signal.
    panel.remove(0)
    qtbot.waitUntil(lambda: panel._remove_btn.isEnabled(), timeout=3000)
    assert removed == [], "first attempt raised — pack_removed must not fire"
    assert [e.pack_id for e in panel.get_chain()[:1]] == ["a"]

    # Second remove → _robust_rmtree succeeds off-thread → pack gone, signal fires.
    panel.remove(0)
    qtbot.waitUntil(lambda: removed == [None], timeout=3000)
    assert [e.kind for e in panel.get_chain()] == ["jpod101"]


def test_remove_confirm_dialog_mentions_audio_files_untouched(qapp, qtbot, monkeypatch, tmp_path):
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
    qtbot.addWidget(panel)
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


def test_checkbox_toggle_emits_chain_changed(qapp, qtbot, tmp_path):
    panel = AudioPackSettingsPanel(tmp_path)
    qtbot.addWidget(panel)
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


def test_checkbox_reflected_in_get_chain(qapp, qtbot, tmp_path):
    panel = AudioPackSettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    panel.set_chain((AudioSourceEntry(kind="pack", pack_id="a", enabled=True),))

    row = panel._row_widget(0)
    assert row is not None
    row.checkbox.setChecked(False)

    chain = panel.get_chain()
    assert chain[0].enabled is False


# ---------------------------------------------------------------------------
# Add button
# ---------------------------------------------------------------------------


def test_add_button_emits_add_pack_requested(qapp, qtbot, tmp_path):
    panel = AudioPackSettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    fired: list[None] = []
    panel.add_pack_requested.connect(lambda: fired.append(None))

    panel._add_btn.click()

    assert fired == [None]


# ---------------------------------------------------------------------------
# Context menu
# ---------------------------------------------------------------------------


def test_right_click_pack_row_emits_reimport_signal(qapp, qtbot, monkeypatch, tmp_path):
    _make_pack_on_disk(tmp_path, "ajt-pack", fmt="ajt", source="AJT Japanese")
    panel = AudioPackSettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    panel.set_chain(
        (
            AudioSourceEntry(kind="pack", pack_id="ajt-pack", enabled=True),
            AudioSourceEntry(kind="jpod101", pack_id=None, enabled=True),
        )
    )
    # Registry scan is deferred to first showEvent (OVH-053); trigger it so
    # _on_row_context_menu can resolve meta from the registry. The scan runs
    # off the GUI thread.
    panel.show()
    qtbot.waitUntil(lambda: not panel._scan_in_flight, timeout=3000)

    constructed = _patch_menu_exec(monkeypatch, "Re-import…")

    emitted: list[str] = []
    panel.reimport_pack_requested.connect(emitted.append)

    item = panel._list.item(0)
    pos = panel._list.visualItemRect(item).center()
    panel._on_row_context_menu(pos)

    assert len(constructed) == 1
    assert emitted == ["ajt-pack"]


def test_right_click_jpod101_row_shows_no_menu(qapp, qtbot, monkeypatch, tmp_path):
    panel = AudioPackSettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    panel.set_chain((AudioSourceEntry(kind="jpod101", pack_id=None, enabled=True),))

    constructed = _patch_menu_exec(monkeypatch, "Re-import…")

    emitted: list[str] = []
    panel.reimport_pack_requested.connect(emitted.append)

    item = panel._list.item(0)
    pos = panel._list.visualItemRect(item).center()
    panel._on_row_context_menu(pos)

    assert constructed == [], "jpod101 row must not open a context menu"
    assert emitted == []


def test_right_click_remove_action_removes_pack(qapp, qtbot, monkeypatch, tmp_path, confirm_remove):
    """Right-click → Remove delegates to self.remove()."""
    _make_pack_on_disk(tmp_path, "a", fmt="ajt", source="Pack A")
    panel = AudioPackSettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    panel.set_chain((AudioSourceEntry(kind="pack", pack_id="a", enabled=True),))
    # Registry scan is deferred to first showEvent (OVH-053); runs off-thread.
    panel.show()
    qtbot.waitUntil(lambda: not panel._scan_in_flight, timeout=3000)

    _patch_menu_exec(monkeypatch, "Remove")

    removed: list[None] = []
    panel.pack_removed.connect(lambda: removed.append(None))

    item = panel._list.item(0)
    pos = panel._list.visualItemRect(item).center()
    panel._on_row_context_menu(pos)

    # Remove delegates to self.remove(), whose rmtree runs off-thread.
    qtbot.waitUntil(lambda: removed == [None], timeout=3000)
    assert panel._list.count() == 0


def test_right_click_pack_row_no_meta_shows_no_menu(qapp, qtbot, monkeypatch, tmp_path):
    """Context menu is skipped when registry meta lookup returns None for the pack."""
    # Use registry_meta={} so the pack_id has no entry — meta lookup returns None.
    panel = AudioPackSettingsPanel(tmp_path)
    qtbot.addWidget(panel)
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


def test_set_chain_with_registry_meta_uses_injected_meta(qapp, qtbot, tmp_path):
    """set_chain(registry_meta=...) must use the supplied meta, not scan disk."""
    meta = _make_meta("nhk", fmt="nhk16", source="NHK Daily", entry_count=999)
    panel = AudioPackSettingsPanel(tmp_path)
    qtbot.addWidget(panel)
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


def test_chain_changed_emits_on_reorder_remove_and_toggle(qapp, qtbot, monkeypatch, tmp_path, confirm_remove):
    panel = AudioPackSettingsPanel(tmp_path)
    qtbot.addWidget(panel)
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


# ---------------------------------------------------------------------------
# OVH-053 — registry scan deferred to first showEvent
# ---------------------------------------------------------------------------


class TestShowEventDeferral:
    """AudioPackSettingsPanel defers AudioPackRegistry.load() off the paint
    path (OVH-053): constructing the panel + calling set_chain must NOT scan
    the registry; only the first showEvent triggers the scan."""

    def test_construction_does_not_call_registry_load(self, qapp, qtbot, tmp_path, monkeypatch):
        """Constructing the panel (including _load_config's set_chain call) must
        not call AudioPackRegistry.load()."""
        from anki_miner.services.audio_packs.registry import AudioPackRegistry

        load_calls: list[None] = []
        real_load = AudioPackRegistry.load

        def _spy_load(self):
            load_calls.append(None)
            return real_load(self)

        monkeypatch.setattr(AudioPackRegistry, "load", _spy_load)

        panel = AudioPackSettingsPanel(tmp_path)
        qtbot.addWidget(panel)
        panel.set_chain(AnkiMinerConfig().expression_audio_chain)

        assert load_calls == [], "AudioPackRegistry.load() must not run before first showEvent"

    def test_first_show_event_triggers_exactly_one_scan(self, qapp, qtbot, tmp_path, monkeypatch):
        """The first showEvent must trigger exactly one AudioPackRegistry.load()."""
        from anki_miner.services.audio_packs.registry import AudioPackRegistry

        load_calls: list[None] = []
        real_load = AudioPackRegistry.load

        def _spy_load(self):
            load_calls.append(None)
            return real_load(self)

        monkeypatch.setattr(AudioPackRegistry, "load", _spy_load)

        panel = AudioPackSettingsPanel(tmp_path)
        qtbot.addWidget(panel)
        panel.set_chain(AnkiMinerConfig().expression_audio_chain)

        assert load_calls == []
        panel.show()
        # Scan now runs off the GUI thread.
        qtbot.waitUntil(lambda: len(load_calls) == 1, timeout=3000)
        qtbot.waitUntil(lambda: not panel._scan_in_flight, timeout=3000)
        assert len(load_calls) == 1, "First showEvent must trigger exactly one registry scan"

    def test_second_show_event_does_not_rescan(self, qapp, qtbot, tmp_path, monkeypatch):
        """Showing the panel a second time must not re-scan (guard prevents it)."""
        from anki_miner.services.audio_packs.registry import AudioPackRegistry

        load_calls: list[None] = []
        real_load = AudioPackRegistry.load

        def _spy_load(self):
            load_calls.append(None)
            return real_load(self)

        monkeypatch.setattr(AudioPackRegistry, "load", _spy_load)

        panel = AudioPackSettingsPanel(tmp_path)
        qtbot.addWidget(panel)
        panel.set_chain(AnkiMinerConfig().expression_audio_chain)

        panel.show()
        qtbot.waitUntil(lambda: len(load_calls) == 1, timeout=3000)
        qtbot.waitUntil(lambda: not panel._scan_in_flight, timeout=3000)

        panel.hide()
        panel.show()
        assert len(load_calls) == 1, "Second showEvent must not re-scan"


# ---------------------------------------------------------------------------
# OVH disk-scan-off-thread — registry scan + remove rmtree run off the GUI thread
# ---------------------------------------------------------------------------


class TestOffThreadDiskWork:
    """First-show scan and Remove rmtree must run on a worker thread."""

    def test_first_show_scan_runs_off_gui_thread(self, qapp, qtbot, tmp_path, monkeypatch):
        import threading

        main_id = threading.get_ident()
        scan_threads: list[int] = []
        real_load = asp_mod.AudioPackRegistry.load

        def _spy_load(self):
            scan_threads.append(threading.get_ident())
            return real_load(self)

        monkeypatch.setattr(asp_mod.AudioPackRegistry, "load", _spy_load)

        _make_pack_on_disk(tmp_path, "ajt-pack", fmt="ajt", source="AJT")
        panel = AudioPackSettingsPanel(tmp_path)
        qtbot.addWidget(panel)
        panel.set_chain((AudioSourceEntry(kind="pack", pack_id="ajt-pack", enabled=True),))
        panel.show()

        qtbot.waitUntil(lambda: bool(scan_threads), timeout=3000)
        qtbot.waitUntil(lambda: not panel._scan_in_flight, timeout=3000)
        assert scan_threads and all(t != main_id for t in scan_threads), scan_threads

    def test_remove_rmtree_runs_off_gui_thread(self, qapp, qtbot, tmp_path, confirm_remove, monkeypatch):
        import threading

        main_id = threading.get_ident()
        rmtree_threads: list[int] = []
        real_rmtree = asp_mod.shutil.rmtree

        def _spy_rmtree(path, *a, **kw):
            rmtree_threads.append(threading.get_ident())
            return real_rmtree(path, *a, **kw)

        monkeypatch.setattr(asp_mod.shutil, "rmtree", _spy_rmtree)

        pack_dir = tmp_path / "a"
        pack_dir.mkdir()
        (pack_dir / "index.sqlite").write_bytes(b"placeholder")
        panel = AudioPackSettingsPanel(tmp_path)
        qtbot.addWidget(panel)
        panel.set_chain(
            (
                AudioSourceEntry(kind="pack", pack_id="a", enabled=True),
                AudioSourceEntry(kind="jpod101", pack_id=None, enabled=True),
            )
        )

        panel.remove(0)
        qtbot.waitUntil(lambda: not pack_dir.exists(), timeout=3000)
        assert rmtree_threads and all(t != main_id for t in rmtree_threads), rmtree_threads

    def test_remove_disables_then_reenables_button(self, qapp, qtbot, tmp_path, confirm_remove):
        pack_dir = tmp_path / "a"
        pack_dir.mkdir()
        (pack_dir / "index.sqlite").write_bytes(b"placeholder")
        panel = AudioPackSettingsPanel(tmp_path)
        qtbot.addWidget(panel)
        panel.set_chain(
            (
                AudioSourceEntry(kind="pack", pack_id="a", enabled=True),
                AudioSourceEntry(kind="jpod101", pack_id=None, enabled=True),
            )
        )

        panel.remove(0)
        assert panel._remove_btn.isEnabled() is False
        qtbot.waitUntil(lambda: panel._remove_btn.isEnabled(), timeout=3000)
        assert not pack_dir.exists()


class TestRescanWhileInFlight:
    """A refresh_registry() requested while a scan is in flight must re-dispatch
    a fresh scan so the latest disk state renders, not the stale first one."""

    def test_refresh_during_in_flight_scan_renders_latest_disk_state(self, qapp, qtbot, tmp_path, monkeypatch):
        import threading

        gate = threading.Event()
        load_calls: list[int] = []
        real_load = asp_mod.AudioPackRegistry.load

        def _spy_load(self):
            n = len(load_calls)
            load_calls.append(n)
            if n == 0:
                gate.wait(timeout=5.0)
            return real_load(self)

        monkeypatch.setattr(asp_mod.AudioPackRegistry, "load", _spy_load)

        panel = AudioPackSettingsPanel(tmp_path)
        qtbot.addWidget(panel)
        panel.set_chain((AudioSourceEntry(kind="pack", pack_id="latepack", enabled=True),))

        # First-show scan A starts and blocks (disk has no pack yet).
        panel.show()
        qtbot.waitUntil(lambda: len(load_calls) == 1, timeout=3000)
        assert panel._scan_in_flight is True

        # Import finishes: pack now on disk + refresh requested while A is busy.
        _make_pack_on_disk(tmp_path, "latepack", fmt="ajt", source="Late Pack", entry_count=777)
        panel.refresh_registry()
        assert panel._rescan_pending is True

        # Release scan A; the pending rescan must re-dispatch.
        gate.set()
        qtbot.waitUntil(lambda: len(load_calls) == 2, timeout=3000)
        qtbot.waitUntil(lambda: not panel._scan_in_flight, timeout=3000)
        assert panel._rescan_pending is False

        row = panel._row_widget(0)
        assert row is not None
        texts = [lbl.text() for lbl in row.findChildren(QLabel)]
        assert any("Late Pack" in t for t in texts), texts
        assert panel._view is not None
        meta = panel._view.get("latepack")
        assert meta is not None and meta.source == "Late Pack"


# ---------------------------------------------------------------------------
# Custom / scrape source rows (Task 8.1 + 8.2)
# ---------------------------------------------------------------------------


def test_custom_row_shows_url(qapp, qtbot, tmp_path):
    panel = AudioPackSettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    panel.set_chain((AudioSourceEntry(kind="custom", url="http://localhost:5050/?t={term}", enabled=True),))
    row = panel._row_widget(0)
    assert row is not None
    texts = [lbl.text() for lbl in row.findChildren(QLabel)]
    assert any("http://localhost:5050" in t for t in texts), texts


def test_scrape_rows_show_display_names(qapp, qtbot, tmp_path):
    panel = AudioPackSettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    panel.set_chain(
        (
            AudioSourceEntry(kind="jpod101_scrape", enabled=False),
            AudioSourceEntry(kind="jisho_scrape", enabled=False),
        )
    )
    texts0 = [lbl.text() for lbl in panel._row_widget(0).findChildren(QLabel)]
    texts1 = [lbl.text() for lbl in panel._row_widget(1).findChildren(QLabel)]
    assert any("JapanesePod101 dictionary" in t for t in texts0), texts0
    assert any("Jisho" in t for t in texts1), texts1


def test_get_chain_preserves_url(qapp, qtbot, tmp_path):
    panel = AudioPackSettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    entry = AudioSourceEntry(kind="custom_json", url="http://h/list?t={term}", enabled=True)
    panel.set_chain((entry, AudioSourceEntry(kind="jpod101", enabled=True)))
    chain = panel.get_chain()
    assert chain[0].kind == "custom_json"
    assert chain[0].url == "http://h/list?t={term}"


def test_add_source_entry_appends_and_emits(qapp, qtbot, tmp_path):
    panel = AudioPackSettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    panel.set_chain((AudioSourceEntry(kind="jpod101", enabled=True),))
    with qtbot.waitSignal(panel.chain_changed, timeout=1000):
        panel.add_source_entry(AudioSourceEntry(kind="jisho_scrape", enabled=True))
    chain = panel.get_chain()
    assert [e.kind for e in chain] == ["jpod101", "jisho_scrape"]
    assert panel._list.count() == 2


def test_remove_custom_source_no_confirmation(qapp, qtbot, tmp_path, monkeypatch):
    # No QMessageBox.question stub: removing an online source must not prompt.
    def _boom(*a, **kw):
        raise AssertionError("removing an online source must not show a confirmation dialog")

    monkeypatch.setattr("anki_miner.gui.widgets.panels.audio_pack_settings_panel.QMessageBox.question", _boom)
    panel = AudioPackSettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    panel.set_chain(
        (
            AudioSourceEntry(kind="jpod101", enabled=True),
            AudioSourceEntry(kind="custom", url="http://h/?t={term}", enabled=True),
        )
    )
    with qtbot.waitSignal(panel.chain_changed, timeout=1000):
        panel.remove(1)
    assert [e.kind for e in panel.get_chain()] == ["jpod101"]


def test_remove_jpod101_scrape_allowed(qapp, qtbot, tmp_path):
    panel = AudioPackSettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    panel.set_chain(
        (
            AudioSourceEntry(kind="jpod101", enabled=True),
            AudioSourceEntry(kind="jpod101_scrape", enabled=True),
        )
    )
    panel.remove(1)
    assert [e.kind for e in panel.get_chain()] == ["jpod101"]


def test_remove_builtin_jpod101_blocked(qapp, qtbot, tmp_path):
    panel = AudioPackSettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    panel.set_chain((AudioSourceEntry(kind="jpod101", enabled=True),))
    panel.remove(0)
    assert [e.kind for e in panel.get_chain()] == ["jpod101"]


# ---------------------------------------------------------------------------
# _AddSourceDialog behaviour
# ---------------------------------------------------------------------------


def test_add_source_dialog_ok_disabled_until_url_for_custom(qapp, qtbot):
    dialog = asp_mod._AddSourceDialog()
    qtbot.addWidget(dialog)
    # Default first kind is "custom" → OK disabled with empty URL.
    assert dialog.selected_kind() == "custom"
    ok = dialog._buttons.button(QDialogButtonBox.StandardButton.Ok)
    assert not ok.isEnabled()
    dialog._url_edit.setText("http://h/?t={term}")
    assert ok.isEnabled()
    assert dialog.url_value() == "http://h/?t={term}"


def test_add_source_dialog_scrape_kind_needs_no_url(qapp, qtbot):
    dialog = asp_mod._AddSourceDialog()
    qtbot.addWidget(dialog)
    # Select the jpod101_scrape kind.
    idx = dialog._kind_combo.findData("jpod101_scrape")
    dialog._kind_combo.setCurrentIndex(idx)
    ok = dialog._buttons.button(QDialogButtonBox.StandardButton.Ok)
    assert ok.isEnabled()
    assert dialog.url_value() is None
    assert not dialog._url_edit.isVisible()
