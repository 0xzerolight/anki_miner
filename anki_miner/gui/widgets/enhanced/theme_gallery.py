"""A scrollable grid of rendered theme cards, shared by two hosts.

Used by the UI settings panel (full set, grouped by family) and by the setup
wizard's theme step (a shortlist that expands in place). One widget, two hosts:
the wizard must not grow a second implementation that drifts from the panel.

Thumbnails load lazily. Qt only paints the cards the scroll viewport actually
shows, so the first ``paintEvent`` IS the "this card became visible" signal --
there is no viewport-intersection bookkeeping. The render is deferred out of the
paint through a zero-interval child timer, because setting a pixmap on a child
label from inside ``paintEvent`` re-enters layout and repaint.

The timer being parented to the card does NOT mean it can never fire into a
deleted C++ object -- that was tried and is false, proven by a real crash
trace. Parenting only guarantees the timer itself is destroyed together with
the card; it says nothing about ordering against the card's *sibling*
children. When a host (e.g. the setup wizard) tears down the card's widget
tree while the zero-interval timer is still pending -- a card painted once,
then its dialog closed in the same event-loop cadence before the timer got a
turn -- the ``thumbnail`` label can already be gone by the time the timer
fires, and ``_load_thumbnail`` raises ``RuntimeError: wrapped C/C++ object of
type QLabel has been deleted``. ``_load_thumbnail`` guards against exactly
that with ``widget_alive``, the same idiom used for late worker-completion
signals elsewhere in the GUI (see ``AnkiProbeController._alive``).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QCursor, QKeyEvent, QMouseEvent, QPaintEvent
from PyQt6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from anki_miner.gui.resources.styles._variables import SPACING
from anki_miner.gui.resources.styles.theme import Theme, ThemeGroupEntry
from anki_miner.gui.utils.qt_helpers import data_row_height, widget_alive
from anki_miner.utils.i18n import tr_format

from .theme_preview import DEFAULT_THUMBNAIL_SIZE, render_theme_thumbnail

logger = logging.getLogger(__name__)

#: Unicode star glyphs, routed through the font pipeline so hinting stays sharp
#: at small sizes -- no QPainter maths, no devicePixelRatio handling.
STAR_FILLED = "★"
STAR_OUTLINE = "☆"

#: Same glyph as filled, lower alpha -- reads as "some but not all favorited".
FAMILY_STAR_PARTIAL_OPACITY = 0.45

#: Cards per row. Three fits the settings panel at its default width without a
#: horizontal scrollbar and still shows two full rows above the fold.
_COLUMNS = 3


def _star_geometry(widget: QWidget) -> tuple[int, int]:
    """Return ``(box_side_px, font_px)`` for a star button anchored to ``widget``.

    Same formula the deleted tree-panel ``_apply_tree_metrics`` used, re-derived
    here from ``widget``'s own (polished, themed) font through the shared
    ``data_row_height`` rather than a flat constant -- the old flat 28px box
    clipped the glyph at ``ui_font_scale`` above ~1.4 and shrank it well below
    the tree-era 21px at 1.0.
    """
    row_height = data_row_height(widget)
    return row_height - 2, int(row_height * 0.6)


class ThemeCard(QFrame):
    """One theme: thumbnail, name, Active marker, optional favorite star."""

    clicked = pyqtSignal(str)
    star_clicked = pyqtSignal(str)

    def __init__(
        self,
        key: str,
        display_name: str,
        *,
        show_star: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._key = key
        self._selected = False
        self._focused = False
        self._thumbnail_requested = False

        self.setObjectName("themeCard")
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setAccessibleName(display_name)
        # The QTreeWidget this replaced was arrow-key navigable; a QFrame
        # defaults to NoFocus, which made every theme past the header combo's
        # favorites unreachable from the keyboard. StrongFocus + keyPressEvent
        # below restore both Tab and click reachability.
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACING.xxs, SPACING.xxs, SPACING.xxs, SPACING.xxs)
        layout.setSpacing(SPACING.xxs)

        self.thumbnail = QLabel()
        self.thumbnail.setFixedSize(DEFAULT_THUMBNAIL_SIZE)
        self.thumbnail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.thumbnail)

        caption = QHBoxLayout()
        caption.setSpacing(SPACING.xxs)

        self.name_label = QLabel(display_name)
        caption.addWidget(self.name_label, 1)

        self.active_label = QLabel("")
        self.active_label.setObjectName("caption")
        caption.addWidget(self.active_label)

        self.star: QToolButton | None = None
        if show_star:
            self.star = self._build_star()
            caption.addWidget(self.star)

        layout.addLayout(caption)

        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(0)
        self._render_timer.timeout.connect(self._load_thumbnail)

        self.set_selected(False)

    # -- rendering ------------------------------------------------------

    def paintEvent(self, event: QPaintEvent | None) -> None:  # noqa: N802 - Qt override
        if not self._thumbnail_requested:
            self._thumbnail_requested = True
            self._render_timer.start()
        super().paintEvent(event)

    def _load_thumbnail(self) -> None:
        # render_theme_thumbnail promises leniency for an unknown theme KEY
        # (falls back rather than raising), not for every failure mode -- a
        # malformed user theme JSON reaching _substitute_variables, say, can
        # still raise. This is a timer slot: an uncaught exception here
        # escapes straight into the Qt event loop, not to any caller. Catch
        # broadly and leave the card blank rather than propagate.
        try:
            pixmap = render_theme_thumbnail(self._key)
        except Exception:
            logger.warning("Could not render theme thumbnail for %r", self._key, exc_info=True)
            return

        # The zero-interval timer can outlive its sibling `thumbnail` label --
        # see the module docstring for the teardown race this guards. The
        # pixmap is rendered BEFORE the liveness check (not passed inline to
        # setPixmap): render_theme_thumbnail builds, polishes and grabs an
        # offscreen widget, which pumps Qt's event/paint queues as a side
        # effect -- a deferred-delete for this very card can land during that
        # call. Checking liveness only right before the call, with nothing
        # Qt-side between the check and the use, closes that window.
        if not widget_alive(self) or not widget_alive(self.thumbnail):
            return
        self.thumbnail.setPixmap(pixmap)

    # -- interaction ----------------------------------------------------

    def click(self) -> None:
        """Programmatic activation -- the seam tests drive instead of a click."""
        self.clicked.emit(self._key)

    def mousePressEvent(self, event: QMouseEvent | None) -> None:  # noqa: N802 - Qt override
        if event is not None and event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._key)
        super().mousePressEvent(event)

    def keyPressEvent(self, event: QKeyEvent | None) -> None:  # noqa: N802 - Qt override
        if event is not None and event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.clicked.emit(self._key)
            return
        super().keyPressEvent(event)

    def focusInEvent(self, event) -> None:  # noqa: N802 - Qt override
        self._focused = True
        self._refresh_style()
        super().focusInEvent(event)

    def focusOutEvent(self, event) -> None:  # noqa: N802 - Qt override
        self._focused = False
        self._refresh_style()
        super().focusOutEvent(event)

    # -- state ----------------------------------------------------------

    def set_selected(self, selected: bool) -> None:
        """Mark (or clear) the selection ring; repaint reflects both selection and focus."""
        self._selected = selected
        self._refresh_style()

    def _refresh_style(self) -> None:
        """Draw the selection ring and/or the keyboard-focus ring.

        The ring colour comes from the LIVE theme, not the card's own, so the
        highlight stays legible against whatever the app is currently wearing.
        Written as an instance stylesheet scoped by ``#themeCard`` so it reaches
        the frame and nothing inside it -- in particular not the thumbnail label,
        whose pixmap must never be restyled. Focus draws a dashed ring so it
        reads as distinct from the solid selection ring even when both apply.
        """
        colour = Theme.get_colors().get("primary", "#6366f1")
        if self._selected:
            border = f"2px solid {colour}"
        elif self._focused:
            border = f"2px dashed {colour}"
        else:
            border = "2px solid transparent"
        self.setStyleSheet(f"#themeCard {{ border: {border}; border-radius: 6px; }}")

    def set_active(self, active: bool) -> None:
        self.active_label.setText(self.tr("Active") if active else "")

    def set_favorite(self, is_favorite: bool) -> None:
        if self.star is None:
            return
        self.star.setChecked(is_favorite)
        self.star.setText(STAR_FILLED if is_favorite else STAR_OUTLINE)
        self.star.setAccessibleName(self.tr("Unfavorite") if is_favorite else self.tr("Favorite"))

    def _build_star(self) -> QToolButton:
        button = QToolButton(self)
        button.setObjectName("starToggle")
        button.setCheckable(True)
        button.setAutoRaise(True)
        button.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        side, font_px = _star_geometry(self)
        button.setFixedSize(side, side)
        button.setStyleSheet(f"font-size: {font_px}px;")
        button.setToolTip(self.tr("Click to add to / remove from favorites."))
        button.clicked.connect(lambda _checked=False: self.star_clicked.emit(self._key))
        return button


class ThemeGalleryWidget(QWidget):
    """Scrollable grid of :class:`ThemeCard`, grouped by theme family.

    Signals:
        theme_activated: A card was clicked. Carries the theme key. The host
            decides what "activate" means -- this widget never applies a theme.
        favorite_toggled: A card's star was clicked. Carries the theme key; the
            host reads and writes the actual favorite state.
        family_favorites_toggled: A family header's star was clicked. Carries a
            tuple of every key in that family.
    """

    theme_activated = pyqtSignal(str)
    favorite_toggled = pyqtSignal(str)
    family_favorites_toggled = pyqtSignal(tuple)

    def __init__(self, parent: QWidget | None = None, *, show_stars: bool = True) -> None:
        super().__init__(parent)
        self._show_stars = show_stars
        #: ``None`` means "every theme, grouped"; a tuple means shortlist mode.
        self._shortlist: tuple[str, ...] | None = None
        self._cards: dict[str, ThemeCard] = {}
        self._order: list[str] = []
        self._family_stars: dict[str, QToolButton] = {}
        self._family_titles: list[str] = []
        self._selected: str | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(self._scroll)

        self._content = QWidget()
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(SPACING.sm)
        self._scroll.setWidget(self._content)

        self.refresh()

    # -- public API -----------------------------------------------------

    def set_shortlist(self, keys: Sequence[str]) -> None:
        """Show only ``keys``, in the given order, with no family headers."""
        self._shortlist = tuple(keys)
        self.refresh()

    def show_all_themes(self) -> None:
        """Expand back to every discovered theme, grouped by family."""
        self._shortlist = None
        self.refresh()

    def is_showing_all(self) -> bool:
        return self._shortlist is None

    def card_keys(self) -> tuple[str, ...]:
        """Theme keys of the built cards, in display order."""
        return tuple(self._order)

    def family_titles(self) -> tuple[str, ...]:
        return tuple(self._family_titles)

    def card(self, key: str) -> ThemeCard | None:
        return self._cards.get(key)

    def star(self, key: str) -> QToolButton | None:
        card = self._cards.get(key)
        return None if card is None else card.star

    def family_star(self, family: str) -> QToolButton | None:
        return self._family_stars.get(family)

    def selected_key(self) -> str | None:
        return self._selected

    def set_active(self, key: str) -> None:
        """Move the Active marker and the selection ring without a rebuild."""
        self._selected = key
        for card_key, card in self._cards.items():
            card.set_active(card_key == key)
            card.set_selected(card_key == key)

    def refresh_favorite(self, key: str) -> None:
        """Restate one card's star and its family star, in place."""
        card = self._cards.get(key)
        if card is not None:
            card.set_favorite(Theme.is_favorite(key))
        for family, entries in Theme.get_themes_grouped():
            if family is not None and any(e.key == key for e in entries):
                self._restyle_family_star(family, entries)
                return

    def refresh(self) -> None:
        """Rebuild every card from the current Theme state."""
        self._clear()
        active = Theme.get_current_mode()
        self._selected = active

        for family, entries in self._sections():
            if family is not None:
                self._content_layout.addWidget(self._build_family_header(family, entries))
                self._family_titles.append(family)
            self._content_layout.addLayout(self._build_grid(family, entries))

        self._content_layout.addStretch(1)
        self.set_active(active)
        # refresh() destroys and recreates every card, so the proxy has to be
        # re-pointed every rebuild -- otherwise a later setFocus() on this
        # widget (e.g. the settings-search jump landing on "theme") forwards
        # into a deleted card and silently does nothing. ThemeGalleryWidget
        # itself stays a plain QWidget (NoFocus); the proxy is its only entry
        # point into the keyboard-focusable cards.
        first_card = self._cards.get(self._order[0]) if self._order else None
        self.setFocusProxy(first_card)

    # -- construction ---------------------------------------------------

    def _clear(self) -> None:
        self._cards.clear()
        self._order.clear()
        self._family_stars.clear()
        self._family_titles.clear()
        while self._content_layout.count():
            item = self._content_layout.takeAt(0)
            if item is None:
                continue
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
                continue
            child = item.layout()
            if child is not None:
                self._drop_layout(child)

    def _drop_layout(self, layout: QLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            if item is None:
                continue
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        layout.deleteLater()

    def _sections(self) -> list[tuple[str | None, list[ThemeGroupEntry]]]:
        """Display grouping.

        Shortlist mode is one header-less section in the caller's order. In full
        mode, CONSECUTIVE standalone themes merge into a single header-less
        section -- rendered one per section they would each get their own
        one-card row and shred the grid.
        """
        if self._shortlist is not None:
            available = Theme.get_available_themes()
            entries = [
                ThemeGroupEntry(key=k, variant_name=available[k], display_name=available[k])
                for k in self._shortlist
                if k in available
            ]
            return [(None, entries)]

        sections: list[tuple[str | None, list[ThemeGroupEntry]]] = []
        for family, entries in Theme.get_themes_grouped():
            if family is None and sections and sections[-1][0] is None:
                sections[-1][1].extend(entries)
            else:
                sections.append((family, list(entries)))
        return sections

    def _build_grid(self, family: str | None, entries: list[ThemeGroupEntry]) -> QGridLayout:
        grid = QGridLayout()
        grid.setSpacing(SPACING.xs)
        for index, entry in enumerate(entries):
            label = entry.variant_name if family is not None else entry.display_name
            card = ThemeCard(entry.key, label, show_star=self._show_stars, parent=self._content)
            card.clicked.connect(self._on_card_clicked)
            card.star_clicked.connect(self.favorite_toggled)
            card.set_favorite(Theme.is_favorite(entry.key))
            grid.addWidget(card, index // _COLUMNS, index % _COLUMNS)
            self._cards[entry.key] = card
            self._order.append(entry.key)
        # Trailing stretch column so a part-filled last row stays left-aligned.
        grid.setColumnStretch(_COLUMNS, 1)
        return grid

    def _build_family_header(self, family: str, entries: list[ThemeGroupEntry]) -> QWidget:
        header = QWidget(self._content)
        row = QHBoxLayout(header)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(SPACING.xxs)

        title = QLabel(family, header)
        title.setObjectName("caption")
        row.addWidget(title)

        if self._show_stars:
            button = QToolButton(header)
            button.setObjectName("starToggle")
            button.setAutoRaise(True)
            button.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            side, font_px = _star_geometry(header)
            button.setFixedSize(side, side)
            button.setStyleSheet(f"font-size: {font_px}px;")
            keys = tuple(e.key for e in entries)
            button.clicked.connect(lambda _checked=False, k=keys: self.family_favorites_toggled.emit(k))
            self._family_stars[family] = button
            row.addWidget(button)
            self._restyle_family_star(family, entries)

        row.addStretch(1)
        return header

    def _restyle_family_star(self, family: str, entries: list[ThemeGroupEntry]) -> None:
        """Tri-state family star: none / all / partial.

        0 favorited -> outline; all favorited -> filled; partial -> filled at
        reduced opacity. Click rule (owned by the host): all favorited means
        unfavorite all, otherwise favorite all.
        """
        button = self._family_stars.get(family)
        if button is None:
            return
        favorites = set(Theme.get_favorites())
        keys = [e.key for e in entries]
        n_fav = sum(1 for k in keys if k in favorites)
        n_total = len(keys)

        if n_fav == 0:
            button.setText(STAR_OUTLINE)
            button.setToolTip(tr_format(self.tr("Favorite all %1 %2 variants."), n_total, family))
            button.setGraphicsEffect(None)
        elif n_fav == n_total:
            button.setText(STAR_FILLED)
            button.setToolTip(tr_format(self.tr("Unfavorite all %1 %2 variants."), n_total, family))
            button.setGraphicsEffect(None)
        else:
            button.setText(STAR_FILLED)
            button.setToolTip(
                tr_format(
                    self.tr("%1 of %2 %3 variants favorited. Click to favorite all."),
                    n_fav,
                    n_total,
                    family,
                )
            )
            effect = QGraphicsOpacityEffect(button)
            effect.setOpacity(FAMILY_STAR_PARTIAL_OPACITY)
            button.setGraphicsEffect(effect)

    # -- interaction ----------------------------------------------------

    def _on_card_clicked(self, key: str) -> None:
        # Emit BEFORE marking active. set_active -> set_selected reads
        # Theme.get_colors()["primary"] for the ring colour; at the moment of
        # the click the live theme is still the PREVIOUS one, since this widget
        # never applies a theme itself (the host does, in its
        # theme_activated slot). Under Qt's default same-thread direct
        # connection that slot runs to completion inside emit(), so by the
        # time set_active runs below, Theme reflects the newly-activated
        # theme and the ring is drawn in the right colour. Swapping this order
        # back would draw every activation ring in the theme that was active
        # a moment ago.
        self.theme_activated.emit(key)
        self.set_active(key)
