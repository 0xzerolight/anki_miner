"""The resource checklist: grouped rows, disabled reasons, and the empty-selection guard."""

from PyQt6.QtCore import Qt

from anki_miner.gui.widgets.dialogs.resource_bundle_dialog import BundleChoice, ResourceBundleDialog
from anki_miner.services.resource_bundle import BundleItem

_DICT = BundleItem(kind="dictionary", item_id="jitendex", name="Jitendex", member="dictionary/jitendex/source.zip")
_FREQ = BundleItem(kind="frequency", item_id="jpdb", name="JPDB", member="frequency/jpdb/source.csv")
_KNOWN = BundleItem(kind="known_words", item_id="known_words", name="x", member="known_words.txt")
_BLACK = BundleItem(kind="blacklist", item_id="blacklist", name="x", member="blacklist.txt")
_WHITE = BundleItem(kind="whitelist", item_id="whitelist", name="x", member="whitelist.txt")


def _dialog(qtbot, choices):
    dialog = ResourceBundleDialog(choices, title="t", intro="i", accept_text="Go")
    qtbot.addWidget(dialog)
    return dialog


def test_rows_are_grouped_by_family_in_order(qtbot):
    choices = [
        BundleChoice(_DICT, "37 MB"),
        BundleChoice(_FREQ),
        BundleChoice(_KNOWN),
        BundleChoice(_BLACK),
        BundleChoice(_WHITE),
    ]
    dialog = _dialog(qtbot, choices)
    groups = [dialog.tree.topLevelItem(i) for i in range(dialog.tree.topLevelItemCount())]
    assert [g.childCount() for g in groups] == [1, 1, 1, 2]
    assert "37 MB" in groups[0].child(0).text(0)


def test_available_rows_start_checked_and_are_all_selected(qtbot):
    dialog = _dialog(qtbot, [BundleChoice(_DICT), BundleChoice(_FREQ)])
    assert dialog.selected_items() == [_DICT, _FREQ]


def test_disabled_rows_are_shown_but_never_selected(qtbot):
    dialog = _dialog(qtbot, [BundleChoice(_DICT, disabled_reason="already installed"), BundleChoice(_FREQ)])
    row = dialog.tree.topLevelItem(0).child(0)
    assert "already installed" in row.text(0)
    assert not row.flags() & Qt.ItemFlag.ItemIsEnabled
    assert dialog.selected_items() == [_FREQ]


def test_unchecking_everything_disables_the_accept_button(qtbot):
    dialog = _dialog(qtbot, [BundleChoice(_DICT)])
    dialog.tree.topLevelItem(0).child(0).setCheckState(0, Qt.CheckState.Unchecked)
    assert not dialog.accept_button.isEnabled()
    assert dialog.selected_items() == []
