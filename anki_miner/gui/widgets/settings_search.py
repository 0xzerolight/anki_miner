"""Field-level settings search and the jump it performs (decision D11).

Ten destinations hold roughly ninety settings. Finding one by reading panels is
the slow path; typing the words you remember is the fast one. This module turns
the anchors :mod:`anki_miner.gui.widgets.base.setting_anchor` registers into a
searchable index and renders it as a query field over a result list.

Three constraints shaped it.

* **The index is translated, and built late.** Entries hold the strings the
  anchors resolve *at build time* — after ``app.py`` installs the Qt
  translators. Building from module-level constants, or snapshotting text when a
  panel is constructed, hands non-English users an index of English.
* **Search is a jump aid, nothing more.** It never hides, reveals, expands or
  edits a setting: every setting on offer is visible on its page already,
  because the Basic/Advanced disclosure was rejected. The only thing activation
  changes is which page is shown and where focus is. The corollary, since the
  language gate started hiding rows: a setting the active language does not have
  is not offered, because search cannot reveal it and jumping to it would
  scroll, focus and flash a control the user cannot see. Hence
  :attr:`SettingSearchEntry.visible` — resolved here, once, per index build.
* **Renamed destinations keep answering to their old names.** D10 renamed
  half the destinations. Users did not rename their vocabulary, so a small table
  of previous names is folded into the matched text — see
  :data:`LEGACY_DESTINATION_TERMS`.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from PyQt6.QtCore import QEvent, QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout, QWidget

from anki_miner.gui.resources.styles import SPACING
from anki_miner.gui.widgets.base.setting_anchor import SettingAnchor
from anki_miner.gui.widgets.base.sizing import metric_row_height

#: Separator between the group and the panel in a result's breadcrumb.
BREADCRUMB_SEPARATOR = " › "

#: Dynamic property set on a control the user jumped to. Styled in
#: ``common.qss``; carried by the widget only for :data:`SEARCH_HIT_MS`.
SEARCH_HIT_PROPERTY = "settingsSearchHit"

#: How long the jumped-to control stays marked. This is a dwell, not a
#: transition, so it is deliberately not a ``MOTION`` token: those size how long
#: something takes to *move*, and at 120-220ms a mark would be gone before the
#: eye arrived. Long enough to notice, short enough to never feel like a mode.
SEARCH_HIT_MS = 900

#: Names a destination used to have, keyed by its stable page key. Every setting
#: on that page matches them, so "ASR" still reaches Transcription & Alignment
#: after the D10 rename. Deliberately untranslated, like ``capabilities.py``'s
#: keywords: this is the vocabulary users type, not text the app displays.
#: Rename a destination in ``SettingsTab._build_navigator`` -> add its old name
#: here, or search silently stops finding it under the name people know.
LEGACY_DESTINATION_TERMS: dict[str, tuple[str, ...]] = {
    "anki": ("anki",),
    "media": ("media",),
    "audio": ("audio",),
    "filtering": ("filtering",),
    "subtitles": ("subtitles", "asr"),
    "ui": ("ui",),
}

#: Result rows visible before the list scrolls.
_MAX_VISIBLE_ROWS = 6

#: Name of the per-widget timer that clears :data:`SEARCH_HIT_PROPERTY`. Found
#: back through Qt's child list, like ``utils/motion.py`` does, so a second jump
#: to the same control retimes the mark instead of stacking timers on it.
_HIT_TIMER_NAME = "am-settings-search-hit"


def normalize(text: str) -> str:
    """Fold ``text`` to the form both queries and indexed strings are matched in.

    NFKC collapses the full-width Latin a Japanese IME produces onto the ASCII
    the same user typed elsewhere, so ``ＡＳＲ`` and ``asr`` are one query.
    """
    return unicodedata.normalize("NFKC", text).casefold().strip()


@dataclass(frozen=True)
class SettingSearchSource:
    """One page's worth of anchors, with the breadcrumb they are shown under.

    ``page_key`` is the stable navigator key :meth:`SettingsTab.open_subtab`
    takes; empty means the setting is not on a page at all (the tab's own
    always-visible controls) and activating it switches no page.

    ``host`` is the surface the anchors are laid out on, and visibility is
    judged relative to it — never to the window. The index is built before
    Settings is ever shown, and only one navigator page is on the stack at a
    time, so a window-relative check calls every anchor invisible.
    """

    page_key: str
    breadcrumb: str
    anchors: tuple[SettingAnchor, ...]
    host: QWidget | None = None


@dataclass(frozen=True)
class SettingSearchEntry:
    """One searchable setting: what it is called, where it lives, how to reach it."""

    anchor: SettingAnchor
    page_key: str
    breadcrumb: str
    title: str
    #: Everything matched against, already normalized. ``haystack[0]`` is the
    #: normalized title, which is what ranking reads.
    haystack: tuple[str, ...]
    #: Whether the setting is on screen for the active language. False entries
    #: stay addressable by id — deep links resolve against the same index — but
    #: are kept out of the searchable set the query field is handed.
    visible: bool = True

    @property
    def stable_id(self) -> str:
        return self.anchor.stable_id

    def row_text(self) -> str:
        """The single line a result row shows: the setting, then where it is."""
        return f"{self.title}  —  {self.breadcrumb}"


def is_on_screen(anchor: SettingAnchor, host: QWidget | None) -> bool:
    """Whether ``anchor``'s control is on screen within ``host``.

    ``isVisibleTo`` and not ``isVisible``: this runs while Settings is still
    being constructed, so nothing has been shown yet and ``isVisible`` is False
    for every widget in the app. ``isVisibleTo(host)`` answers the question that
    is actually being asked — would this control be there if its panel were —
    and that is exactly what the language gate's ``setVisible`` drives.

    A source with no host claims nothing about layout, so its anchors are taken
    as on screen.
    """
    return True if host is None else anchor.widget.isVisibleTo(host)


def build_entries(sources: tuple[SettingSearchSource, ...]) -> tuple[SettingSearchEntry, ...]:
    """Resolve ``sources`` into index entries, reading every string right now.

    Call this after the translators are installed; call it again whenever the
    registered anchors change **or the language gate moves a row** — visibility
    is resolved here, so an index built before a switch describes the outgoing
    language. Nothing here is cached between calls.
    """
    entries: list[SettingSearchEntry] = []
    for source in sources:
        legacy = LEGACY_DESTINATION_TERMS.get(source.page_key, ())
        for anchor in source.anchors:
            texts = anchor.search_text()
            title = texts[0] if texts else anchor.stable_id
            haystack = tuple(dict.fromkeys(normalize(part) for part in (title, *texts, source.breadcrumb, *legacy)))
            entries.append(
                SettingSearchEntry(
                    anchor=anchor,
                    page_key=source.page_key,
                    breadcrumb=source.breadcrumb,
                    title=title,
                    haystack=haystack,
                    visible=is_on_screen(anchor, source.host),
                )
            )
    return tuple(entries)


def _rank(entry: SettingSearchEntry, query: str, tokens: tuple[str, ...]) -> int | None:
    """Score ``entry`` against a normalized query, or ``None`` if it misses.

    Every token must appear somewhere, so extra words narrow rather than widen.
    Ranking then looks only at the title: a setting whose *name* you typed
    outranks one that merely mentions your words in its helper text.
    """
    if not all(any(token in field for field in entry.haystack) for token in tokens):
        return None
    title = entry.haystack[0]
    if title == query:
        return 3
    if title.startswith(query):
        return 2
    if query in title:
        return 1
    return 0


def search(entries: tuple[SettingSearchEntry, ...], query: str) -> tuple[SettingSearchEntry, ...]:
    """Return the entries matching ``query``, best first.

    A blank query matches nothing: with no question asked there is no answer to
    show, and listing all ninety settings would only bury the navigator.
    """
    normalized = normalize(query)
    tokens = tuple(normalized.split())
    if not tokens:
        return ()
    scored = [
        (rank, index, entry)
        for index, entry in enumerate(entries)
        if (rank := _rank(entry, normalized, tokens)) is not None
    ]
    scored.sort(key=lambda row: (-row[0], row[1]))
    return tuple(entry for _rank_, _index, entry in scored)


def _set_search_hit(widget: QWidget, on: bool) -> None:
    """Set or clear the transient search-hit mark and repaint the widget."""
    widget.setProperty(SEARCH_HIT_PROPERTY, on)
    if style := widget.style():
        style.unpolish(widget)
        style.polish(widget)


def flash_search_hit(widget: QWidget, *, duration_ms: int = SEARCH_HIT_MS) -> QTimer:
    """Mark ``widget`` as the control the user jumped to, briefly.

    A dynamic property plus a repolish, not an animation: this is a state the
    widget is in for a moment, and painting it is the stylesheet's job. The
    clearing timer is parented to the widget, so a jumped-to control that is
    destroyed takes its pending clear with it.
    """
    timer = widget.findChild(QTimer, _HIT_TIMER_NAME, Qt.FindChildOption.FindDirectChildrenOnly)
    if timer is None:
        timer = QTimer(widget)
        timer.setObjectName(_HIT_TIMER_NAME)
        timer.setSingleShot(True)
        timer.timeout.connect(lambda: _set_search_hit(widget, False))
    _set_search_hit(widget, True)
    timer.start(max(0, duration_ms))
    return timer


class SettingsSearchBox(QWidget):
    """A query field over a result list; emits the id of the chosen setting.

    Owns no settings and reaches into none: it knows the index it was handed and
    emits a stable id. Performing the jump belongs to the surface that owns the
    navigator.
    """

    #: Stable anchor id of the setting the user chose.
    setting_activated = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._entries: tuple[SettingSearchEntry, ...] = ()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACING.xxs)

        self.input = QLineEdit()
        self.input.setObjectName("settings-search-input")
        self.input.setPlaceholderText(self.tr("Search settings"))
        self.input.setClearButtonEnabled(True)
        self.input.textChanged.connect(self._on_query_changed)
        self.input.returnPressed.connect(self._activate_current)
        # Up/Down belong to the result list while the caret is still in the
        # field, so choosing a result never costs a Tab press.
        self.input.installEventFilter(self)
        layout.addWidget(self.input)

        self.results = QListWidget()
        self.results.setObjectName("settings-search-results")
        self.results.setUniformItemSizes(True)
        # itemActivated only, deliberately: it is the platform's own notion of
        # "chosen" (double-click, or single-click where the desktop says so).
        # Also connecting itemClicked would run the handler again for the click
        # inside a double-click -- with a QListWidgetItem the first call already
        # deleted.
        self.results.itemActivated.connect(self._activate_item)
        self.results.setVisible(False)
        layout.addWidget(self.results)

    def set_entries(self, entries: tuple[SettingSearchEntry, ...]) -> None:
        """Replace the index this box searches and re-run the current query."""
        self._entries = entries
        self._on_query_changed(self.input.text())

    def clear(self) -> None:
        """Empty the query, which puts the result list away."""
        self.input.clear()

    # ------------------------------------------------------------------
    # Query -> rows
    # ------------------------------------------------------------------

    def _on_query_changed(self, text: str) -> None:
        matches = search(self._entries, text)
        self.results.clear()
        if not normalize(text):
            self.results.setVisible(False)
            return
        if not matches:
            empty = QListWidgetItem(self.tr("No matching settings."))
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            self.results.addItem(empty)
        for entry in matches:
            item = QListWidgetItem(entry.row_text())
            item.setData(Qt.ItemDataRole.UserRole, entry.stable_id)
            item.setToolTip(entry.row_text())
            self.results.addItem(item)
        row_height = metric_row_height(self.results)
        self.results.setMaximumHeight(row_height * _MAX_VISIBLE_ROWS + 2 * SPACING.xxs)
        self.results.setVisible(True)
        if matches:
            self.results.setCurrentRow(0)

    # ------------------------------------------------------------------
    # Activation
    # ------------------------------------------------------------------

    def _activate_item(self, item: QListWidgetItem | None) -> None:
        stable_id = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if not stable_id:
            return
        # Clear first: the query field is about to lose focus to the control the
        # user asked for, and a stale result list floating over the page it just
        # navigated to is the one thing this surface must not leave behind.
        self.clear()
        self.setting_activated.emit(stable_id)

    def _activate_current(self) -> None:
        self._activate_item(self.results.currentItem())

    def eventFilter(self, a0: QObject | None, a1: QEvent | None) -> bool:  # noqa: N802 - Qt override
        """Steer Up/Down from the query field into the result list.

        Picking a result must not cost a Tab press: the caret stays where the
        user is typing and the arrows drive the list underneath it.
        """
        if a0 is self.input and isinstance(a1, QKeyEvent) and a1.type() == QEvent.Type.KeyPress:
            step = {Qt.Key.Key_Down: 1, Qt.Key.Key_Up: -1}.get(Qt.Key(a1.key()))
            if step is not None and self.results.count():
                row = self.results.currentRow() + step
                if 0 <= row < self.results.count():
                    self.results.setCurrentRow(row)
                return True
        return super().eventFilter(a0, a1)
