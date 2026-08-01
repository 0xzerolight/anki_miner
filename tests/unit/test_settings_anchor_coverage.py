"""Every settable control in Settings must be addressable by search (D11).

A control with no anchor is invisible to settings search forever, and nothing
about the app looks broken when that happens — which is exactly why this file
exists. It walks the live ``SettingsTab`` widget tree, picks out the controls
that hold a user-settable value, and fails if any of them resolves neither to an
anchor nor to an explicitly stated ignore reason.
"""

from __future__ import annotations

import contextlib

import pytest
from PyQt6.QtWidgets import (
    QAbstractSpinBox,
    QCheckBox,
    QComboBox,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QRadioButton,
    QTextEdit,
    QTreeWidget,
    QWidget,
)

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.widgets.base.setting_anchor import SettingAnchor
from anki_miner.gui.widgets.settings_tab import SettingsTab

#: Widget types that hold a value the user sets. Buttons, labels, status badges
#: and scrollbars are deliberately absent: they are actions or chrome, not
#: settings, and indexing them would bury the real ones.
VALUE_WIDGET_TYPES: tuple[type[QWidget], ...] = (
    QCheckBox,
    QRadioButton,
    QComboBox,
    QAbstractSpinBox,
    QLineEdit,
    QListWidget,
    QTreeWidget,
    QPlainTextEdit,
    QTextEdit,
)

#: The chain panels' logical anchors. D13 rebuilds those rows; the ids stay.
CHAIN_ANCHOR_IDS = frozenset(
    {
        "dictionaries.chain",
        "audio.chain",
        "frequency.chain",
        "pitch.chain",
    }
)


@pytest.fixture
def tab(test_config: AnkiMinerConfig, qtbot):
    """A fully constructed SettingsTab, torn down like the other suites do."""
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


def _is_settable(widget: QWidget) -> bool:
    """Whether ``widget`` holds a value the user can set."""
    if not isinstance(widget, VALUE_WIDGET_TYPES):
        return False
    # Read-only fields (the copyable install commands) display, not collect.
    return not (isinstance(widget, QLineEdit) and widget.isReadOnly())


def _covering_anchor(widget: QWidget, anchors: tuple[SettingAnchor, ...]) -> SettingAnchor | None:
    """Return the nearest ancestor-or-self anchor addressing ``widget``.

    Nesting is how one logical anchor covers many child widgets: a spin box's
    internal line edit, a combo beside its Refresh button, the checkboxes inside
    a chain row. The nearest match wins so a composite never shadows a field
    anchored inside it.
    """
    anchored = {}
    for anchor in anchors:
        anchored.setdefault(anchor.widget, anchor)
        anchored.setdefault(anchor.focus_widget, anchor)
    node: QWidget | None = widget
    while node is not None:
        if node in anchored:
            return anchored[node]
        parent = node.parent()
        node = parent if isinstance(parent, QWidget) else None
    return None


def _ignore_reason(widget: QWidget, reasons: dict[QWidget, str]) -> str:
    node: QWidget | None = widget
    while node is not None:
        if node in reasons:
            return reasons[node]
        parent = node.parent()
        node = parent if isinstance(parent, QWidget) else None
    return ""


def _unanchored(tab: SettingsTab) -> list[str]:
    """Describe every settable control that resolves to no anchor."""
    anchors = tab.setting_anchors()
    reasons = dict(tab.setting_ignore_reasons())

    missing = []
    for widget in tab.findChildren(QWidget):
        if not _is_settable(widget):
            continue
        if _covering_anchor(widget, anchors) is not None:
            continue
        if _ignore_reason(widget, reasons):
            continue
        missing.append(f"{type(widget).__name__}(objectName={widget.objectName()!r})")
    return missing


def test_every_settable_control_resolves_to_an_anchor(tab):
    assert _unanchored(tab) == []


def test_a_new_unanchored_control_fails_coverage(tab):
    """The guard has to actually catch a control nobody anchored."""
    orphan = QCheckBox("Synthetic unanchored setting", tab.media_panel)
    tab.media_panel.main_layout.addWidget(orphan)

    missing = _unanchored(tab)
    assert any("QCheckBox" in entry for entry in missing)


def test_anchor_ids_are_unique(tab):
    ids = [anchor.stable_id for anchor in tab.setting_anchors()]
    assert len(ids) == len(set(ids)), sorted({i for i in ids if ids.count(i) > 1})


def test_every_anchor_id_is_namespaced(tab):
    for anchor in tab.setting_anchors():
        assert "." in anchor.stable_id, anchor.stable_id


def test_every_anchor_has_searchable_text(tab):
    for anchor in tab.setting_anchors():
        assert anchor.search_text(), anchor.stable_id


def test_the_four_chain_anchors_are_present(tab):
    ids = {anchor.stable_id for anchor in tab.setting_anchors()}
    assert ids >= CHAIN_ANCHOR_IDS


def test_chain_anchors_focus_the_list_not_a_transient_row(tab):
    by_id = {anchor.stable_id: anchor for anchor in tab.setting_anchors()}
    for panel, anchor_id in (
        (tab.dictionary_panel, "dictionaries.chain"),
        (tab.audio_panel, "audio.chain"),
        (tab.frequency_panel, "frequency.chain"),
        (tab.pitch_panel, "pitch.chain"),
    ):
        assert by_id[anchor_id].focus_widget is panel._list


def test_every_panel_contributes_anchors(tab):
    for host in tab.setting_anchor_hosts():
        assert host.setting_anchors(), type(host).__name__


def test_label_less_checkboxes_index_their_own_caption(tab):
    by_id = {anchor.stable_id: anchor for anchor in tab.setting_anchors()}
    checkbox = tab.filtering_panel.use_blacklist_checkbox

    assert checkbox.text() in by_id["filtering.use_blacklist_checkbox"].search_text()


def test_the_update_checkbox_is_anchored_on_the_tab_itself(tab):
    by_id = {anchor.stable_id: anchor for anchor in tab.setting_anchors()}

    assert by_id["app.check_for_updates"].focus_widget is tab.check_for_updates_checkbox


def test_ui_panel_controls_are_anchored(tab):
    by_id = {anchor.stable_id: anchor for anchor in tab.setting_anchors()}

    assert by_id["ui.language"].focus_widget is tab.ui_panel.language_combo
    assert by_id["ui.theme"].focus_widget is tab.ui_panel.gallery


def test_anchor_search_text_follows_a_relabelled_control(tab):
    """Proves the index is resolved lazily, not snapshotted at construction."""
    by_id = {anchor.stable_id: anchor for anchor in tab.setting_anchors()}
    anchor = by_id["filtering.use_blacklist_checkbox"]
    tab.filtering_panel.use_blacklist_checkbox.setText("ブラックリストを有効にする")

    assert "ブラックリストを有効にする" in anchor.search_text()
