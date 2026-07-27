"""Regression tests for FormPanel section/field ordering and setting anchors.

Bug: add_section() appended labels to the main QVBoxLayout, but every
add_field() call wrote to a single form layout registered once at init.
Result was every section heading rendering at the bottom of the panel,
under all the fields, instead of above its own fields.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtWidgets import QCheckBox, QFormLayout, QLabel, QLineEdit, QWidget  # noqa: E402

from anki_miner.gui.widgets.base.form_panel import FormPanel  # noqa: E402


def _layout_items(layout):
    return [layout.itemAt(i) for i in range(layout.count())]


def _layout_sequence(layout):
    """Flat sequence of ('label', text) and ('form', form_layout) entries.

    Skips the panel header row, which is the first child layout containing
    the title QLabel. Only sections + form layouts matter for the bug.
    """
    sequence = []
    for i, item in enumerate(_layout_items(layout)):
        widget = item.widget()
        sub = item.layout()
        if widget is not None and isinstance(widget, QLabel) and i > 0:
            sequence.append(("label", widget.text()))
        elif isinstance(sub, QFormLayout):
            sequence.append(("form", sub))
    return sequence


def test_section_heading_renders_above_its_fields(qapp, qtbot):
    panel = FormPanel("Test")
    qtbot.addWidget(panel)
    panel.add_section("Alpha")
    panel.add_field("a-field", QCheckBox())
    panel.add_section("Beta")
    panel.add_field("b-field", QCheckBox())

    seq = _layout_sequence(panel.main_layout)
    kinds = [kind for kind, _ in seq]
    labels = [val for kind, val in seq if kind == "label"]

    # Order must be: initial form (empty), Alpha label, Alpha form, Beta label, Beta form.
    assert kinds == ["form", "label", "form", "label", "form"], kinds
    assert labels == ["Alpha", "Beta"]


def test_each_section_owns_its_own_form_layout(qapp, qtbot):
    panel = FormPanel("Test")
    qtbot.addWidget(panel)
    panel.add_section("Alpha")
    panel.add_field("a-field", QCheckBox())
    panel.add_section("Beta")
    panel.add_field("b-field-1", QCheckBox())
    panel.add_field("b-field-2", QCheckBox())

    forms = [item for kind, item in _layout_sequence(panel.main_layout) if kind == "form"]
    initial, alpha, beta = forms
    assert initial.rowCount() == 0
    assert alpha.rowCount() == 1
    assert beta.rowCount() == 2


def test_fields_before_any_section_stay_in_initial_form(qapp, qtbot):
    panel = FormPanel("Test")
    qtbot.addWidget(panel)
    panel.add_field("pre", QCheckBox())
    panel.add_section("Alpha")
    panel.add_field("post", QCheckBox())

    forms = [item for kind, item in _layout_sequence(panel.main_layout) if kind == "form"]
    assert len(forms) == 2
    assert forms[0].rowCount() == 1  # pre-section field
    assert forms[1].rowCount() == 1  # post-section field


# ---------------------------------------------------------------------------
# Setting anchors (W6-T1, decision D11)
# ---------------------------------------------------------------------------


class _AnchoredPanel(FormPanel):
    """FormPanel that opts into anchoring, like the real settings panels."""

    ANCHOR_NAMESPACE = "demo"


def test_plain_form_panel_registers_no_anchors(qapp, qtbot):
    """A namespace-less FormPanel stays exactly as it was: no anchors, no raise."""
    panel = FormPanel("Test")
    qtbot.addWidget(panel)
    panel.add_field("a-field", QCheckBox())

    assert panel.setting_anchors() == ()


def test_add_field_registers_one_anchor_per_field(qapp, qtbot):
    panel = _AnchoredPanel("Test")
    qtbot.addWidget(panel)
    panel.tempo = QLineEdit()
    panel.add_field("Tempo", panel.tempo)

    anchors = panel.setting_anchors()
    assert len(anchors) == 1
    assert anchors[0].focus_widget is panel.tempo
    assert anchors[0].scroll_widget is panel.tempo
    assert anchors[0].highlight_widget is panel.tempo


def test_anchor_id_is_namespace_plus_panel_attribute(qapp, qtbot):
    """Ids come from code, never from translated text."""
    panel = _AnchoredPanel("Test")
    qtbot.addWidget(panel)
    panel.audio_format_combo = QLineEdit()
    panel.add_field("Audio Format", panel.audio_format_combo)

    assert panel.setting_anchors()[0].stable_id == "demo.audio_format_combo"


def test_anchor_id_drops_leading_underscores(qapp, qtbot):
    panel = _AnchoredPanel("Test")
    qtbot.addWidget(panel)
    panel._private = QLineEdit()
    panel.add_field("Private", panel._private)

    assert panel.setting_anchors()[0].stable_id == "demo.private"


def test_explicit_anchor_name_wins_over_derivation(qapp, qtbot):
    panel = _AnchoredPanel("Test")
    qtbot.addWidget(panel)
    panel.container = QWidget()
    panel.add_field("Chain", panel.container, anchor="chain")

    assert panel.setting_anchors()[0].stable_id == "demo.chain"


def test_widget_without_attribute_or_explicit_anchor_raises(qapp, qtbot):
    """A widget search can never address is a bug, not a silent gap."""
    panel = _AnchoredPanel("Test")
    qtbot.addWidget(panel)

    with pytest.raises(ValueError, match="anchor"):
        panel.add_field("Orphan", QLineEdit())


def test_duplicate_anchor_id_raises(qapp, qtbot):
    panel = _AnchoredPanel("Test")
    qtbot.addWidget(panel)
    panel.add_field("One", QLineEdit(), anchor="same")

    with pytest.raises(ValueError, match="same"):
        panel.add_field("Two", QLineEdit(), anchor="same")


def test_search_text_is_resolved_lazily(qapp, qtbot):
    """The index must reflect the translator installed *after* construction."""
    panel = _AnchoredPanel("Test")
    qtbot.addWidget(panel)
    panel.checkbox = QCheckBox("English label")
    panel.add_field("", panel.checkbox)

    anchor = panel.setting_anchors()[0]
    assert "English label" in anchor.search_text()

    panel.checkbox.setText("Translated label")
    assert "Translated label" in anchor.search_text()
    assert "English label" not in anchor.search_text()


def test_empty_label_checkbox_indexes_its_own_text(qapp, qtbot):
    panel = _AnchoredPanel("Test")
    qtbot.addWidget(panel)
    panel.checkbox = QCheckBox("Enable Blacklist")
    panel.add_field("", panel.checkbox, helper="Skip these words")

    assert set(panel.setting_anchors()[0].search_text()) >= {
        "Enable Blacklist",
        "Skip these words",
    }


def test_search_text_includes_label_section_and_panel_title(qapp, qtbot):
    panel = _AnchoredPanel("Word Filtering")
    qtbot.addWidget(panel)
    panel.add_section("Word Frequency")
    panel.spin = QLineEdit()
    panel.add_field("Max Frequency Rank", panel.spin, helper="Words missing are excluded")

    text = panel.setting_anchors()[0].search_text()
    assert set(text) == {
        "Max Frequency Rank",
        "Words missing are excluded",
        "Word Frequency",
        "Word Filtering",
    }


def test_line_edit_value_is_not_indexed(qapp, qtbot):
    """Only button captions are text; a QLineEdit's text() is user data."""
    panel = _AnchoredPanel("Test")
    qtbot.addWidget(panel)
    panel.edit = QLineEdit("secret-cookie-path")
    panel.add_field("Cookies file", panel.edit)

    assert "secret-cookie-path" not in panel.setting_anchors()[0].search_text()


def test_prose_label_row_is_not_anchored(qapp, qtbot):
    """add_field("", QLabel(...)) renders guidance, not a control."""
    panel = _AnchoredPanel("Test")
    qtbot.addWidget(panel)
    panel.add_field("", QLabel("Required before subtitle generation can run."))

    assert panel.setting_anchors() == ()


def test_anchor_ignore_records_a_reason_and_skips_the_anchor(qapp, qtbot):
    panel = _AnchoredPanel("Test")
    qtbot.addWidget(panel)
    widget = QLineEdit()
    panel.add_field("Command", widget, anchor_ignore="read-only install command")

    assert panel.setting_anchors() == ()
    assert panel.setting_ignore_reasons()[widget] == "read-only install command"


def test_empty_ignore_reason_raises(qapp, qtbot):
    panel = _AnchoredPanel("Test")
    qtbot.addWidget(panel)

    with pytest.raises(ValueError, match="reason"):
        panel.ignore_setting_widget(QLineEdit(), "")


def test_anchor_focus_target_can_differ_from_the_highlighted_widget(qapp, qtbot):
    panel = _AnchoredPanel("Test")
    qtbot.addWidget(panel)
    container = QWidget()
    inner = QLineEdit(container)
    panel.add_field("Deck Name", container, anchor="deck_name", anchor_focus=inner)

    anchor = panel.setting_anchors()[0]
    assert anchor.focus_widget is inner
    assert anchor.highlight_widget is container
    assert anchor.scroll_widget is container


def test_extra_anchor_text_extends_the_default_index(qapp, qtbot):
    """Composite rows can name the controls nested inside them."""
    panel = _AnchoredPanel("Test")
    qtbot.addWidget(panel)
    container = QWidget()
    nested = QCheckBox("Naver Papago (fallback)", container)
    panel.add_field("", container, anchor="reading_tts", anchor_text=lambda: (nested.text(),))

    assert "Naver Papago (fallback)" in panel.setting_anchors()[0].search_text()


def test_add_widget_anchors_only_when_asked(qapp, qtbot):
    panel = _AnchoredPanel("Test")
    qtbot.addWidget(panel)
    panel.add_widget(QLabel("status"))
    panel.decks = QLineEdit()
    panel.add_widget(panel.decks, anchor="excluded_decks")

    anchors = panel.setting_anchors()
    assert [a.stable_id for a in anchors] == ["demo.excluded_decks"]
    assert anchors[0].focus_widget is panel.decks
