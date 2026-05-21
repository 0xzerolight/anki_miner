"""Smoke tests for DictionarySettingsPanel."""

from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication, QMessageBox

from anki_miner.config import AnkiMinerConfig, ChainEntry
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


@pytest.fixture
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def confirm_remove(monkeypatch):
    """Auto-accept the 'Remove dictionary' QMessageBox confirmation."""
    monkeypatch.setattr(
        "anki_miner.gui.widgets.panels.dictionary_settings_panel.QMessageBox.question",
        lambda *a, **kw: QMessageBox.StandardButton.Yes,
    )


def test_panel_renders_default_chain(qapp, monkeypatch, tmp_path):
    panel = DictionarySettingsPanel(tmp_path)
    panel.set_chain(AnkiMinerConfig().dictionary_chain)
    chain = panel.get_chain()
    # Default has two entries; one indexed (missing on disk -> keeps entry), one jisho
    assert len(chain) == 2
    assert chain[1].kind == "jisho"


def test_reorder_moves_entry_up(qapp, monkeypatch, tmp_path):
    panel = DictionarySettingsPanel(tmp_path)
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


def test_chain_changed_emits_on_reorder_remove_and_toggle(qapp, monkeypatch, tmp_path, confirm_remove):
    panel = DictionarySettingsPanel(tmp_path)
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


def test_jisho_remove_is_noop(qapp, monkeypatch, tmp_path):
    panel = DictionarySettingsPanel(tmp_path)
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


def test_edge_reorder_calls_are_noops(qapp, monkeypatch, tmp_path):
    panel = DictionarySettingsPanel(tmp_path)
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


def test_checkbox_toggle_preserved_on_reorder(qapp, monkeypatch, tmp_path, confirm_remove):
    """The implementer's deviation: get_chain()-resync before mutation must
    preserve a user's checkbox toggle across move_up/move_down/remove."""
    panel = DictionarySettingsPanel(tmp_path)
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


def test_remove_deletes_dict_folder_on_disk(qapp, monkeypatch, tmp_path, confirm_remove):
    """Regression: remove() must delete dicts_root/<dict_id>/ so a re-add of the
    same dict does not hit the importer's 'already exists' guard."""
    dict_dir = tmp_path / "a"
    dict_dir.mkdir()
    (dict_dir / "index.sqlite").write_bytes(b"placeholder")

    panel = DictionarySettingsPanel(tmp_path)
    panel.set_chain(
        (
            ChainEntry(kind="indexed", dict_id="a", enabled=True),
            ChainEntry(kind="jisho", dict_id=None, enabled=True),
        )
    )

    panel.remove(0)

    assert not dict_dir.exists(), "remove() must rmtree the dict folder"
    chain = panel.get_chain()
    assert [e.kind for e in chain] == ["jisho"]


def test_remove_cancelled_keeps_dict_and_chain(qapp, monkeypatch, tmp_path):
    """Clicking 'No' on the confirm dialog must leave both disk + chain intact."""
    monkeypatch.setattr(
        "anki_miner.gui.widgets.panels.dictionary_settings_panel.QMessageBox.question",
        lambda *a, **kw: QMessageBox.StandardButton.No,
    )

    dict_dir = tmp_path / "a"
    dict_dir.mkdir()
    (dict_dir / "index.sqlite").write_bytes(b"placeholder")

    panel = DictionarySettingsPanel(tmp_path)
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


def test_remove_tolerates_missing_dict_folder(qapp, tmp_path, confirm_remove):
    """If the dict folder is already gone (e.g. user deleted it manually), remove()
    should still drop the in-memory entry instead of erroring."""
    # No folder created on disk.

    panel = DictionarySettingsPanel(tmp_path)
    panel.set_chain(
        (
            ChainEntry(kind="indexed", dict_id="ghost", enabled=True),
            ChainEntry(kind="jisho", dict_id=None, enabled=True),
        )
    )

    panel.remove(0)

    chain = panel.get_chain()
    assert [e.kind for e in chain] == ["jisho"]


def test_stale_yomitan_row_shows_warning_and_reimport_button(qapp, tmp_path):
    """A Yomitan dictionary with outdated schema_version renders the stale UI."""
    _make_dict_on_disk(
        tmp_path,
        "stale-yomi",
        fmt="yomitan",
        schema_version=SCHEMA_VERSION - 1,
        source_name="Stale Yomi",
    )
    panel = DictionarySettingsPanel(tmp_path)
    panel.set_chain(
        (
            ChainEntry(kind="indexed", dict_id="stale-yomi", enabled=True),
            ChainEntry(kind="jisho", dict_id=None, enabled=True),
        )
    )

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
    assert any("re-import for new formatting" in t for t in label_texts)

    emitted: list[str] = []
    panel.reimport_dict_requested.connect(emitted.append)
    jmdict_fired: list[None] = []
    panel.reimport_jmdict_requested.connect(lambda: jmdict_fired.append(None))

    row.reimport_button.click()
    assert emitted == ["stale-yomi"]
    assert jmdict_fired == [], "Yomitan row must not fire the JMdict signal"


def test_stale_jmdict_row_fires_reimport_jmdict_signal(qapp, tmp_path):
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
    panel.set_chain(
        (
            ChainEntry(kind="indexed", dict_id="jmdict-english", enabled=True),
            ChainEntry(kind="jisho", dict_id=None, enabled=True),
        )
    )

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


def test_current_schema_row_has_no_stale_ui(qapp, tmp_path):
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
    panel.set_chain(
        (
            ChainEntry(kind="indexed", dict_id="fresh-yomi", enabled=True),
            ChainEntry(kind="jisho", dict_id=None, enabled=True),
        )
    )

    row = panel._row_widget(0)
    assert row is not None
    assert row.stale is False
    assert row.reimport_button is None

    from PyQt6.QtWidgets import QLabel

    labels = row.findChildren(QLabel)
    label_texts = [lbl.text() for lbl in labels]
    assert not any(t.startswith("⚠") for t in label_texts)
    assert not any("re-import for new formatting" in t for t in label_texts)


def test_global_button_labeled_reimport_all(qapp, tmp_path):
    """The top-level button reads 'Reimport All', not the legacy 'Reimport JMdict'."""
    panel = DictionarySettingsPanel(tmp_path)
    assert panel._reimport_btn.text() == "Reimport All"


def test_reimport_all_signal_fires_on_button_click(qapp, tmp_path):
    """Clicking the top-level button emits the new reimport_all_requested signal,
    not the per-row reimport_jmdict_requested signal."""
    panel = DictionarySettingsPanel(tmp_path)

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


def test_right_click_non_stale_yomitan_row_emits_reimport_dict_requested(qapp, monkeypatch, tmp_path):
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
    panel.set_chain(
        (
            ChainEntry(kind="indexed", dict_id="fresh-yomi", enabled=True),
            ChainEntry(kind="jisho", dict_id=None, enabled=True),
        )
    )

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


def test_right_click_jmdict_row_emits_reimport_jmdict_requested(qapp, monkeypatch, tmp_path):
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
    panel.set_chain(
        (
            ChainEntry(kind="indexed", dict_id="jmdict-english", enabled=True),
            ChainEntry(kind="jisho", dict_id=None, enabled=True),
        )
    )

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


def test_right_click_jisho_row_shows_no_menu(qapp, monkeypatch, tmp_path):
    """Jisho is an online fallback — no zip, no re-import, no menu."""
    panel = DictionarySettingsPanel(tmp_path)
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
