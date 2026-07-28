"""Theme gates: an all-theme smoke pass and a few rendered endpoints.

Deliberately *not* a 29-theme pixel matrix. Consolidation cut exact-pixel
comparisons across every shipped theme because that oracle fails on any platform
style change while saying nothing about whether a theme is usable. What survives
is the pair that actually catches regressions:

* **All-theme smoke.** Every shipped theme must parse, build a palette, compile
  a stylesheet, install both and render a composed widget without raising and
  without mutating the theme data it was loaded from. That is the gate that
  fires when a palette or QSS change breaks one theme out of 29.
* **Representative rendered endpoints.** Two radically different themes (one
  light, one dark) are rendered and read back at three points a user actually
  looks at: the page background, the accent fill of the primary action, and the
  selected row of a list. These assert the colour the theme author wrote reaches
  the screen — which a palette-role assertion cannot prove, since a role can be
  routed correctly and still be overwritten by a stylesheet rule.

**Everything here is scoped to a widget, never to ``QApplication``.** A palette
or stylesheet set on the application is process-global and sticky: Qt has no
"un-set" for an application palette, so even a faithful save/restore leaves it
*explicitly* set for every widget built afterwards. An earlier draft of this file
applied all 29 themes through ``Theme.apply_to_app`` and restored both by hand,
and it still made ``tests/unit/test_status_badge_motion.py`` fail whenever xdist
happened to schedule the two files onto one worker. ``QWidget.setPalette`` plus
``QWidget.setStyleSheet`` render identically -- verified pixel for pixel against
the application-scoped version -- and die with the widget.

``tests/unit/test_theme_palette_routes.py`` owns per-role palette routing across
all shipped themes and the ``apply_to_app`` ordering contract; this file does not
repeat either.
"""

from __future__ import annotations

import copy

import pytest
from PyQt6.QtCore import QPoint, QRect
from PyQt6.QtWidgets import QListWidget, QVBoxLayout, QWidget

from anki_miner.gui.resources.styles.theme import Theme

#: One light and one dark theme with nothing in common. Two is the point: a
#: single theme cannot distinguish "the theme reached the pixels" from "the
#: default happened to match".
ENDPOINT_THEMES = ("light", "catppuccin-mocha")


def _shipped_keys() -> list[str]:
    return sorted(Theme.get_available_themes())


def _flat_fill_point(rect: QRect) -> QPoint:
    """A point inside ``rect`` that is fill, never a glyph, under any font.

    Sampling ``rect.center()`` reads the middle of the widget's *centred label*,
    so whether the pixel is the fill or an antialiased letter stem depends on the
    advance width of that text -- which changes with the installed font. That is
    a real failure: on a runner with only DejaVu Sans, ``Run`` measures 31px
    instead of Noto's 27px and the primary button's centre lands on the ``u``,
    reading a subpixel-blended ``#7b68f1`` instead of the theme's ``#6366f1``.

    Inset from the left edge instead: text is centred, and the fill runs to the
    border (which carries the same colour on the surfaces asserted here), so this
    point is flat for every face and every border radius.
    """
    return QPoint(rect.left() + _FILL_INSET_PX, rect.center().y())


#: Past the 1px border and any radius, well short of centred text.
_FILL_INSET_PX = 8


@pytest.fixture
def themed_gallery(qapp, qtbot):
    """Yield ``build(theme_key)`` -> a shown widget wearing that theme alone."""
    from anki_miner.gui.widgets.enhanced.modern_button import ModernButton

    def build(theme_key: str) -> QWidget:
        page = QWidget()
        qtbot.addWidget(page)
        page.resize(320, 260)
        layout = QVBoxLayout(page)
        layout.addWidget(ModernButton("Run", variant="primary"))
        disabled = ModernButton("Off", variant="secondary")
        disabled.setEnabled(False)
        layout.addWidget(disabled)
        rows = QListWidget()
        rows.addItems(["one", "two"])
        rows.setCurrentRow(0)
        layout.addWidget(rows)

        # Palette first, then the sheet -- the same order apply_to_app uses, and
        # for the same reason: a stylesheet freezes a polished widget's palette.
        page.setPalette(Theme.build_palette(theme_key))
        page.setStyleSheet(Theme.get_stylesheet(theme_key))
        page.show()
        qapp.processEvents()
        return page

    yield build


class TestEveryShippedThemeRenders:
    """The smoke pass: 29 themes, no exceptions, no mutation, real pixels."""

    def test_the_shipped_set_is_not_empty(self):
        """Guards the rest of this file against silently testing nothing."""
        assert len(_shipped_keys()) >= 20

    def test_every_theme_installs_and_renders(self, themed_gallery):
        failures = []
        for key in _shipped_keys():
            try:
                image = themed_gallery(key).grab().toImage()
                if image.isNull() or image.width() <= 0:
                    failures.append(f"{key}: rendered a null image")
            except Exception as exc:  # noqa: BLE001 - the whole point is which theme
                failures.append(f"{key}: {type(exc).__name__}: {exc}")

        assert not failures, "themes that could not be installed and rendered: " + "; ".join(failures)

    def test_rendering_a_theme_does_not_mutate_its_colours(self, themed_gallery):
        """A theme dict is shipped data; a render that edits it corrupts the next."""
        before = {key: copy.deepcopy(Theme.get_colors(key)) for key in _shipped_keys()}

        for key in _shipped_keys():
            themed_gallery(key)

        assert {key: Theme.get_colors(key) for key in _shipped_keys()} == before


class TestRenderedEndpoints:
    """Three points where the theme author's colour must survive to the pixel."""

    @pytest.mark.parametrize("theme_key", ENDPOINT_THEMES)
    def test_the_page_background_is_the_theme_s_own_background(self, themed_gallery, theme_key):
        page = themed_gallery(theme_key)

        corner = page.grab().toImage().pixelColor(2, 2)

        assert corner.name().lower() == Theme.get_colors(theme_key)["background"].lower()

    @pytest.mark.parametrize("theme_key", ENDPOINT_THEMES)
    def test_the_primary_action_is_filled_with_the_theme_s_accent(self, themed_gallery, theme_key):
        page = themed_gallery(theme_key)
        button = page.layout().itemAt(0).widget()

        fill = page.grab().toImage().pixelColor(_flat_fill_point(button.geometry()))

        assert fill.name().lower() == Theme.get_colors(theme_key)["primary"].lower()

    @pytest.mark.parametrize("theme_key", ENDPOINT_THEMES)
    def test_a_selected_row_uses_the_theme_s_selection_colour(self, themed_gallery, theme_key):
        page = themed_gallery(theme_key)
        rows = page.layout().itemAt(2).widget()
        point = rows.mapTo(page, _flat_fill_point(rows.visualItemRect(rows.item(0))))

        fill = page.grab().toImage().pixelColor(point)

        assert fill.name().lower() == Theme.get_colors(theme_key)["table-selected-bg"].lower()

    def test_two_themes_do_not_render_the_same_pixels(self, themed_gallery):
        """The endpoints above would all pass on a stylesheet that ignored the theme."""
        rendered = [themed_gallery(key).grab().toImage() for key in ENDPOINT_THEMES]

        assert rendered[0] != rendered[1]


class TestThisFileLeavesTheApplicationAlone:
    """The reason this file is widget-scoped, pinned so it stays that way."""

    def test_no_application_stylesheet_is_installed(self, qapp, themed_gallery):
        before = qapp.styleSheet()

        for key in ENDPOINT_THEMES:
            themed_gallery(key)

        assert qapp.styleSheet() == before

    def test_the_application_palette_is_untouched(self, qapp, themed_gallery):
        before = qapp.palette()

        for key in ENDPOINT_THEMES:
            themed_gallery(key)

        assert qapp.palette() == before
