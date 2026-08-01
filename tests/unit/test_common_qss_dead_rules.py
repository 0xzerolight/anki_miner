"""``common.qss`` may only contain declarations Qt actually renders (D45).

Eight declarations in the stylesheet asked for effects the widget style engine
ignores outright — four ``opacity``, two ``line-height``, and two
``QLineEdit::placeholder`` blocks (there is no such pseudo-element). They were
removed; the placeholder intent was re-expressed with the property Qt does
support, ``placeholder-text-color``.

Two properties the audit listed as dead are **not**: ``letter-spacing`` and
``text-transform`` both work on Qt 6.11 (each test below proves it by rendering,
so a future cleanup pass cannot delete them on a false premise).

The colour rules that give disabled buttons and inputs a real background are a
separate mechanism and must survive: they are what makes "disabled" visible now
that the inert ``opacity`` is gone.
"""

from __future__ import annotations

import re

import pytest
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QLabel, QLineEdit, QPushButton

from anki_miner.gui.resources import get_resource_dir
from anki_miner.gui.resources.styles.theme import Theme


@pytest.fixture(scope="module")
def qss() -> str:
    """``common.qss`` with comments stripped — the rules Qt actually parses.

    Comments are removed because the removals below are *documented* in
    comments naming the properties they replaced.
    """
    raw = (get_resource_dir() / "styles" / "common.qss").read_text(encoding="utf-8")
    return re.sub(r"/\*.*?\*/", "", raw, flags=re.DOTALL)


def _declarations(qss: str, prop: str) -> list[str]:
    """Every line declaring ``prop``."""
    return [line.strip() for line in qss.splitlines() if re.match(rf"^{re.escape(prop)}\s*:", line.strip())]


class TestDeadDeclarationsAreGone:
    """Qt's widget style engine renders none of these."""

    def test_no_opacity_declarations(self, qss: str):
        assert _declarations(qss, "opacity") == []

    def test_no_line_height_declarations(self, qss: str):
        assert _declarations(qss, "line-height") == []

    def test_no_placeholder_pseudo_element(self, qss: str):
        assert "::placeholder" not in qss

    def test_no_themes_panel_tree_rule(self, qss: str):
        """The theme gallery card grid replaced the tree; the objectName it
        scoped a padding override to matches nothing now."""
        assert "themesPanelTree" not in qss


class TestDeadDeclarationsReallyWereDead:
    """Proof, not folklore: these produce identical pixels with and without."""

    def _grab(self, widget, css: str):
        widget.setStyleSheet(css)
        widget.ensurePolished()
        widget.resize(200, 40)
        return widget.grab().toImage()

    def test_opacity_changes_nothing(self, qtbot):
        plain, faded = QLabel("Sample"), QLabel("Sample")
        qtbot.addWidget(plain)
        qtbot.addWidget(faded)

        assert self._grab(plain, "QLabel { color: #101010; }") == self._grab(
            faded, "QLabel { color: #101010; opacity: 0.2; }"
        )

    def test_line_height_changes_nothing(self, qtbot):
        plain, spaced = QLabel("one two three four five"), QLabel("one two three four five")
        for w in (plain, spaced):
            w.setWordWrap(True)
            w.setFixedWidth(80)
            qtbot.addWidget(w)
        spaced.setStyleSheet("QLabel { line-height: 3.0; }")
        plain.ensurePolished()
        spaced.ensurePolished()

        assert plain.heightForWidth(80) == spaced.heightForWidth(80)

    def test_placeholder_pseudo_element_changes_nothing(self, qtbot):
        plain, styled = QLineEdit(), QLineEdit()
        for w in (plain, styled):
            w.setPlaceholderText("Search")
            qtbot.addWidget(w)

        assert self._grab(plain, "QLineEdit { background: #ffffff; color: #000000; }") == self._grab(
            styled,
            "QLineEdit { background: #ffffff; color: #000000; } QLineEdit::placeholder { color: #ff0000; }",
        )


class TestSupportedPropertiesStay:
    """``letter-spacing`` and ``text-transform`` are live — do not "clean" them."""

    def test_letter_spacing_is_still_declared(self, qss: str):
        assert _declarations(qss, "letter-spacing")

    def test_letter_spacing_reaches_the_font(self, qtbot):
        label = QLabel("Sample")
        qtbot.addWidget(label)
        label.setStyleSheet("QLabel { letter-spacing: 5px; }")
        label.ensurePolished()

        assert label.font().letterSpacing() == 5.0

    def test_text_transform_is_still_declared(self, qss: str):
        assert len(_declarations(qss, "text-transform")) == 2

    def test_text_transform_actually_uppercases(self, qtbot):
        transformed, literal = QLabel("stat label"), QLabel("STAT LABEL")
        qtbot.addWidget(transformed)
        qtbot.addWidget(literal)
        transformed.setStyleSheet("QLabel { text-transform: uppercase; }")
        transformed.ensurePolished()
        literal.ensurePolished()

        assert transformed.sizeHint() == literal.sizeHint()


class TestPlaceholderIntentIsExpressedProperly:
    def test_placeholder_text_color_is_declared(self, qss: str):
        assert _declarations(qss, "placeholder-text-color")

    def test_placeholder_colour_resolves_to_the_theme_muted_text(self):
        assert f"placeholder-text-color: {Theme.get_colors('dark')['text-muted']}" in Theme.get_stylesheet("dark")

    def test_placeholder_colour_reaches_the_live_palette(self, qapp, qtbot):
        field = QLineEdit()
        field.setPlaceholderText("Search")
        qtbot.addWidget(field)
        try:
            qapp.setStyleSheet(Theme.get_stylesheet("dark"))
            field.ensurePolished()

            expected = QColor(Theme.get_colors("dark")["text-muted"])
            assert field.palette().color(QPalette.ColorRole.PlaceholderText) == expected
        finally:
            qapp.setStyleSheet("")


class TestDisabledStylingSurvives:
    """The real disabled backgrounds are not collateral of the opacity removal."""

    def test_disabled_button_keeps_its_background(self, qapp, qtbot):
        """Asserted by rendering, not by matching the rule's text: the disabled
        declaration is shared by one selector per button role now, so a string
        oracle would only be pinning today's selector list."""
        button = QPushButton("Cancel")
        button.setEnabled(False)
        qtbot.addWidget(button)
        try:
            qapp.setStyleSheet(Theme.get_stylesheet("dark"))
            button.resize(120, 32)
            button.show()

            painted = QColor.fromRgba(button.grab().toImage().pixel(60, 4))

            assert painted == QColor(Theme.get_colors("dark")["disabled"])
        finally:
            qapp.setStyleSheet("")

    def test_disabled_input_keeps_its_background(self):
        qss = Theme.get_stylesheet("dark")
        colors = Theme.get_colors("dark")

        assert f"background-color: {colors['input-disabled-bg']};" in qss
