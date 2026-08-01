"""A tab bar whose selection indicator slides instead of jumping.

The underline is the only moving part, and it is deliberately *behind* the
navigation rather than in front of it: the page switch happens first and the
underline catches up. That ordering is the whole design -- motion on the
critical path of a navigation command turns a 160ms flourish into 160ms of lag.

Qt draws the old static indicator from a ``border-bottom`` on
``QTabBar::tab:selected``, which cannot move. ``common.qss`` therefore turns
that border transparent for this class only (plain ``QTabBar``s elsewhere keep
it) and pushes the accent colour in through ``qproperty-accentColour``, so the
bar needs no theme lookup of its own.
"""

from __future__ import annotations

# pyqtProperty is present at runtime but missing from the PyQt6 stubs.
from PyQt6.QtCore import QRectF, Qt, pyqtProperty  # type: ignore[attr-defined]
from PyQt6.QtGui import QColor, QPainter, QPaintEvent, QPalette, QResizeEvent, QShowEvent
from PyQt6.QtWidgets import QTabBar, QTabWidget

from anki_miner.gui.resources.styles import MOTION
from anki_miner.gui.utils import motion

#: Thickness of the indicator, matching the border the stylesheet used to draw.
_UNDERLINE_HEIGHT = 3


class AnimatedTabBar(QTabBar):
    """A ``QTabBar`` that draws one underline and slides it between tabs."""

    def __init__(self, parent=None) -> None:
        # Set before super(): QTabBar's constructor and setTabBar() both run
        # layout hooks that call back into this object.
        self._underline = QRectF()
        self._accent = QColor()
        # Which tab the underline is drawn under. Deliberately NOT the same as
        # currentIndex(): Qt moves currentIndex() first and announces the
        # selection later, and the gap between those two is where a relayout
        # lands. See relayout_underline().
        self._anchor = -1
        super().__init__(parent)
        self._accent = self.palette().color(QPalette.ColorRole.Highlight)

    # ------------------------------------------------------------------
    # Animated state
    # ------------------------------------------------------------------
    def _get_underline_rect(self) -> QRectF:
        return self._underline

    def _set_underline_rect(self, rect: QRectF) -> None:
        self._underline = rect
        self.update()

    underlineRect = pyqtProperty(QRectF, fget=_get_underline_rect, fset=_set_underline_rect)  # noqa: N815 - Qt property

    def _get_accent_colour(self) -> QColor:
        return self._accent

    def _set_accent_colour(self, colour: QColor) -> None:
        self._accent = colour
        self.update()

    accentColour = pyqtProperty(QColor, fget=_get_accent_colour, fset=_set_accent_colour)  # noqa: N815 - Qt property

    # ------------------------------------------------------------------
    # Moving the underline
    # ------------------------------------------------------------------
    def underline_target(self, index: int | None = None) -> QRectF:
        """Return where the underline belongs under ``index``, empty if nowhere.

        Defaults to the selected tab. The argument exists for the one caller
        that needs a *different* tab: a relayout arriving mid-selection, where
        the underline still belongs to the tab it has not left yet.
        """
        if index is None:
            index = self.currentIndex()
        if index < 0 or index >= self.count():
            return QRectF()
        rect = self.tabRect(index)
        if rect.isEmpty():
            return QRectF()
        return QRectF(
            float(rect.x()),
            float(rect.y() + rect.height() - _UNDERLINE_HEIGHT),
            float(rect.width()),
            float(_UNDERLINE_HEIGHT),
        )

    def slide_underline(self, *_signal_args) -> None:
        """Glide to the selected tab. The page has already changed by now."""
        target = self.underline_target()
        if target.isEmpty() or self._underline.isEmpty() or not self.isVisible():
            # Nothing to travel between: sliding in from the corner of the bar
            # would be motion inventing a journey that did not happen.
            self.snap_underline()
            return
        self._anchor = self.currentIndex()
        motion.animate(
            self,
            b"underlineRect",
            target,
            duration=MOTION.navigation,
            curve=motion.spatial_curve(),
        )

    def snap_underline(self) -> None:
        """Put the underline where it belongs with no motion at all."""
        for animation in motion.active_animations(self):
            animation.stop()
        self._anchor = self.currentIndex()
        self._set_underline_rect(self.underline_target())

    def relayout_underline(self) -> None:
        """Re-place the underline after the bar's geometry moved under it.

        Selecting a tab is a geometry change here, and that is the whole trap.
        ``common.qss`` draws the selected label one weight heavier, so the
        selected tab measures wider -- which changes that tab's size hint AND
        the width of the bar as a whole. Qt therefore re-lays the bar out from
        inside ``setCurrentIndex``, and ``QTabWidget`` re-sizes it a moment
        later. Both arrive here while ``currentIndex()`` has ALREADY moved and
        ``QTabWidget.currentChanged`` has not been emitted yet, and both used to
        snap: the first put the underline on the destination before the slide
        was asked to travel there (so it travelled nowhere), and the second
        stopped the slide a few milliseconds in (so it stopped dead and the
        underline was placed at the destination anyway).

        It only bites fonts that ship separate Medium and SemiBold faces -- the
        offscreen platform's generic sans measures both the same, which is why
        the suite never saw it -- and only on the tabs whose width actually
        changes, so on a real desktop it read as the underline animating at
        random.

        Two rules, in order:

        * The underline belongs to the tab it is drawn under, not to whichever
          tab is selected this instant. If the selection has moved on ahead,
          keep the underline on its own tab at that tab's *new* width, and leave
          the destination to the slide that is about to run.
        * A geometry change must never kill a slide already in flight. Retarget
          it -- ``motion.animate`` redirects from the current rendered value --
          because stopping mid-travel is exactly the jump this class exists to
          avoid.
        """
        anchored = self.underline_target(self._anchor)
        if self._anchor != self.currentIndex() and not anchored.isEmpty():
            self._set_underline_rect(anchored)
            return
        if motion.active_animations(self) and not self.underline_target().isEmpty():
            self.slide_underline()
            return
        self.snap_underline()

    # ------------------------------------------------------------------
    # Qt hooks -- every one of these invalidates the tab geometry
    # ------------------------------------------------------------------
    def showEvent(self, event: QShowEvent | None) -> None:  # noqa: N802 - Qt override
        super().showEvent(event)
        self.snap_underline()

    def resizeEvent(self, event: QResizeEvent | None) -> None:  # noqa: N802 - Qt override
        # Not only window resizes: selecting a tab changes the bar's own width
        # by the difference between the two label weights, so QTabWidget resizes
        # it on the way through every switch. See relayout_underline().
        super().resizeEvent(event)
        self.relayout_underline()

    def tabLayoutChange(self) -> None:  # noqa: N802 - Qt override
        # Fires when tab widths change: a retranslated label, an icon, a
        # different font scale -- AND on selection, which is the part that is
        # easy to miss and was wrong here for a release.
        super().tabLayoutChange()
        self.relayout_underline()

    def tabInserted(self, index: int) -> None:  # noqa: N802 - Qt override
        super().tabInserted(index)
        self.snap_underline()

    def tabRemoved(self, index: int) -> None:  # noqa: N802 - Qt override
        super().tabRemoved(index)
        self.snap_underline()

    def paintEvent(self, event: QPaintEvent | None) -> None:  # noqa: N802 - Qt override
        super().paintEvent(event)
        if self._underline.isEmpty():
            return
        painter = QPainter(self)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._accent)
        painter.drawRect(self._underline)
        painter.end()


def install_animated_tab_bar(tabs: QTabWidget) -> AnimatedTabBar:
    """Give ``tabs`` a sliding underline. Call this before adding any tabs.

    ``QTabWidget.setTabBar()`` is documented as undefined once tabs exist, and
    the underline binds to ``QTabWidget.currentChanged`` -- which Qt emits
    *after* the page has switched, so the animation can never be in front of
    the navigation it decorates.
    """
    bar = AnimatedTabBar(tabs)
    # The tab-bar base is the one piece of tab chrome the stylesheet cannot
    # reach: Qt paints it through the platform style's ``PE_FrameTabBarBase``,
    # and ``common.qss`` only ever addresses ``QTabBar::tab`` and
    # ``QTabWidget::pane``. So it fell through to Fusion, which drew it in a
    # palette-derived near-black -- a hairline along the bar, broken under the
    # selected tab, on every tab bar in the app at once. Nothing wants it: the
    # underline below *is* the boundary between navigation and page.
    bar.setDrawBase(False)
    tabs.setTabBar(bar)
    tabs.currentChanged.connect(bar.slide_underline)
    return bar
