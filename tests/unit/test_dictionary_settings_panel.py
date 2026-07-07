"""Smoke tests for DictionarySettingsPanel."""

import os
import shutil
import stat
from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QMessageBox

from anki_miner.config import AnkiMinerConfig, ChainEntry
from anki_miner.gui.widgets.panels import dictionary_settings_panel as dsp_mod
from anki_miner.gui.widgets.panels.dictionary_settings_panel import DictionarySettingsPanel
from anki_miner.services.dictionary.storage import SCHEMA_VERSION, create_index, write_meta


def _make_dict_on_disk(
    root: Path,
    dict_id: str,
    *,
    fmt: str,
    schema_version: int,
    source_name: str | None = None,
) -> Path:
    """Materialize a minimal on-disk dictionary with chosen schema_version + format."""
    dict_dir = root / dict_id
    dict_dir.mkdir(parents=True, exist_ok=True)
    db_path = dict_dir / "index.sqlite"
    create_index(db_path)
    write_meta(
        db_path,
        {
            "schema_version": str(schema_version),
            "format": fmt,
            "source_name": source_name or dict_id,
            "entry_count": "0",
        },
    )
    return dict_dir


def _wait_scan(panel, qtbot):
    """Wait for the panel's off-thread registry scan to populate _registry.

    The disk scan now runs on a worker thread (OVH disk-scan-off-thread); row
    metadata is only available once it lands back on the GUI thread.
    """
    qtbot.waitUntil(lambda: not panel._scan_in_flight, timeout=3000)


@pytest.fixture
def confirm_remove(monkeypatch):
    """Auto-accept the 'Remove dictionary' QMessageBox confirmation."""
    monkeypatch.setattr(
        "anki_miner.gui.widgets.panels.dictionary_settings_panel.QMessageBox.question",
        lambda *a, **kw: QMessageBox.StandardButton.Yes,
    )


def test_panel_renders_default_chain(qapp, qtbot, monkeypatch, tmp_path):
    panel = DictionarySettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    panel.set_chain(AnkiMinerConfig().dictionary_chain)
    chain = panel.get_chain()
    # Default has two entries; one indexed (missing on disk -> keeps entry), one jisho
    assert len(chain) == 2
    assert chain[1].kind == "jisho"


def test_reorder_controls_disabled_during_scan_placeholder(qapp, qtbot, tmp_path):
    """The move/remove buttons are disabled while the Loading placeholder shows
    (no real rows) and re-enabled once the list is rebuilt."""
    panel = DictionarySettingsPanel(tmp_path)
    qtbot.addWidget(panel)

    panel._show_loading_placeholder()
    assert not panel._up_btn.isEnabled()
    assert not panel._down_btn.isEnabled()
    assert not panel._remove_btn.isEnabled()

    panel._rebuild_list()
    assert panel._up_btn.isEnabled()
    assert panel._down_btn.isEnabled()
    assert panel._remove_btn.isEnabled()


def test_frequency_widgets_removed_pitch_selector_kept(qapp, qtbot, tmp_path):
    # Frequency moved to its own multi-source Settings → Frequency panel; the
    # old single-file picker no longer lives on the dictionary panel. The pitch
    # file selector stays here; its enable checkbox was removed (activation is
    # now derived from the pitch file being present, config.pitch_active).
    panel = DictionarySettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    assert not hasattr(panel, "frequency_selector")
    assert not hasattr(panel, "use_frequency_checkbox")
    assert hasattr(panel, "pitch_accent_selector")
    assert not hasattr(panel, "use_pitch_accent_checkbox")


def test_reorder_moves_entry_up(qapp, qtbot, monkeypatch, tmp_path):
    panel = DictionarySettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    panel.set_chain(
        (
            ChainEntry(kind="indexed", dict_id="a", enabled=True),
            ChainEntry(kind="indexed", dict_id="b", enabled=True),
            ChainEntry(kind="jisho", dict_id=None, enabled=True),
        )
    )
    panel.move_up(1)  # move b up
    chain = panel.get_chain()
    assert chain[0].dict_id == "b"
    assert chain[1].dict_id == "a"


def test_chain_changed_emits_on_reorder_remove_and_toggle(qapp, qtbot, monkeypatch, tmp_path, confirm_remove):
    panel = DictionarySettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    panel.set_chain(
        (
            ChainEntry(kind="indexed", dict_id="a", enabled=True),
            ChainEntry(kind="indexed", dict_id="b", enabled=True),
            ChainEntry(kind="jisho", dict_id=None, enabled=True),
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

    # Toggle checkbox via row widget -> chain_changed should fire
    row = panel._row_widget(0)
    assert row is not None
    row.checkbox.setChecked(not row.checkbox.isChecked())
    assert events[-1] == "changed"
    assert len(events) == 4


def test_jisho_remove_is_noop(qapp, qtbot, monkeypatch, tmp_path):
    panel = DictionarySettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    panel.set_chain(
        (
            ChainEntry(kind="indexed", dict_id="a", enabled=True),
            ChainEntry(kind="jisho", dict_id=None, enabled=True),
        )
    )

    events: list[str] = []
    panel.chain_changed.connect(lambda: events.append("changed"))

    panel.remove(1)  # jisho row -> no-op, no signal
    chain = panel.get_chain()
    assert len(chain) == 2
    assert chain[1].kind == "jisho"
    assert events == []


def test_edge_reorder_calls_are_noops(qapp, qtbot, monkeypatch, tmp_path):
    panel = DictionarySettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    panel.set_chain(
        (
            ChainEntry(kind="indexed", dict_id="a", enabled=True),
            ChainEntry(kind="jisho", dict_id=None, enabled=True),
        )
    )

    events: list[str] = []
    panel.chain_changed.connect(lambda: events.append("changed"))

    panel.move_up(0)  # top row
    panel.move_down(1)  # bottom row
    panel.move_up(-1)  # no selection
    panel.move_down(-1)
    panel.remove(-1)

    assert events == []
    chain = panel.get_chain()
    assert chain[0].dict_id == "a"
    assert chain[1].kind == "jisho"


def test_checkbox_toggle_preserved_on_reorder(qapp, qtbot, monkeypatch, tmp_path, confirm_remove):
    """The implementer's deviation: get_chain()-resync before mutation must
    preserve a user's checkbox toggle across move_up/move_down/remove."""
    panel = DictionarySettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    panel.set_chain(
        (
            ChainEntry(kind="indexed", dict_id="a", enabled=True),
            ChainEntry(kind="indexed", dict_id="b", enabled=True),
            ChainEntry(kind="indexed", dict_id="c", enabled=True),
            ChainEntry(kind="jisho", dict_id=None, enabled=True),
        )
    )

    # User unchecks row "b" (index 1) — _chain still says enabled=True until
    # get_chain() / mutation re-syncs.
    row_b = panel._row_widget(1)
    assert row_b is not None
    row_b.checkbox.setChecked(False)

    # Move "b" up. The disabled state should travel with it.
    panel.move_up(1)
    chain = panel.get_chain()
    assert chain[0].dict_id == "b"
    assert chain[0].enabled is False
    assert chain[1].dict_id == "a"
    assert chain[1].enabled is True

    # Now move "b" back down via move_down — toggle should still survive.
    panel.move_down(0)
    chain = panel.get_chain()
    assert chain[0].dict_id == "a"
    assert chain[0].enabled is True
    assert chain[1].dict_id == "b"
    assert chain[1].enabled is False

    # Uncheck "c" (now at index 2) then remove "a" (index 0); "c" toggle must
    # survive the remove's _chain rebuild.
    row_c = panel._row_widget(2)
    assert row_c is not None
    row_c.checkbox.setChecked(False)
    panel.remove(0)
    chain = panel.get_chain()
    assert [e.dict_id for e in chain[:2]] == ["b", "c"]
    assert chain[0].enabled is False
    assert chain[1].enabled is False


def test_remove_deletes_dict_folder_on_disk(qapp, qtbot, monkeypatch, tmp_path, confirm_remove):
    """Regression: remove() must delete dicts_root/<dict_id>/ so a re-add of the
    same dict does not hit the importer's 'already exists' guard."""
    dict_dir = tmp_path / "a"
    dict_dir.mkdir()
    (dict_dir / "index.sqlite").write_bytes(b"placeholder")

    panel = DictionarySettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    panel.set_chain(
        (
            ChainEntry(kind="indexed", dict_id="a", enabled=True),
            ChainEntry(kind="jisho", dict_id=None, enabled=True),
        )
    )

    panel.remove(0)

    # rmtree now runs off the GUI thread; wait for it to land.
    qtbot.waitUntil(lambda: not dict_dir.exists(), timeout=3000)
    chain = panel.get_chain()
    assert [e.kind for e in chain] == ["jisho"]


def test_remove_cancelled_keeps_dict_and_chain(qapp, qtbot, monkeypatch, tmp_path):
    """Clicking 'No' on the confirm dialog must leave both disk + chain intact."""
    monkeypatch.setattr(
        "anki_miner.gui.widgets.panels.dictionary_settings_panel.QMessageBox.question",
        lambda *a, **kw: QMessageBox.StandardButton.No,
    )

    dict_dir = tmp_path / "a"
    dict_dir.mkdir()
    (dict_dir / "index.sqlite").write_bytes(b"placeholder")

    panel = DictionarySettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    panel.set_chain(
        (
            ChainEntry(kind="indexed", dict_id="a", enabled=True),
            ChainEntry(kind="jisho", dict_id=None, enabled=True),
        )
    )

    events: list[str] = []
    panel.chain_changed.connect(lambda: events.append("changed"))

    panel.remove(0)

    assert dict_dir.exists(), "cancel must not touch disk"
    chain = panel.get_chain()
    assert [e.dict_id for e in chain[:1]] == ["a"]
    assert events == [], "cancel must not emit chain_changed"


def test_remove_tolerates_missing_dict_folder(qapp, qtbot, tmp_path, confirm_remove):
    """If the dict folder is already gone (e.g. user deleted it manually), remove()
    should still drop the in-memory entry instead of erroring."""
    # No folder created on disk.

    panel = DictionarySettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    panel.set_chain(
        (
            ChainEntry(kind="indexed", dict_id="ghost", enabled=True),
            ChainEntry(kind="jisho", dict_id=None, enabled=True),
        )
    )

    panel.remove(0)

    chain = panel.get_chain()
    assert [e.kind for e in chain] == ["jisho"]


def test_stale_yomitan_row_shows_warning_and_reimport_button(qapp, qtbot, tmp_path):
    """A Yomitan dictionary with outdated schema_version renders the stale UI."""
    _make_dict_on_disk(
        tmp_path,
        "stale-yomi",
        fmt="yomitan",
        schema_version=SCHEMA_VERSION - 1,
        source_name="Stale Yomi",
    )
    panel = DictionarySettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    panel.set_chain(
        (
            ChainEntry(kind="indexed", dict_id="stale-yomi", enabled=True),
            ChainEntry(kind="jisho", dict_id=None, enabled=True),
        )
    )
    # Registry scan is deferred to first showEvent (OVH-053); trigger it now so
    # the row metadata is populated before inspecting row content.
    panel.show()
    _wait_scan(panel, qtbot)

    row = panel._row_widget(0)
    assert row is not None
    assert row.stale is True
    assert row.reimport_button is not None

    # ⚠ prefix is on the name label
    from PyQt6.QtWidgets import QLabel

    labels = row.findChildren(QLabel)
    label_texts = [lbl.text() for lbl in labels]
    assert any(t.startswith("⚠ ") and "Stale Yomi" in t for t in label_texts)
    # italic suffix exists as one of the label texts
    assert any("re-import to refresh" in t for t in label_texts)

    emitted: list[str] = []
    panel.reimport_dict_requested.connect(emitted.append)
    jmdict_fired: list[None] = []
    panel.reimport_jmdict_requested.connect(lambda: jmdict_fired.append(None))

    row.reimport_button.click()
    assert emitted == ["stale-yomi"]
    assert jmdict_fired == [], "Yomitan row must not fire the JMdict signal"


def test_stale_jmdict_row_fires_reimport_jmdict_signal(qapp, qtbot, tmp_path):
    """A JMdict dictionary with outdated schema_version wires the per-row button
    to the existing reimport_jmdict_requested signal, not the new generic one."""
    _make_dict_on_disk(
        tmp_path,
        "jmdict-english",
        fmt="jmdict",
        schema_version=SCHEMA_VERSION - 1,
        source_name="JMdict (English)",
    )
    panel = DictionarySettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    panel.set_chain(
        (
            ChainEntry(kind="indexed", dict_id="jmdict-english", enabled=True),
            ChainEntry(kind="jisho", dict_id=None, enabled=True),
        )
    )
    # Registry scan is deferred to first showEvent (OVH-053).
    panel.show()
    _wait_scan(panel, qtbot)

    row = panel._row_widget(0)
    assert row is not None
    assert row.stale is True
    assert row.reimport_button is not None

    jmdict_fired: list[None] = []
    panel.reimport_jmdict_requested.connect(lambda: jmdict_fired.append(None))
    generic_fired: list[str] = []
    panel.reimport_dict_requested.connect(generic_fired.append)

    row.reimport_button.click()
    assert jmdict_fired == [None]
    assert generic_fired == [], "JMdict row must not fire the generic signal"


def test_current_schema_row_has_no_stale_ui(qapp, qtbot, tmp_path):
    """A dictionary at the current schema_version renders clean: no ⚠, no italic
    suffix, no Re-import button."""
    _make_dict_on_disk(
        tmp_path,
        "fresh-yomi",
        fmt="yomitan",
        schema_version=SCHEMA_VERSION,
        source_name="Fresh Yomi",
    )
    panel = DictionarySettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    panel.set_chain(
        (
            ChainEntry(kind="indexed", dict_id="fresh-yomi", enabled=True),
            ChainEntry(kind="jisho", dict_id=None, enabled=True),
        )
    )
    # Registry scan is deferred to first showEvent (OVH-053).
    panel.show()
    _wait_scan(panel, qtbot)

    row = panel._row_widget(0)
    assert row is not None
    assert row.stale is False
    assert row.reimport_button is None

    from PyQt6.QtWidgets import QLabel

    labels = row.findChildren(QLabel)
    label_texts = [lbl.text() for lbl in labels]
    assert not any(t.startswith("⚠") for t in label_texts)
    assert not any("re-import to refresh" in t for t in label_texts)


def test_global_button_labeled_reimport_all(qapp, qtbot, tmp_path):
    """The top-level button reads 'Reimport All', not the legacy 'Reimport JMdict'."""
    panel = DictionarySettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    assert panel._reimport_btn.text() == "Reimport All"


def test_reimport_all_signal_fires_on_button_click(qapp, qtbot, tmp_path):
    """Clicking the top-level button emits the new reimport_all_requested signal,
    not the per-row reimport_jmdict_requested signal."""
    panel = DictionarySettingsPanel(tmp_path)
    qtbot.addWidget(panel)

    all_fired: list[None] = []
    panel.reimport_all_requested.connect(lambda: all_fired.append(None))
    jmdict_fired: list[None] = []
    panel.reimport_jmdict_requested.connect(lambda: jmdict_fired.append(None))

    panel._reimport_btn.click()
    assert all_fired == [None]
    assert jmdict_fired == [], "Global button must not fire the JMdict-only signal"


def _patch_menu_exec(monkeypatch, action_label: str | None):
    """Stub ``QMenu.exec`` to return the action matching ``action_label``.

    Use ``action_label=None`` to simulate the user dismissing the menu.
    Records every constructed menu so tests can assert it was opened.
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


def test_right_click_non_stale_yomitan_row_emits_reimport_dict_requested(qapp, qtbot, monkeypatch, tmp_path):
    """Right-clicking a current-schema Yomitan row → Re-import… emits the
    per-dict signal so legacy users (no source.zip) have a discoverable seed path."""
    _make_dict_on_disk(
        tmp_path,
        "fresh-yomi",
        fmt="yomitan",
        schema_version=SCHEMA_VERSION,
        source_name="Fresh Yomi",
    )
    panel = DictionarySettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    panel.set_chain(
        (
            ChainEntry(kind="indexed", dict_id="fresh-yomi", enabled=True),
            ChainEntry(kind="jisho", dict_id=None, enabled=True),
        )
    )
    # Registry scan is deferred to first showEvent (OVH-053); trigger it so
    # _on_row_context_menu can resolve meta from the registry.
    panel.show()
    _wait_scan(panel, qtbot)

    constructed = _patch_menu_exec(monkeypatch, "Re-import…")

    emitted: list[str] = []
    panel.reimport_dict_requested.connect(emitted.append)
    jmdict_fired: list[None] = []
    panel.reimport_jmdict_requested.connect(lambda: jmdict_fired.append(None))

    item = panel._list.item(0)
    pos = panel._list.visualItemRect(item).center()
    panel._on_row_context_menu(pos)

    assert len(constructed) == 1, "Yomitan row must open the context menu"
    assert emitted == ["fresh-yomi"]
    assert jmdict_fired == [], "Yomitan row must not fire the JMdict signal"


def test_right_click_jmdict_row_emits_reimport_jmdict_requested(qapp, qtbot, monkeypatch, tmp_path):
    """Right-clicking a JMdict row → Re-import… emits the JMdict-specific
    signal (which uses the configured XML path, not a file picker)."""
    _make_dict_on_disk(
        tmp_path,
        "jmdict-english",
        fmt="jmdict",
        schema_version=SCHEMA_VERSION,
        source_name="JMdict (English)",
    )
    panel = DictionarySettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    panel.set_chain(
        (
            ChainEntry(kind="indexed", dict_id="jmdict-english", enabled=True),
            ChainEntry(kind="jisho", dict_id=None, enabled=True),
        )
    )
    # Registry scan is deferred to first showEvent (OVH-053).
    panel.show()
    _wait_scan(panel, qtbot)

    _patch_menu_exec(monkeypatch, "Re-import…")

    jmdict_fired: list[None] = []
    panel.reimport_jmdict_requested.connect(lambda: jmdict_fired.append(None))
    generic_fired: list[str] = []
    panel.reimport_dict_requested.connect(generic_fired.append)

    item = panel._list.item(0)
    pos = panel._list.visualItemRect(item).center()
    panel._on_row_context_menu(pos)

    assert jmdict_fired == [None]
    assert generic_fired == [], "JMdict row must not fire the generic signal"


def test_remove_emits_dictionary_removed_signal(qapp, qtbot, tmp_path, confirm_remove):
    """remove() must fire dictionary_removed so settings_tab can persist the
    chain to gui_config.json without waiting for the user to click Save."""
    dict_dir = tmp_path / "a"
    dict_dir.mkdir()
    (dict_dir / "index.sqlite").write_bytes(b"placeholder")

    panel = DictionarySettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    panel.set_chain(
        (
            ChainEntry(kind="indexed", dict_id="a", enabled=True),
            ChainEntry(kind="jisho", dict_id=None, enabled=True),
        )
    )

    removed: list[None] = []
    panel.dictionary_removed.connect(lambda: removed.append(None))

    panel.remove(0)
    qtbot.waitUntil(lambda: removed == [None], timeout=3000)


def test_remove_cancelled_does_not_emit_dictionary_removed(qapp, qtbot, monkeypatch, tmp_path):
    """Cancelling the confirm dialog must not fire dictionary_removed — nothing
    on disk changed, so we don't want settings_tab to persist."""
    monkeypatch.setattr(
        "anki_miner.gui.widgets.panels.dictionary_settings_panel.QMessageBox.question",
        lambda *a, **kw: QMessageBox.StandardButton.No,
    )
    dict_dir = tmp_path / "a"
    dict_dir.mkdir()
    (dict_dir / "index.sqlite").write_bytes(b"placeholder")

    panel = DictionarySettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    panel.set_chain(
        (
            ChainEntry(kind="indexed", dict_id="a", enabled=True),
            ChainEntry(kind="jisho", dict_id=None, enabled=True),
        )
    )

    removed: list[None] = []
    panel.dictionary_removed.connect(lambda: removed.append(None))

    panel.remove(0)
    assert removed == []


def test_remove_failed_rmtree_does_not_emit_dictionary_removed(qapp, qtbot, monkeypatch, tmp_path, confirm_remove):
    """If rmtree exhausts its retries we abort early; dictionary_removed must
    not fire because the chain mutation also did not happen."""
    dict_dir = tmp_path / "a"
    dict_dir.mkdir()
    (dict_dir / "index.sqlite").write_bytes(b"placeholder")

    # Stub QMessageBox.warning so the error dialog doesn't try to render.
    monkeypatch.setattr(
        "anki_miner.gui.widgets.panels.dictionary_settings_panel.QMessageBox.warning",
        lambda *a, **kw: QMessageBox.StandardButton.Ok,
    )

    def _always_fail(*args, **kwargs):
        raise PermissionError("simulated locked file")

    monkeypatch.setattr(dsp_mod.shutil, "rmtree", _always_fail)
    # Speed up the retry loop — the helper sleeps between attempts.
    monkeypatch.setattr(dsp_mod.time, "sleep", lambda _s: None)

    panel = DictionarySettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    panel.set_chain(
        (
            ChainEntry(kind="indexed", dict_id="a", enabled=True),
            ChainEntry(kind="jisho", dict_id=None, enabled=True),
        )
    )

    removed: list[None] = []
    panel.dictionary_removed.connect(lambda: removed.append(None))

    panel.remove(0)
    # The off-thread rmtree fails; wait for the error handler to re-enable the
    # Remove button (proof the error callback ran on the GUI thread).
    qtbot.waitUntil(lambda: panel._remove_btn.isEnabled(), timeout=3000)
    assert removed == []
    chain = panel.get_chain()
    assert [e.dict_id for e in chain[:1]] == ["a"], "failed remove must leave chain intact"


def test_remove_retries_transient_oserror(qapp, qtbot, monkeypatch, tmp_path, confirm_remove):
    """A flake on the first rmtree attempt should be absorbed by the retry loop
    and succeed without surfacing an error dialog (Win11 sqlite-handle race)."""
    dict_dir = tmp_path / "a"
    dict_dir.mkdir()
    (dict_dir / "index.sqlite").write_bytes(b"placeholder")

    real_rmtree = shutil.rmtree
    calls = {"n": 0}

    def _flaky(path, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise PermissionError("[WinError 32] simulated transient lock")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(dsp_mod.shutil, "rmtree", _flaky)
    monkeypatch.setattr(dsp_mod.time, "sleep", lambda _s: None)

    warned: list[None] = []
    monkeypatch.setattr(
        "anki_miner.gui.widgets.panels.dictionary_settings_panel.QMessageBox.warning",
        lambda *a, **kw: warned.append(None) or QMessageBox.StandardButton.Ok,
    )

    panel = DictionarySettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    panel.set_chain(
        (
            ChainEntry(kind="indexed", dict_id="a", enabled=True),
            ChainEntry(kind="jisho", dict_id=None, enabled=True),
        )
    )

    removed: list[None] = []
    panel.dictionary_removed.connect(lambda: removed.append(None))

    panel.remove(0)

    # rmtree (with its retry loop) now runs off the GUI thread.
    qtbot.waitUntil(lambda: removed == [None], timeout=3000)
    assert calls["n"] >= 2, "retry loop should have triggered at least once"
    assert not dict_dir.exists(), "second attempt must complete the rmtree"
    assert warned == [], "successful retry must not show an error dialog"


def test_on_rmtree_error_clears_readonly_then_retries(tmp_path):
    """Unit test the onerror handler in isolation: PermissionError on a RO file
    should result in chmod(S_IWRITE) + retry of the failing op (Win11 zip RO)."""
    target = tmp_path / "ro.bin"
    target.write_bytes(b"x")
    os.chmod(target, stat.S_IREAD)

    # First call simulates the rmtree-internal failure; the handler should
    # chmod the file then re-invoke os.unlink, which now succeeds on Windows
    # (and is harmless on POSIX since the parent dir is writable).
    dsp_mod._on_rmtree_error(os.unlink, str(target), None)

    assert not target.exists()


def test_on_rmtree_error_reraises_non_permission(tmp_path):
    """Non-RO failures must re-raise so the retry loop / caller can surface
    them — we only special-case the read-only bit, nothing else."""
    target = tmp_path / "missing"

    def _always_oserror(_path):
        raise FileNotFoundError(target)

    with pytest.raises(OSError):
        dsp_mod._on_rmtree_error(_always_oserror, str(target), None)


def test_release_callback_returning_false_aborts_remove(qapp, qtbot, monkeypatch, tmp_path, confirm_remove):
    """When the release callback says no (mining run in flight), the panel
    must show a warning and leave the dictionary on disk untouched."""
    dict_dir = tmp_path / "a"
    dict_dir.mkdir()
    (dict_dir / "index.sqlite").write_bytes(b"placeholder")

    rmtree_calls: list[Path] = []
    monkeypatch.setattr(dsp_mod.shutil, "rmtree", lambda p, *a, **kw: rmtree_calls.append(Path(p)))

    warned: list[str] = []
    monkeypatch.setattr(
        "anki_miner.gui.widgets.panels.dictionary_settings_panel.QMessageBox.warning",
        lambda parent, title, body, *a, **kw: warned.append(body) or QMessageBox.StandardButton.Ok,
    )

    panel = DictionarySettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    panel.set_chain(
        (
            ChainEntry(kind="indexed", dict_id="a", enabled=True),
            ChainEntry(kind="jisho", dict_id=None, enabled=True),
        )
    )
    panel.set_release_callback(lambda: False)

    removed: list[None] = []
    panel.dictionary_removed.connect(lambda: removed.append(None))

    panel.remove(0)

    assert rmtree_calls == [], "rmtree must not run when release callback refuses"
    assert dict_dir.exists()
    assert any("mining run" in w.lower() for w in warned), warned
    assert removed == []
    assert [e.dict_id for e in panel.get_chain()[:1]] == ["a"]


def test_release_callback_runs_before_rmtree(qapp, qtbot, monkeypatch, tmp_path, confirm_remove):
    """The release callback must fire strictly before rmtree so cached sqlite
    handles are dropped first (Issue #30 Win11 file-lock ordering)."""
    dict_dir = tmp_path / "a"
    dict_dir.mkdir()
    (dict_dir / "index.sqlite").write_bytes(b"placeholder")

    events: list[str] = []

    def _release():
        events.append("release")
        return True

    real_rmtree = dsp_mod.shutil.rmtree

    def _spy_rmtree(path, *args, **kwargs):
        events.append("rmtree")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(dsp_mod.shutil, "rmtree", _spy_rmtree)

    panel = DictionarySettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    panel.set_chain(
        (
            ChainEntry(kind="indexed", dict_id="a", enabled=True),
            ChainEntry(kind="jisho", dict_id=None, enabled=True),
        )
    )
    panel.set_release_callback(_release)

    panel.remove(0)

    # release fires synchronously on the GUI thread before the off-thread
    # rmtree is dispatched; wait for the delete to land.
    qtbot.waitUntil(lambda: not dict_dir.exists(), timeout=3000)
    assert events == ["release", "rmtree"], events


def test_remove_without_release_callback_still_works(qapp, qtbot, tmp_path, confirm_remove):
    """Unwired panel (tests, headless) must keep the pre-Issue-#30 behaviour:
    skip the release step entirely and just delete."""
    dict_dir = tmp_path / "a"
    dict_dir.mkdir()
    (dict_dir / "index.sqlite").write_bytes(b"placeholder")

    panel = DictionarySettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    panel.set_chain(
        (
            ChainEntry(kind="indexed", dict_id="a", enabled=True),
            ChainEntry(kind="jisho", dict_id=None, enabled=True),
        )
    )
    # Intentionally do NOT call set_release_callback.

    panel.remove(0)

    qtbot.waitUntil(lambda: not dict_dir.exists(), timeout=3000)
    assert [e.kind for e in panel.get_chain()] == ["jisho"]


def test_robust_rmtree_exhausts_retries_and_raises(monkeypatch, tmp_path):
    """After ``retries`` failures the helper must surface the last OSError."""
    target = tmp_path / "doomed"
    target.mkdir()

    attempts = {"n": 0}

    def _always_fail(*args, **kwargs):
        attempts["n"] += 1
        raise PermissionError("simulated")

    monkeypatch.setattr(dsp_mod.shutil, "rmtree", _always_fail)
    monkeypatch.setattr(dsp_mod.time, "sleep", lambda _s: None)

    with pytest.raises(PermissionError):
        dsp_mod._robust_rmtree(target, retries=3, delay_s=0)

    assert attempts["n"] == 3


def test_right_click_jisho_row_shows_no_menu(qapp, qtbot, monkeypatch, tmp_path):
    """Jisho is an online fallback — no zip, no re-import, no menu."""
    panel = DictionarySettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    panel.set_chain((ChainEntry(kind="jisho", dict_id=None, enabled=True),))

    constructed = _patch_menu_exec(monkeypatch, "Re-import…")

    emitted: list[str] = []
    panel.reimport_dict_requested.connect(emitted.append)
    jmdict_fired: list[None] = []
    panel.reimport_jmdict_requested.connect(lambda: jmdict_fired.append(None))

    item = panel._list.item(0)
    pos = panel._list.visualItemRect(item).center()
    panel._on_row_context_menu(pos)

    assert constructed == [], "Jisho row must not open a context menu"
    assert emitted == []
    assert jmdict_fired == []


def test_request_resource_release_returns_true_when_unset(qapp, qtbot, tmp_path):
    """Headless/test setups never wire the release callback; the proxy must
    treat 'no callback' as a successful no-op so re-import flows do not stall."""
    panel = DictionarySettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    assert panel.request_resource_release() is True


def test_request_resource_release_proxies_callback_return(qapp, qtbot, tmp_path):
    """The proxy forwards the callback's return value verbatim so settings_tab
    can branch on True/False (mining run in flight refuses with False)."""
    panel = DictionarySettingsPanel(tmp_path)
    qtbot.addWidget(panel)

    calls = {"n": 0}

    def _ok():
        calls["n"] += 1
        return True

    panel.set_release_callback(_ok)
    assert panel.request_resource_release() is True
    assert calls["n"] == 1

    panel.set_release_callback(lambda: False)
    assert panel.request_resource_release() is False


# === Issue #45: configurable dictionary storage path ===


def test_dicts_root_selector_populated_from_constructor(qapp, qtbot, tmp_path):
    """The storage-folder selector must display the path passed to __init__
    so the Settings tab reflects whatever dicts_root is on the live config."""
    panel = DictionarySettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    assert panel.dicts_root_selector.get_path() == str(tmp_path)


def test_get_dicts_root_returns_selector_value(qapp, qtbot, tmp_path):
    """get_dicts_root reads from the selector so settings_tab sees the user's
    in-progress pick (not the value the panel was constructed with)."""
    panel = DictionarySettingsPanel(tmp_path)
    qtbot.addWidget(panel)

    new_path = tmp_path / "elsewhere"
    new_path.mkdir()
    panel.dicts_root_selector.set_path(str(new_path))

    assert panel.get_dicts_root() == new_path


def test_get_dicts_root_falls_back_to_internal_when_selector_empty(qapp, qtbot, tmp_path):
    """An empty selector must collapse to the panel's last-known _dicts_root,
    never to Path('') — otherwise the save flow would silently rewrite the
    storage root to the cwd."""
    panel = DictionarySettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    panel.dicts_root_selector.set_path("")

    assert panel.get_dicts_root() == tmp_path


def test_set_dicts_root_updates_selector(qapp, qtbot, tmp_path):
    """Updating dicts_root externally (e.g. config reload) must refresh the
    visible field so the UI doesn't drift from the saved config."""
    panel = DictionarySettingsPanel(tmp_path)
    qtbot.addWidget(panel)

    new_path = tmp_path / "new"
    new_path.mkdir()
    panel.set_dicts_root(new_path)

    assert panel.dicts_root_selector.get_path() == str(new_path)
    assert panel.get_dicts_root() == new_path


def test_reset_dicts_root_button_restores_default(qapp, qtbot, tmp_path):
    """The Reset button must repopulate the selector with ANKI_MINER_HOME/dicts
    so users can roll back a mistaken pick without restarting."""
    from anki_miner.config.paths import ANKI_MINER_HOME

    panel = DictionarySettingsPanel(tmp_path)
    qtbot.addWidget(panel)
    other = tmp_path / "other"
    other.mkdir()
    panel.dicts_root_selector.set_path(str(other))

    panel._reset_dicts_root_btn.click()

    assert panel.get_dicts_root() == ANKI_MINER_HOME / "dicts"


# ---------------------------------------------------------------------------
# OVH-053 — registry scan deferred to first showEvent
# ---------------------------------------------------------------------------


class TestShowEventDeferral:
    """DictionarySettingsPanel defers DictionaryRegistry.load() off the paint
    path (OVH-053): constructing the panel + calling set_chain must NOT scan
    the registry; only the first showEvent triggers the scan."""

    def test_construction_does_not_call_registry_load(self, qapp, qtbot, tmp_path, monkeypatch):
        """Constructing the panel (including _load_config's set_chain call) must
        not call DictionaryRegistry.load()."""
        from anki_miner.services.dictionary.registry import DictionaryRegistry

        load_calls: list[None] = []
        real_load = DictionaryRegistry.load

        def _spy_load(self):
            load_calls.append(None)
            return real_load(self)

        monkeypatch.setattr(DictionaryRegistry, "load", _spy_load)

        panel = DictionarySettingsPanel(tmp_path)
        qtbot.addWidget(panel)
        panel.set_chain(AnkiMinerConfig().dictionary_chain)

        assert load_calls == [], "DictionaryRegistry.load() must not run before first showEvent"

    def test_first_show_event_triggers_exactly_one_scan(self, qapp, qtbot, tmp_path, monkeypatch):
        """The first showEvent must trigger exactly one DictionaryRegistry.load()."""
        from anki_miner.services.dictionary.registry import DictionaryRegistry

        load_calls: list[None] = []
        real_load = DictionaryRegistry.load

        def _spy_load(self):
            load_calls.append(None)
            return real_load(self)

        monkeypatch.setattr(DictionaryRegistry, "load", _spy_load)

        panel = DictionarySettingsPanel(tmp_path)
        qtbot.addWidget(panel)
        panel.set_chain(AnkiMinerConfig().dictionary_chain)

        assert load_calls == []
        panel.show()
        # Scan now runs off the GUI thread.
        qtbot.waitUntil(lambda: len(load_calls) == 1, timeout=3000)
        _wait_scan(panel, qtbot)
        assert len(load_calls) == 1, "First showEvent must trigger exactly one registry scan"

    def test_second_show_event_does_not_rescan(self, qapp, qtbot, tmp_path, monkeypatch):
        """Showing the panel a second time must not re-scan (guard prevents it)."""
        from anki_miner.services.dictionary.registry import DictionaryRegistry

        load_calls: list[None] = []
        real_load = DictionaryRegistry.load

        def _spy_load(self):
            load_calls.append(None)
            return real_load(self)

        monkeypatch.setattr(DictionaryRegistry, "load", _spy_load)

        panel = DictionarySettingsPanel(tmp_path)
        qtbot.addWidget(panel)
        panel.set_chain(AnkiMinerConfig().dictionary_chain)

        panel.show()
        qtbot.waitUntil(lambda: len(load_calls) == 1, timeout=3000)
        _wait_scan(panel, qtbot)

        panel.hide()
        panel.show()
        assert len(load_calls) == 1, "Second showEvent must not re-scan"


# ---------------------------------------------------------------------------
# OVH disk-scan-off-thread — registry scan + remove rmtree run off the GUI thread
# ---------------------------------------------------------------------------


class TestOffThreadDiskWork:
    """The first-show registry scan and the Remove rmtree must not block the
    GUI thread; both run on a worker and render/finish back on the GUI thread."""

    def test_first_show_scan_runs_off_gui_thread(self, qapp, qtbot, tmp_path, monkeypatch):
        import threading

        from anki_miner.services.dictionary.registry import DictionaryRegistry

        main_id = threading.get_ident()
        scan_threads: list[int] = []
        real_load = DictionaryRegistry.load

        def _spy_load(self):
            scan_threads.append(threading.get_ident())
            return real_load(self)

        monkeypatch.setattr(DictionaryRegistry, "load", _spy_load)

        panel = DictionarySettingsPanel(tmp_path)
        qtbot.addWidget(panel)
        panel.set_chain(AnkiMinerConfig().dictionary_chain)
        panel.show()

        qtbot.waitUntil(lambda: bool(scan_threads), timeout=3000)
        _wait_scan(panel, qtbot)
        assert scan_threads and all(t != main_id for t in scan_threads), scan_threads

    def test_remove_rmtree_runs_off_gui_thread(self, qapp, qtbot, tmp_path, monkeypatch, confirm_remove):
        import threading

        main_id = threading.get_ident()
        rmtree_threads: list[int] = []
        real_rmtree = dsp_mod.shutil.rmtree

        def _spy_rmtree(path, *a, **kw):
            rmtree_threads.append(threading.get_ident())
            return real_rmtree(path, *a, **kw)

        monkeypatch.setattr(dsp_mod.shutil, "rmtree", _spy_rmtree)

        dict_dir = tmp_path / "a"
        dict_dir.mkdir()
        (dict_dir / "index.sqlite").write_bytes(b"placeholder")

        panel = DictionarySettingsPanel(tmp_path)
        qtbot.addWidget(panel)
        panel.set_chain(
            (
                ChainEntry(kind="indexed", dict_id="a", enabled=True),
                ChainEntry(kind="jisho", dict_id=None, enabled=True),
            )
        )

        panel.remove(0)
        qtbot.waitUntil(lambda: not dict_dir.exists(), timeout=3000)
        assert rmtree_threads and all(t != main_id for t in rmtree_threads), rmtree_threads

    def test_refresh_registry_scans_off_gui_thread(self, qapp, qtbot, tmp_path, monkeypatch):
        """The import-finished refresh path (refresh_registry) scans off-thread."""
        import threading

        from anki_miner.services.dictionary.registry import DictionaryRegistry

        main_id = threading.get_ident()
        scan_threads: list[int] = []
        real_load = DictionaryRegistry.load

        def _spy_load(self):
            scan_threads.append(threading.get_ident())
            return real_load(self)

        monkeypatch.setattr(DictionaryRegistry, "load", _spy_load)

        panel = DictionarySettingsPanel(tmp_path)
        qtbot.addWidget(panel)
        panel.set_chain(AnkiMinerConfig().dictionary_chain)
        panel.show()
        _wait_scan(panel, qtbot)
        scan_threads.clear()

        # Simulate the import flow finishing → refresh_registry().
        panel.refresh_registry()
        qtbot.waitUntil(lambda: bool(scan_threads), timeout=3000)
        _wait_scan(panel, qtbot)
        assert scan_threads and all(t != main_id for t in scan_threads), scan_threads

    def test_remove_disables_then_reenables_button(self, qapp, qtbot, tmp_path, confirm_remove):
        dict_dir = tmp_path / "a"
        dict_dir.mkdir()
        (dict_dir / "index.sqlite").write_bytes(b"placeholder")

        panel = DictionarySettingsPanel(tmp_path)
        qtbot.addWidget(panel)
        panel.set_chain(
            (
                ChainEntry(kind="indexed", dict_id="a", enabled=True),
                ChainEntry(kind="jisho", dict_id=None, enabled=True),
            )
        )

        panel.remove(0)
        # Disabled immediately on dispatch (still in flight).
        assert panel._remove_btn.isEnabled() is False
        # Re-enabled once the off-thread delete completes.
        qtbot.waitUntil(lambda: panel._remove_btn.isEnabled(), timeout=3000)
        assert not dict_dir.exists()


# ---------------------------------------------------------------------------
# Dropped post-import rescan race — a refresh_registry() requested while a scan
# is in flight must re-dispatch a fresh scan so the latest disk state renders.
# ---------------------------------------------------------------------------


class TestRescanWhileInFlight:
    def test_refresh_during_in_flight_scan_renders_latest_disk_state(self, qapp, qtbot, tmp_path, monkeypatch):
        """A refresh_registry() requested while scan A (pre-import disk state) is
        in flight must trigger a SECOND scan that renders the post-import dict,
        not the stale first one."""
        import threading

        from anki_miner.services.dictionary.registry import DictionaryRegistry

        gate = threading.Event()
        load_calls: list[int] = []
        real_load = DictionaryRegistry.load

        def _spy_load(self):
            n = len(load_calls)
            load_calls.append(n)
            if n == 0:
                # First (pre-import) scan: block until the test has requested a
                # refresh and dropped a new dict on disk.
                gate.wait(timeout=5.0)
            return real_load(self)

        monkeypatch.setattr(DictionaryRegistry, "load", _spy_load)

        panel = DictionarySettingsPanel(tmp_path)
        qtbot.addWidget(panel)
        panel.set_chain(
            (
                ChainEntry(kind="indexed", dict_id="latedict", enabled=True),
                ChainEntry(kind="jisho", dict_id=None, enabled=True),
            )
        )

        # First-show scan A starts and blocks in _spy_load (disk has no dict yet).
        panel.show()
        qtbot.waitUntil(lambda: len(load_calls) == 1, timeout=3000)
        assert panel._scan_in_flight is True

        # Import finishes: dict now on disk + refresh requested while A is busy.
        _make_dict_on_disk(
            tmp_path,
            "latedict",
            fmt="yomitan",
            schema_version=SCHEMA_VERSION,
            source_name="Late Dict",
        )
        panel.refresh_registry()
        # Request was deferred, not dropped.
        assert panel._rescan_pending is True

        # Release scan A; the pending rescan must re-dispatch (load called twice).
        gate.set()
        qtbot.waitUntil(lambda: len(load_calls) == 2, timeout=3000)
        _wait_scan(panel, qtbot)
        assert panel._rescan_pending is False

        # The panel must render the post-import dict (latest state), not stale.
        from PyQt6.QtWidgets import QLabel

        row = panel._row_widget(0)
        assert row is not None
        texts = [lbl.text() for lbl in row.findChildren(QLabel)]
        assert any("Late Dict" in t for t in texts), texts
        assert panel._registry is not None
        meta = panel._registry.get("latedict")
        assert meta is not None and meta.source_name == "Late Dict"
