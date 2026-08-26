"""The four chain editors are one drag-reorderable priority list (D13).

Dictionaries, word audio, frequency and pitch each used to draw their own
editor. This file is about the one component that replaced all four, and most
of it is about the risk that replacement carries: a chain is an *ordered* list
whose entries each carry an enabled flag, so a reorder that rebinds a flag onto
its neighbour silently switches off a source the user is still using.

Reordering is therefore always tested through the model. That is not a
convenience: ``QListView::dropEvent`` performs an ``InternalMove`` by calling
``QAbstractItemModel::moveRow``, so driving ``moveRow`` exercises the exact code
path a drag takes, item widgets and all, without needing a real drag loop.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QAbstractItemView, QPushButton

from anki_miner.config import AudioSourceEntry, ChainEntry, FreqEntry, PitchSourceEntry
from anki_miner.gui.widgets.enhanced import FileSelector
from anki_miner.gui.widgets.enhanced.modern_button import ModernButton
from anki_miner.gui.widgets.panels.audio_pack_settings_panel import AudioPackSettingsPanel
from anki_miner.gui.widgets.panels.chain_priority_list import ChainPriorityList, ChainSourceRow
from anki_miner.gui.widgets.panels.chain_settings_panel_base import ChainSettingsPanelBase
from anki_miner.gui.widgets.panels.dictionary_settings_panel import DictionarySettingsPanel
from anki_miner.gui.widgets.panels.frequency_settings_panel import FrequencySettingsPanel
from anki_miner.gui.widgets.panels.pitch_settings_panel import PitchSettingsPanel
from anki_miner.services.frequency.registry import FreqSourceMeta
from anki_miner.services.frequency.storage import SCHEMA_VERSION as FREQ_SCHEMA_VERSION

#: Every panel, with a factory for a chain of *n* removable entries and the
#: reader that turns an entry back into the id the test names it by.
PANELS = ["dictionary", "audio", "frequency", "pitch"]


def _chain(kind: str, *ids_enabled: tuple[str, bool]):
    """Build a chain of entries for *kind* from ``(id, enabled)`` pairs."""
    if kind == "dictionary":
        return tuple(ChainEntry(kind="indexed", dict_id=i, enabled=e) for i, e in ids_enabled)
    if kind == "audio":
        return tuple(AudioSourceEntry(kind="pack", pack_id=i, enabled=e) for i, e in ids_enabled)
    if kind == "frequency":
        return tuple(FreqEntry(source_id=i, enabled=e) for i, e in ids_enabled)
    return tuple(PitchSourceEntry(source_id=i, enabled=e) for i, e in ids_enabled)


def _ids(chain) -> list[str]:
    """The source ids of a chain, in order."""
    return [getattr(e, "dict_id", None) or getattr(e, "pack_id", None) or getattr(e, "source_id", None) for e in chain]


def _flags(chain) -> list[tuple[str, bool]]:
    """``(id, enabled)`` for each entry, in order."""
    return list(zip(_ids(chain), [e.enabled for e in chain], strict=True))


def _make_panel(kind: str, qtbot, tmp_path: Path, *ids_enabled: tuple[str, bool]):
    """Build a panel of *kind* already holding a chain, with no disk scan.

    Every panel but the dictionary one accepts pre-built registry metadata, and
    the dictionary panel renders happily with none. Nothing here needs real
    metadata: these tests are about order and flags, not about rendering.
    """
    factory = {
        "dictionary": DictionarySettingsPanel,
        "audio": AudioPackSettingsPanel,
        "frequency": FrequencySettingsPanel,
        "pitch": PitchSettingsPanel,
    }[kind]
    panel = factory(tmp_path)
    qtbot.addWidget(panel)
    chain = _chain(kind, *ids_enabled)
    if kind == "dictionary":
        panel.set_chain(chain)
    else:
        panel.set_chain(chain, registry_meta={})
    return panel


@pytest.fixture(params=PANELS)
def kind(request) -> str:
    """Each of the four chains in turn."""
    return request.param


@pytest.fixture
def panel(kind, qtbot, tmp_path: Path):
    """One of the four panels, holding A(enabled), B(enabled), C(enabled)."""
    return _make_panel(kind, qtbot, tmp_path, ("a", True), ("b", True), ("c", True))


# --------------------------------------------------------------- the component


class TestTheListItself:
    def test_it_is_configured_for_internal_moves(self, qtbot):
        widget = ChainPriorityList()
        qtbot.addWidget(widget)

        assert widget.dragDropMode() == QAbstractItemView.DragDropMode.InternalMove
        assert widget.defaultDropAction() == Qt.DropAction.MoveAction
        assert widget.showDropIndicator() is True

    def test_a_completed_move_announces_itself_once(self, qtbot):
        widget = ChainPriorityList()
        qtbot.addWidget(widget)
        for _ in range(3):
            widget.addItem("row")
        moves: list[None] = []
        widget.order_changed.connect(lambda: moves.append(None))

        assert widget.move_row(0, 2) is True

        assert len(moves) == 1

    def test_all_four_panels_render_from_it(self, panel):
        assert isinstance(panel._list, ChainPriorityList)
        assert all(isinstance(panel._row_widget(i), ChainSourceRow) for i in range(panel._list.count()))


# ------------------------------------------------------------------ reordering


class TestReorder:
    @pytest.mark.parametrize(("source", "target"), [(0, 2), (2, 0), (1, 2)])
    def test_a_drag_persists_the_order_it_produced(self, panel, source, target):
        changes: list[None] = []
        panel.chain_changed.connect(lambda: changes.append(None))

        panel._list.move_row(source, target)

        expected = ["a", "b", "c"]
        expected.insert(target, expected.pop(source))
        assert _ids(panel.get_chain()) == expected
        assert len(changes) == 1

    def test_the_arrows_produce_exactly_what_a_drag_produces(self, kind, qtbot, tmp_path):
        dragged = _make_panel(kind, qtbot, tmp_path / "d", ("a", True), ("b", True), ("c", True))
        clicked = _make_panel(kind, qtbot, tmp_path / "c", ("a", True), ("b", True), ("c", True))

        dragged._list.move_row(0, 2)
        clicked._list.setCurrentRow(0)
        clicked.move_down(0)
        clicked.move_down(1)

        assert _ids(dragged.get_chain()) == _ids(clicked.get_chain()) == ["b", "c", "a"]

    def test_a_rows_arrow_moves_that_row_not_the_selected_one(self, kind, qtbot, tmp_path):
        """The reason the buttons moved onto the rows.

        A toolbar arrow acted on ``currentRow()``, so reordering meant click the
        row, then travel to the far corner, then click up. On the row itself the
        button has to mean *this* row -- including when the selection is
        somewhere else entirely, which is what a first click would leave behind.
        """
        widget = _make_panel(kind, qtbot, tmp_path, ("a", True), ("b", True), ("c", True))
        widget._list.setCurrentRow(0)

        widget._row_widget(2).up_button.click()

        assert _ids(widget.get_chain()) == ["a", "c", "b"]

    def test_the_arrows_stop_at_the_ends_of_the_list(self, kind, qtbot, tmp_path):
        """A button that cannot do anything should not invite a press."""
        widget = _make_panel(kind, qtbot, tmp_path, ("a", True), ("b", True), ("c", True))

        rows = widget._rows()

        assert not rows[0].up_button.isEnabled()
        assert rows[0].down_button.isEnabled()
        assert rows[1].up_button.isEnabled()
        assert rows[1].down_button.isEnabled()
        assert rows[-1].up_button.isEnabled()
        assert not rows[-1].down_button.isEnabled()

    def test_the_arrows_wake_up_when_a_row_leaves_the_end(self, kind, qtbot, tmp_path):
        """The ends of the list move when the rows do.

        Boundary state used to be derived once per render and never again, so a
        row that moved off the top kept a greyed-out ``↑`` and the row that
        arrived kept a live one that ``_move_row`` then refused.
        """
        widget = _make_panel(kind, qtbot, tmp_path, ("a", True), ("b", True), ("c", True))

        widget._row_widget(0).down_button.click()

        rows = widget._rows()
        assert _ids(widget.get_chain()) == ["b", "a", "c"]
        assert not rows[0].up_button.isEnabled()
        assert rows[1].up_button.isEnabled()
        assert rows[1].down_button.isEnabled()
        assert not rows[-1].down_button.isEnabled()

    def test_a_drag_resyncs_the_arrows_too(self, kind, qtbot, tmp_path):
        """A drag ends at the same place an arrow does, so it gets the same fix."""
        widget = _make_panel(kind, qtbot, tmp_path, ("a", True), ("b", True), ("c", True))

        widget._list.move_row(0, 2)

        rows = widget._rows()
        assert _ids(widget.get_chain()) == ["b", "c", "a"]
        assert not rows[0].up_button.isEnabled()
        assert rows[0].down_button.isEnabled()
        assert rows[-1].up_button.isEnabled()
        assert not rows[-1].down_button.isEnabled()

    def test_a_held_mutation_still_owns_the_arrows(self, kind, qtbot, tmp_path):
        """Re-deriving the ends must not talk over the mutation gate."""
        widget = _make_panel(kind, qtbot, tmp_path, ("a", True), ("b", True), ("c", True))
        widget.hold_mutation("scan")

        widget.move_down(0)

        assert _ids(widget.get_chain()) == ["a", "b", "c"]
        assert all(not row.up_button.isEnabled() and not row.down_button.isEnabled() for row in widget._rows())

    def test_a_mutation_switches_every_rows_arrows_off(self, kind, qtbot, tmp_path):
        """Moving the controls onto the rows must not escape the mutation gate."""
        widget = _make_panel(kind, qtbot, tmp_path, ("a", True), ("b", True), ("c", True))

        widget._set_reorder_controls_enabled(False)

        assert all(not row.up_button.isEnabled() and not row.down_button.isEnabled() for row in widget._rows())
        assert widget._list.dragEnabled() is False

    def test_the_arrows_still_work_after_a_rebuild(self, kind, qtbot, tmp_path):
        """A rebuild destroys every row widget, connections included."""
        widget = _make_panel(kind, qtbot, tmp_path, ("a", True), ("b", True), ("c", True))

        widget._rebuild_list()
        widget._row_widget(0).down_button.click()

        assert _ids(widget.get_chain()) == ["b", "a", "c"]

    def test_a_disabled_row_keeps_its_own_flag_across_a_drag(self, kind, qtbot, tmp_path):
        """The defect this whole component is guarded against.

        ``A(disabled), B(enabled)`` dragged to ``B, A`` must persist exactly
        ``B(enabled), A(disabled)``. Rebasing the chain by index rather than by
        row would hand A's flag to B and switch a live source off.

        The panel's own ``_chain`` is asserted as well as ``get_chain()``:
        ``get_chain()`` re-reads the live checkboxes, so it would paper over a
        mis-bound ``_chain`` for exactly as long as the rows survive -- and
        ``remove()`` indexes ``_chain`` directly.
        """
        widget = _make_panel(kind, qtbot, tmp_path, ("a", False), ("b", True))

        widget._list.move_row(0, 1)

        assert _flags(widget.get_chain()) == [("b", True), ("a", False)]
        assert _flags(widget._chain) == [("b", True), ("a", False)]

    def test_a_toggle_made_before_the_drag_travels_with_its_row(self, panel):
        panel._row_widget(1).checkbox.setChecked(False)

        panel._list.move_row(1, 0)

        assert _flags(panel.get_chain()) == [("b", False), ("a", True), ("c", True)]
        assert _flags(panel._chain) == [("b", False), ("a", True), ("c", True)]

    def test_the_row_widgets_survive_the_move(self, panel):
        rows = [panel._row_widget(i) for i in range(3)]

        panel._list.move_row(0, 2)

        assert [panel._row_widget(i) for i in range(3)] == [rows[1], rows[2], rows[0]]

    def test_a_moved_rows_toggle_still_reaches_the_panel(self, panel):
        panel._list.move_row(0, 2)
        changes: list[None] = []
        panel.chain_changed.connect(lambda: changes.append(None))

        panel._row_widget(2).checkbox.setChecked(False)

        assert changes == [None]
        assert _flags(panel.get_chain()) == [("b", True), ("c", True), ("a", False)]

    def test_moving_a_row_onto_itself_changes_nothing(self, panel):
        changes: list[None] = []
        panel.chain_changed.connect(lambda: changes.append(None))

        panel.move_up(0)
        panel.move_down(2)
        panel._sync_chain_from_visual_order()

        assert changes == []
        assert _ids(panel.get_chain()) == ["a", "b", "c"]

    @pytest.mark.parametrize("index", [-1, 3, 99])
    def test_an_out_of_range_move_changes_nothing(self, panel, index):
        changes: list[None] = []
        panel.chain_changed.connect(lambda: changes.append(None))

        panel.move_up(index)
        panel.move_down(index)

        assert changes == []
        assert _ids(panel.get_chain()) == ["a", "b", "c"]

    def test_the_arrows_follow_the_row_they_moved(self, panel):
        panel._list.setCurrentRow(0)

        panel.move_down(0)

        assert panel._list.currentRow() == 1


class TestReorderIsGated:
    @pytest.mark.parametrize("token_kind", ["scan", "remove", "import"])
    def test_no_reorder_survives_a_held_mutation(self, panel, token_kind):
        changes: list[None] = []
        panel.chain_changed.connect(lambda: changes.append(None))
        panel.hold_mutation(token_kind)

        panel.move_down(0)
        panel._sync_chain_from_visual_order()

        assert changes == []
        assert _ids(panel.get_chain()) == ["a", "b", "c"]

    def test_dragging_is_switched_off_with_the_arrows(self, panel):
        token = panel.hold_mutation("scan")
        assert panel._list.dragEnabled() is False

        panel.release(token)

        assert panel._list.dragEnabled() is True

    def test_the_loading_placeholder_cannot_be_dragged(self, panel):
        panel._show_loading_placeholder()

        item = panel._list.item(0)
        assert item.flags() & Qt.ItemFlag.ItemIsDragEnabled == Qt.ItemFlag.NoItemFlags

    def test_a_list_that_is_not_showing_the_chain_is_re_rendered_not_persisted(self, panel):
        """A stale visual order must never become the persisted one."""
        changes: list[None] = []
        panel.chain_changed.connect(lambda: changes.append(None))
        panel._list.takeItem(0)  # the list no longer matches _chain

        panel._sync_chain_from_visual_order()

        assert changes == []
        assert _ids(panel.get_chain()) == ["a", "b", "c"]
        assert panel._list.count() == 3


class TestLoadingWritesNothing:
    def test_setting_the_chain_emits_no_change(self, kind, qtbot, tmp_path):
        widget = _make_panel(kind, qtbot, tmp_path, ("a", True))
        changes: list[None] = []
        widget.chain_changed.connect(lambda: changes.append(None))

        reload = _chain(kind, ("a", False), ("b", True))
        if kind == "dictionary":
            widget.set_chain(reload)
        else:
            widget.set_chain(reload, registry_meta={})

        assert changes == []
        assert _flags(widget.get_chain()) == [("a", False), ("b", True)]

    def test_rebuilding_emits_no_change(self, panel):
        changes: list[None] = []
        panel.chain_changed.connect(lambda: changes.append(None))

        panel._rebuild_list()
        panel._show_loading_placeholder()
        panel._rebuild_list()

        assert changes == []

    def test_a_rebuild_cannot_be_mistaken_for_a_reorder(self, panel):
        """``_rebuilding`` has to hold across the whole clear/populate cycle."""
        seen: list[bool] = []
        panel.chain_changed.connect(lambda: seen.append(panel._rebuilding))

        panel._rebuild_list()

        assert seen == []
        assert panel._rebuilding is False


# ------------------------------------------------------------------- the chrome


def _glyph_controls(panel):
    """Every glyph-only control the panel shows: the move pairs and the trash.

    The move arrows sit on the rows rather than in the toolbar, so "the glyph
    controls" is per-row plus the one panel-level remove.
    """
    controls = [panel._remove_btn]
    for row in panel._rows():
        controls.extend((row.up_button, row.down_button))
    return controls


class TestOneClearAddAndOneRedTrash:
    def test_each_panel_offers_exactly_one_add_control(self, panel):
        """Accent is scarce (D41): one task action, so one accent button."""
        primaries = [b for b in panel.findChildren(ModernButton) if b.objectName() == "primary"]

        assert primaries == [panel._add_btn]

    def test_the_trash_is_the_only_destructive_control(self, panel):
        red = [b for b in panel.findChildren(QPushButton) if b.objectName() in {"danger", "critical"}]
        assert red == [panel._remove_btn]

    def test_removal_is_an_outline_not_a_fill(self, panel):
        """D41: solid red is reserved for the three irreversible actions."""
        assert panel._remove_btn.objectName() == "danger"

    def test_the_remove_glyph_is_flat_ui_text_not_an_emoji(self):
        """U+1F5D1 WASTEBASKET rendered as Noto's colour bin, not as UI text.

        The glyph carried U+FE0E to ask for text presentation, and Linux font
        matching ignores it: fontconfig hands the astral code point to the
        colour emoji font regardless, so the flat monochrome UI grew one 3D
        teal bin. There is no monochrome wastebasket to switch to -- any trash
        code point pulls the same font somewhere -- so the control says what it
        does with the same multiplication X the update banner already dismisses
        with, in the same family as the two arrows beside it.

        Pinned as an exact value on purpose. "Not astral" would not hold the
        line: U+2705 and U+2764 are inside the BMP and still default to emoji.
        """
        glyph = ChainSettingsPanelBase._REMOVE_GLYPH

        assert glyph == "✕"
        # One character, so no presentation selector is being relied on either.
        assert [ord(ch) for ch in glyph] == [0x2715]

    def test_the_glyph_controls_are_square(self, panel):
        for button in _glyph_controls(panel):
            assert button.maximumWidth() == button.minimumWidth() == button.minimumHeight()

    def test_every_glyph_control_says_what_it_is(self, panel):
        for button in _glyph_controls(panel):
            assert button.accessibleName()
            assert button.toolTip()

    def test_the_move_controls_are_not_accent_spenders(self, panel):
        """D41: the accent is Add. A reorder arrow is an ordinary control."""
        for row in panel._rows():
            assert row.up_button.objectName() == "secondary"
            assert row.down_button.objectName() == "secondary"

    def test_the_add_control_names_what_it_adds(self, panel):
        assert panel._add_btn.text() not in {"", "Add"}
        assert panel._add_btn.text().endswith("…")


class TestRowsCarryTheirOwnFacts:
    def test_the_enable_toggle_has_a_label(self, panel):
        row = panel._row_widget(0)
        assert row.checkbox.text() == "Enabled"

    def test_the_toggle_still_names_its_source_for_a_screen_reader(self, panel):
        row = panel._row_widget(0)
        assert "a" in row.checkbox.accessibleName()

    def test_metadata_is_per_row_not_one_shared_string(self, qtbot, tmp_path):
        panel = FrequencySettingsPanel(tmp_path)
        qtbot.addWidget(panel)
        panel.set_chain(
            (FreqEntry(source_id="big"), FreqEntry(source_id="small")),
            registry_meta={
                "big": _freq_meta("big", entry_count=40000),
                "small": _freq_meta("small", entry_count=12),
            },
        )

        # ``full_text`` throughout: the row labels elide to their current width, so
        # ``text()`` is a pixel-dependent truncation of what was set.
        assert panel._row_widget(0).metadata_label.full_text != panel._row_widget(1).metadata_label.full_text
        assert "40,000 entries" in panel._row_widget(0).metadata_label.full_text
        assert "12 entries" in panel._row_widget(1).metadata_label.full_text

    def test_an_empty_source_states_zero_rather_than_nothing(self, qtbot, tmp_path):
        panel = FrequencySettingsPanel(tmp_path)
        qtbot.addWidget(panel)
        panel.set_chain(
            (FreqEntry(source_id="empty"),),
            registry_meta={"empty": _freq_meta("empty", entry_count=0)},
        )

        assert "0 entries" in panel._row_widget(0).metadata_label.full_text

    def test_unknown_metadata_states_nothing_rather_than_zero(self, qtbot, tmp_path):
        panel = FrequencySettingsPanel(tmp_path)
        qtbot.addWidget(panel)
        panel.set_chain((FreqEntry(source_id="gone"),), registry_meta={})

        assert panel._row_widget(0).metadata_label.full_text == ""
        assert panel._row_widget(0).warning_label.full_text != ""

    def test_the_rows_spend_no_inline_colours(self, panel):
        """Inline ``gray``/``#d97706`` is a colour no theme could reach."""
        row = panel._row_widget(0)
        assert row.metadata_label.styleSheet() == ""
        assert row.warning_label.styleSheet() == ""
        assert row.metadata_label.objectName() == "chain-row-meta"
        assert row.warning_label.objectName() == "chain-row-warning"


class TestTheExplanationIsTrue:
    """Three chains return the first source that has an entry. One does not."""

    @pytest.mark.parametrize("kind", ["dictionary", "audio", "pitch"])
    def test_first_match_chains_say_so(self, kind, qtbot, tmp_path):
        widget = _make_panel(kind, qtbot, tmp_path, ("a", True))
        text = widget._explanation_label.text()

        assert "top to bottom" in text
        assert "first" in text
        assert "additive" not in text

    def test_frequency_says_it_is_additive_instead(self, qtbot, tmp_path):
        widget = _make_panel("frequency", qtbot, tmp_path, ("a", True))
        text = widget._explanation_label.text()

        assert "Every enabled source counts" in text
        assert "lowest rank" in text
        assert "source list" in text


class TestSettingAnchorsAreUnchanged:
    """W6-T3's settings search jumps to these ids; breaking one hides a setting."""

    EXPECTED = {
        "dictionary": {"dictionaries.storage_folder", "dictionaries.chain"},
        "audio": {"audio.chain", "audio.reading_tts"},
        "frequency": {"frequency.chain"},
        "pitch": {"pitch.chain"},
    }

    @pytest.mark.parametrize("kind", PANELS)
    def test_the_anchor_id_set_is_exactly_what_it_was(self, kind, qtbot, tmp_path):
        widget = _make_panel(kind, qtbot, tmp_path, ("a", True))

        assert {a.stable_id for a in widget.setting_anchors()} == self.EXPECTED[kind]

    @pytest.mark.parametrize("kind", PANELS)
    def test_the_chain_anchor_still_focuses_the_list(self, kind, qtbot, tmp_path):
        widget = _make_panel(kind, qtbot, tmp_path, ("a", True))
        by_id = {a.stable_id: a for a in widget.setting_anchors()}

        assert by_id[f"{widget.ANCHOR_NAMESPACE}.chain"].focus_widget is widget._list

    @pytest.mark.parametrize("kind", PANELS)
    def test_the_chain_anchor_indexes_the_explanation_and_the_add_control(self, kind, qtbot, tmp_path):
        widget = _make_panel(kind, qtbot, tmp_path, ("a", True))
        by_id = {a.stable_id: a for a in widget.setting_anchors()}
        text = by_id[f"{widget.ANCHOR_NAMESPACE}.chain"].search_text()

        assert widget._explanation_label.text() in text
        assert widget._add_btn.text() in text


class TestNoRawButtonsLeftInTheChainPanels:
    """T4 left the four chain panels as its one temporary allowlist; T5 closes it.

    ``FileSelector``'s own Browse button is excluded: it belongs to the shared
    selector, is rendered on a dozen screens, and its role is W3-T4's to set.
    """

    def test_every_button_the_panel_builds_carries_a_role(self, kind, qtbot, tmp_path):
        widget = _make_panel(kind, qtbot, tmp_path, ("a", True))

        plain = [
            button
            for button in widget.findChildren(QPushButton)
            if not isinstance(button, ModernButton) and not _inside_file_selector(button)
        ]
        assert plain == []


def _inside_file_selector(widget) -> bool:
    node = widget
    while node is not None:
        if isinstance(node, FileSelector):
            return True
        node = node.parent()
    return False


def _freq_meta(source_id: str, *, entry_count: int) -> FreqSourceMeta:
    return FreqSourceMeta(
        source_id=source_id,
        source_name=source_id,
        format="yomitan-freq",
        entry_count=entry_count,
        schema_ok=True,
        version=FREQ_SCHEMA_VERSION,
        db_path=Path("/nonexistent") / source_id / "index.sqlite",
    )
