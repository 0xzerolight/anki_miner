"""Dialog for curating words before card creation."""

from __future__ import annotations

import html
import logging
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from anki_miner.gui.widgets.subtitle_player_widget import SubtitlePlayerWidget
    from anki_miner.models.reading import ImageRef, ReadingUnit

from PyQt6.QtCore import QPoint, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QImage, QKeySequence, QPixmap, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QSplitter,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from anki_miner.gui.resources.styles import SPACING
from anki_miner.gui.utils.fonts import (
    JAPANESE_BODY,
    JAPANESE_FEATURE,
    apply_japanese_font,
    japanese_cell_font,
    make_scaled_font,
)
from anki_miner.gui.utils.keyboard_shortcuts import disown_default_buttons, primary_action_shortcut
from anki_miner.gui.utils.qt_helpers import (
    CELL_PADDING,
    CellRole,
    add_min_max_buttons,
    configure_data_view,
    install_copy_rows,
    make_table_item,
)
from anki_miner.gui.utils.run_off_thread import join_tracked_workers, run_off_thread
from anki_miner.gui.widgets.base.sizing import metric_row_height
from anki_miner.gui.widgets.enhanced import ModernButton
from anki_miner.gui.widgets.page_image_view import PageImageView, load_page_qimage
from anki_miner.models import TokenizedWord
from anki_miner.utils.i18n import tr_format

logger = logging.getLogger(__name__)

# Decoded-page LRU cap. A full-res manga page is ~13-22 MB as RGBA, so cap 4
# bounds the cache at ~90 MB worst case — a deliberate memory/simplicity
# tradeoff (vs. downscale-on-load + box rescale); consecutive words usually
# share a page, so 4 pages of backtrack covers real navigation.
_PAGE_CACHE_CAP = 4
_PAGE_CACHE_MAX_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class CurationMediaContext:
    """Media context for the word curation dialog's preview panes.

    Video mining: carries the video source and pre-parsed subtitle entries so
    the dialog can seek to the correct frame when the user focuses a word row.

    Manga mining: carries ``page_units`` — the reading document's units keyed
    by ``unit.index`` (== ``int(word.start_time)``) — so the dialog can show
    the focused word's page image with its mokuro block highlighted.
    """

    video_file: Path | None
    subtitle_entries: list[tuple[float, float, str]]  # parsed, offset-zeroed
    offset: float = 0.0
    audio_track_override: int | None = None
    page_units: Mapping[int, ReadingUnit] | None = None  # manga: unit.index -> ReadingUnit


class WordCurationDialog(QDialog):
    """Dialog for selecting which words to include in card creation.

    Shows a table of words with checkboxes. Users search/filter, include or
    exclude in bulk, and confirm. It is a primary interactive surface, not a
    confirmation step: the app automates the mining mechanics, and a user
    frequently picks the words by hand, so every bulk verb names and counts its
    own target, a counter states position/included/shown, and a detail strip
    restates the focused row.

    When ``media_context`` is supplied and its video file exists, an embedded
    ``SubtitlePlayerWidget`` is shown in the right pane so the user can preview
    the scene for each word. When it carries ``page_units`` (manga mining), a
    ``PageImageView`` shows the focused word's manga page with its mokuro
    block highlighted. When ``lookup_fn`` is supplied, a ``QTextBrowser``
    below shows offline dictionary entries for the focused word.
    All panes are optional and backward-compatible; existing callers that pass
    only ``words`` receive the same pure-table behaviour as before.
    """

    def __init__(
        self,
        words: list[TokenizedWord],
        parent=None,
        *,
        mark_known_callback: Callable[[set[str]], int] | None = None,
        media_context: CurationMediaContext | None = None,
        lookup_fn: Callable[[str], list[tuple[str, str]]] | None = None,
    ):
        super().__init__(parent)
        self._words = words
        # Callback invoked with the set of mined forms when the user adds rows to
        # the local known/ignore list (Issue #42). Persisted immediately so the
        # words stick even if the dialog is later cancelled.
        self._mark_known_callback = mark_known_callback
        self._media_context = media_context
        self._lookup_fn = lookup_fn

        # Determine whether each optional pane should be shown.
        ctx = media_context
        self._show_player = ctx is not None and ctx.video_file is not None and ctx.video_file.exists()
        self._show_dict = lookup_fn is not None
        # Manga page pane: gated on page_units exactly like the player gates
        # on video_file. Cache holds decoded QImages (GUI-thread only);
        # _page_request_gen is the stale-guard for off-thread loads and
        # _closing blocks any dispatch once teardown has run (see _stop_player).
        self._page_units = ctx.page_units if ctx is not None else None
        self._show_image = bool(self._page_units)
        self._page_cache: OrderedDict[ImageRef, QImage] = OrderedDict()
        self._page_request_gen = 0
        self._closing = False
        # Sentence picker: shown when at least one word has alternative example
        # sentences (it appears on >= 2 subtitle lines). The chosen variant per
        # word index lives in self._chosen; get_selected_words falls back to the
        # original word when the user never picks an alternative.
        self._has_candidates = any(len(w.sentence_candidates) > 1 for w in words)
        self._chosen: dict[int, TokenizedWord] = {}
        # Context for the candidate list while a row is focused: the focused
        # word's index + its candidate variants. Guards programmatic
        # repopulation from being mistaken for a user pick.
        self._candidate_list_index: int | None = None
        self._candidate_list_words: list[TokenizedWord] = []
        self._populating_candidates = False

        # Lookup result cache keyed by term (empty results are cached too).
        # The fetch itself runs off the GUI thread: at most one request is in
        # flight, the newest queued request replaces any older one, and every
        # callback is checked against _lookup_gen so a fast scroll can never
        # paint an entry the user has already scrolled past.
        self._lookup_cache: dict[str, list[tuple[str, str]]] = {}
        self._lookup_gen = 0
        self._lookup_inflight = False
        self._pending_lookup: tuple[str, str | None] | None = None

        # Debounce timer for row-focus changes (avoid hammering lookup on arrow-key scroll).
        self._focus_timer = QTimer(self)
        self._focus_timer.setSingleShot(True)
        self._focus_timer.setInterval(120)
        self._focus_timer.timeout.connect(self._on_focus_timer_fired)
        self._pending_word: TokenizedWord | None = None
        self._pending_index: int | None = None

        # Debounce search keystrokes so a fast typist doesn't run setRowHidden
        # N times for N characters typed.  150 ms keeps typing latency invisible.
        self._search_debounce_timer = QTimer(self)
        self._search_debounce_timer.setSingleShot(True)
        self._search_debounce_timer.setInterval(150)
        self._search_debounce_timer.timeout.connect(self._apply_search)

        self._setup_ui()
        self._populate_table()
        self._refresh_summary()
        # Connected FIRST, deliberately: MiningTabBase connects its curation
        # resolver to the same signal afterwards, and Qt runs direct connections
        # in connection order, so the mpv core / page decode / dictionary workers
        # are always released before the tab reads the selection and schedules
        # this window for deletion. Do not reorder these two connections.
        self.finished.connect(self._stop_player)
        add_min_max_buttons(self)
        self._configure_as_owned_window()

    def _configure_as_owned_window(self) -> None:
        """Present the curator as a non-modal window owned by its tab (D33).

        Word curation is a primary interactive surface, not a confirmation step:
        the mining item waits for the user's decision, but the rest of Anki
        Miner must stay usable while they read, search and preview. A parented
        ``QDialog`` is already a non-modal top-level window; these two calls
        state that contract explicitly so a later ``setModal(True)`` cannot
        quietly take it away.

        The remaining half is the caller's: ``MiningTabBase`` shows this window
        with ``show()``. ``exec()`` would force application modality back on
        regardless of anything set here.
        """
        self.setWindowFlag(Qt.WindowType.Window, True)
        self.setWindowModality(Qt.WindowModality.NonModal)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        self.setWindowTitle(self.tr("Word Curation"))
        self.setMinimumWidth(900)
        self.setMinimumHeight(600)
        if self._show_player or self._show_image or self._show_dict or self._has_candidates:
            self.resize(1500, 760)
        else:
            self.resize(1100, 700)

        layout = QVBoxLayout()
        layout.setSpacing(SPACING.sm)
        layout.setContentsMargins(SPACING.lg, SPACING.lg, SPACING.lg, SPACING.lg)

        # Header (outside the splitter — always visible)
        header = QLabel(self.tr("Select words for card creation"))
        header.setFont(self._make_font(16, QFont.Weight.Bold))
        layout.addWidget(header)

        # Build the left pane (controls + table)
        left_pane = self._build_left_pane()

        if self._show_player or self._show_image or self._show_dict or self._has_candidates:
            # Horizontal splitter: left = word table, right = player/page + sentences + dict
            h_splitter = QSplitter(Qt.Orientation.Horizontal)
            h_splitter.addWidget(left_pane)

            right_pane = self._build_right_pane()
            h_splitter.addWidget(right_pane)

            # Give the left pane slightly more space initially
            h_splitter.setSizes([700, 800])
            layout.addWidget(h_splitter, 1)
        else:
            layout.addWidget(left_pane, 1)

        # Footer buttons (outside the splitter — always visible)
        footer_layout = QHBoxLayout()
        footer_layout.addStretch()

        cancel_button = ModernButton(self.tr("Cancel"), variant="secondary")
        cancel_button.clicked.connect(self.reject)
        cancel_button.setMinimumWidth(100)
        footer_layout.addWidget(cancel_button)

        confirm_button = ModernButton(self.tr("Confirm Selection"), variant="primary")
        confirm_button.clicked.connect(self.accept)
        confirm_button.setMinimumWidth(140)
        footer_layout.addWidget(confirm_button)

        layout.addLayout(footer_layout)
        self.setLayout(layout)
        self._disown_default_button()
        self._setup_shortcuts()

    def _disown_default_button(self) -> None:
        """Leave this dialog with no default button at all.

        Delegates to the shared D49 primitive, which also re-strips the flags on
        every show — Qt re-promotes a default button from its own show handlers.
        Confirmation is Ctrl+Return instead (see :meth:`_setup_shortcuts`); every
        button here stays reachable by mouse and by Space.
        """
        disown_default_buttons(self)

    def _build_left_pane(self) -> QWidget:
        """Build the left pane containing the search bar, bulk-action buttons, and table."""
        container = QWidget()
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(SPACING.sm)

        # Controls row
        controls_layout = QHBoxLayout()
        controls_layout.setSpacing(SPACING.sm)

        search_label = QLabel(self.tr("Search:"))
        controls_layout.addWidget(search_label)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText(self.tr("Filter by any field..."))
        self.search_input.textChanged.connect(self._on_search_changed)
        self.search_input.setMinimumWidth(200)
        controls_layout.addWidget(self.search_input)

        controls_layout.addSpacing(16)

        # Three bulk verbs, each with ONE fixed target named in its own label and
        # counted live by _refresh_bulk_labels. Nothing here changes meaning with
        # the selection, so no tooltip is needed to disambiguate — and the
        # "Exclude highlighted" verb is the S key, which is on the hint line.
        self.select_all_button = ModernButton(variant="secondary")
        self.select_all_button.clicked.connect(self._select_all)
        controls_layout.addWidget(self.select_all_button)

        self.deselect_all_button = ModernButton(variant="secondary")
        self.deselect_all_button.clicked.connect(self._deselect_all)
        controls_layout.addWidget(self.deselect_all_button)

        self.include_highlighted_button = ModernButton(variant="secondary")
        self.include_highlighted_button.clicked.connect(self._include_highlighted)
        controls_layout.addWidget(self.include_highlighted_button)

        # Add to local known/ignore list (Issue #42). Acts on the highlighted
        # rows, or the current row when nothing is highlighted — deliberately NOT
        # all visible rows, to avoid ignoring the whole list by accident.
        self.add_known_button = ModernButton(self.tr("Add to Known Words"), variant="secondary")
        self.add_known_button.clicked.connect(self._on_add_to_known)
        self.add_known_button.setToolTip(self.tr("Add highlighted rows to your Known Words list — never mined again."))
        controls_layout.addWidget(self.add_known_button)

        controls_layout.addStretch()

        self.word_count_label = QLabel()
        self.word_count_label.setFont(self._make_font(12, QFont.Weight.Medium))
        controls_layout.addWidget(self.word_count_label)

        vbox.addLayout(controls_layout)

        # Table
        self.table = QTableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels(
            [
                "",
                self.tr("Word (mined)"),
                self.tr("Form in subtitle"),
                self.tr("Reading"),
                self.tr("Sentence"),
                self.tr("Freq. Rank"),
                self.tr("Occurrences"),
            ]
        )
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.table.setSortingEnabled(True)

        header_view = self.table.horizontalHeader()
        if header_view:
            header_view.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
            # The checkbox column holds one indicator and its cell padding, so
            # it is measured from the style rather than pinned at 40px -- the
            # indicator grows with the platform and with the text scale.
            style = self.table.style()
            indicator = style.pixelMetric(QStyle.PixelMetric.PM_IndicatorWidth) if style is not None else 0
            header_view.resizeSection(0, indicator + 2 * CELL_PADDING)
            for column in (1, 2, 3, 5, 6):  # mined form, surface, reading, rank, count
                header_view.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
            header_view.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)  # sentence

        self._apply_data_surface()
        install_copy_rows(self.table)

        self.table.itemChanged.connect(self._on_item_changed)

        # Row-focus wiring — independent of checkbox state. Always connected: the
        # detail panel and the target/position summary exist even on a plain
        # table-only dialog, and _on_row_focus_changed is what keeps both truthful.
        #
        # BOTH signals are needed, and neither implies the other:
        #   * currentCellChanged is the cursor. It fires even when the selection
        #     does not change — including when the cursor is cleared while a
        #     modifier is held, because Qt derives the selection command from
        #     QGuiApplication::keyboardModifiers().
        #   * itemSelectionChanged is the highlight, which the "Include
        #     highlighted (N)" count reads and which can change without the
        #     cursor moving (Ctrl+Click).
        self.table.currentCellChanged.connect(lambda *_: self._on_row_focus_changed())
        self.table.itemSelectionChanged.connect(self._on_row_focus_changed)
        if header_view:
            # Sorting relocates the focused word without changing the selection,
            # so itemSelectionChanged alone would leave a stale "Word N of M".
            header_view.sortIndicatorChanged.connect(lambda *_: self._refresh_summary())

        # Right-click context menu (always present; useful for #43)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_table_context_menu)

        vbox.addWidget(self.table)
        vbox.addWidget(self._build_detail_panel())
        vbox.addWidget(self._build_key_hints())
        return container

    def _build_detail_panel(self) -> QFrame:
        """Build the always-visible detail strip for the focused row.

        Restates the focused word's card front, its kana reading and the full
        sentence, so a keyboard user reading down the table never has to hover a
        truncated cell. Plain text throughout (decision D45-B): no furigana, and
        no chance of a sentence's own characters being interpreted as markup.

        The three lines stack: the expression large, its kana reading *beneath*
        it, then the sentence. Beneath, not above — the reading above the kanji
        is ruby, which is decision D45-C and was declined. All three are
        Japanese content rather than interface chrome, so they take the Japanese
        face at content sizes; the word table above keeps its own density.

        Object names are the contract the stylesheet styles against; the
        sentence strip reserves exactly two lines so the panel's height never
        moves as the cursor travels.
        """
        self.detail_panel = QFrame()
        self.detail_panel.setObjectName("curator-detail")
        vbox = QVBoxLayout(self.detail_panel)
        vbox.setContentsMargins(SPACING.sm, SPACING.xs, SPACING.sm, SPACING.xs)
        vbox.setSpacing(SPACING.xxs)

        self.detail_expression = QLabel()
        self.detail_expression.setObjectName("curator-detail-expression")
        # Size here, weight in the stylesheet: a QSS `font-weight` on QWidget
        # overrides setFont, so a Python-set bold never actually rendered.
        apply_japanese_font(self.detail_expression, role=JAPANESE_FEATURE)
        vbox.addWidget(self.detail_expression)

        self.detail_reading = QLabel()
        self.detail_reading.setObjectName("curator-detail-reading")
        apply_japanese_font(self.detail_reading, role=JAPANESE_BODY)
        vbox.addWidget(self.detail_reading)

        self.detail_sentence = QLabel()
        self.detail_sentence.setObjectName("curator-detail-sentence")
        self.detail_sentence.setWordWrap(True)
        self.detail_sentence.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        apply_japanese_font(self.detail_sentence, role=JAPANESE_BODY)
        # Reserved before the font is polished, which is why the Japanese font
        # is set from Python as well as named in the stylesheet.
        two_lines = 2 * metric_row_height(self.detail_sentence, vertical_padding=0)
        self.detail_sentence.setMinimumHeight(two_lines)
        self.detail_sentence.setMaximumHeight(two_lines)
        vbox.addWidget(self.detail_sentence)

        for label in (self.detail_expression, self.detail_reading, self.detail_sentence):
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        return self.detail_panel

    def _build_key_hints(self) -> QLabel:
        """One quiet line naming the keys this screen answers to."""
        if self._show_player:
            text = self.tr(
                "S include/exclude · Space play/pause · Ctrl+A include · Ctrl+D exclude · Ctrl+Enter confirm"
            )
        else:
            text = self.tr("S include/exclude · Ctrl+A include · Ctrl+D exclude · Ctrl+Enter confirm")
        self.key_hint_label = QLabel(text)
        self.key_hint_label.setObjectName("curator-key-hints")
        self.key_hint_label.setFont(self._make_font(11))
        return self.key_hint_label

    def _build_right_pane(self) -> QWidget:
        """Build the right pane from whichever optional sub-panes are enabled.

        Stacks (top→bottom) the player, the sentence picker, and the definition
        browser — only the enabled ones. Returns a vertical ``QSplitter`` when
        two or more are enabled, otherwise the single enabled widget. Called
        only when at least one sub-pane is enabled.
        """
        panes: list[tuple[QWidget, int]] = []  # (widget, initial splitter size)

        if self._show_player:
            self.player_widget = self._create_player_widget()
            panes.append((self.player_widget, 480))

        if self._show_image:
            # Mutually exclusive with the player in practice (manga has no
            # video), but the panes-list pattern composes either way.
            self.page_image_view = PageImageView()
            panes.append((self.page_image_view, 480))

        if self._has_candidates:
            panes.append((self._build_sentence_pane(), 240))

        if self._show_dict:
            self.definition_view = QTextBrowser()
            self.definition_view.setReadOnly(True)
            self.definition_view.setOpenExternalLinks(False)
            panes.append((self.definition_view, 280))

        if len(panes) == 1:
            return panes[0][0]

        v_splitter = QSplitter(Qt.Orientation.Vertical)
        for widget, _ in panes:
            v_splitter.addWidget(widget)
        v_splitter.setSizes([size for _, size in panes])
        return v_splitter

    def _build_sentence_pane(self) -> QWidget:
        """Build the "Sentences" picker pane (label + candidate list).

        The list is repopulated on row focus with the focused word's candidate
        sentences; selecting one rewrites which sentence/scene gets mined.
        """
        container = QWidget()
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(SPACING.xs)

        label = QLabel(self.tr("Sentences"))
        label.setFont(self._make_font(12, QFont.Weight.Medium))
        vbox.addWidget(label)

        self.sentence_list = QListWidget()
        self.sentence_list.setWordWrap(True)
        self.sentence_list.setToolTip(
            self.tr("Pick which sentence (and scene) gets mined for this word. Only shown when the word repeats.")
        )
        self.sentence_list.currentRowChanged.connect(self._on_candidate_chosen)
        vbox.addWidget(self.sentence_list, 1)
        return container

    def _create_player_widget(self) -> SubtitlePlayerWidget:
        """Instantiate and configure the SubtitlePlayerWidget."""
        # Import here to keep the module importable in headless test environments
        # where the player backend may need patching.
        from anki_miner.gui.widgets.subtitle_player_widget import SubtitlePlayerWidget

        widget = SubtitlePlayerWidget(self)
        ctx = self._media_context
        assert ctx is not None  # guarded by self._show_player
        # Offset is passed to set_source for subtitle overlay alignment only.
        # Seek calls use raw word.start_time (video timeline); see _on_focus_timer_fired.
        widget.set_source(
            ctx.video_file,  # type: ignore[arg-type]  # existence checked in _setup_ui
            ctx.subtitle_entries,
            ctx.offset,
            audio_track_override=ctx.audio_track_override,
        )
        return widget

    # ------------------------------------------------------------------
    # Keyboard shortcuts
    # ------------------------------------------------------------------

    def _install_play_pause_shortcut(self, widget: QWidget) -> None:
        """Install a widget-scoped Space play/pause shortcut on ``widget``.

        ``WidgetWithChildrenShortcut`` so it only fires when ``widget`` (or one of
        its children) has focus — never the Search box. Installed on every pane the
        user clicks into to preview a scene (the table plus the right-pane player,
        sentence picker, and dictionary), so Space keeps reaching the player after
        focus leaves the table. A window-scoped shortcut can't be used: it would
        swallow spaces typed in the Search box (Issue #55).
        """
        shortcut = QShortcut(QKeySequence(Qt.Key.Key_Space), widget)
        shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        shortcut.activated.connect(self._toggle_play_pause)

    def _setup_shortcuts(self) -> None:
        """Set up keyboard shortcuts for word curation."""
        # Space: Play/pause the player (Issue #55). Scoped per widget (not the
        # window) so it doesn't swallow spaces typed in the Search box. Installed
        # on the table and on each interactive preview pane — focus leaves the
        # table the moment a sentence/scene is clicked, so the table alone isn't
        # enough.
        self._install_play_pause_shortcut(self.table)
        if self._show_player and hasattr(self, "player_widget"):
            self._install_play_pause_shortcut(self.player_widget)
        if self._has_candidates and hasattr(self, "sentence_list"):
            self._install_play_pause_shortcut(self.sentence_list)
        if self._show_dict and hasattr(self, "definition_view"):
            self._install_play_pause_shortcut(self.definition_view)

        # S: Toggle checkbox of selected rows (or current row if none selected).
        # Relocated off Space, which is now play/pause (Issue #55).
        toggle_shortcut = QShortcut(QKeySequence(Qt.Key.Key_S), self.table)
        toggle_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        toggle_shortcut.activated.connect(self._toggle_selected_rows)

        # Ctrl+A: include every visible word — the same verb as the "Include
        # visible" button, so the two can never disagree. (Scoped to the table so
        # it doesn't override text selection in Search.)
        select_all_shortcut = QShortcut(QKeySequence("Ctrl+A"), self.table)
        select_all_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        select_all_shortcut.activated.connect(self._select_all)

        # Ctrl+D: exclude every visible word (scoped to table)
        deselect_all_shortcut = QShortcut(QKeySequence("Ctrl+D"), self.table)
        deselect_all_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        deselect_all_shortcut.activated.connect(self._deselect_all)

        # Ctrl+Return (and the keypad's Ctrl+Enter): confirm the selection.
        # A bare Return can NOT be used here: this dialog owns a Search field, and
        # a Japanese input method commits a composition with Return — the old
        # window-scoped Return shortcut turned "accept this kana" into "accept the
        # entire review". Scoped to the dialog so it also works from Search.
        primary_action_shortcut(self, self.accept)

    def _toggle_play_pause(self) -> None:
        """Space: toggle player play/pause (no-op when the player pane is hidden)."""
        if self._show_player and hasattr(self, "player_widget"):
            self.player_widget.toggle_play_pause()

    # ------------------------------------------------------------------
    # Table helpers
    # ------------------------------------------------------------------

    def _make_font(self, size: int, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
        # Thin wrapper over the shared scale-aware helper so this dialog's
        # header/count labels track the global UI font scale. Computed at
        # construction; the dialog is modal and recreated each open, so it
        # picks up the current scale on next open (no live re-scaling needed).
        return make_scaled_font(size, weight)

    def _apply_data_surface(self) -> None:
        """(Re-)apply the shared data-surface configuration to the word table.

        Called a second time after each populate because re-enabling sorting
        resets the vertical header's resize mode to Interactive, which drops the
        shared Fixed row height.
        """
        configure_data_view(self.table)

    def _populate_table(self) -> None:
        """Fill the table with words, all checked by default."""
        self.table.setSortingEnabled(False)
        self.table.blockSignals(True)
        self.table.setRowCount(len(self._words))

        for row, word in enumerate(self._words):
            # Checkbox column
            check_item = make_table_item("", CellRole.STATE)
            check_item.setFlags(
                Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
            )
            check_item.setCheckState(Qt.CheckState.Checked)
            check_item.setData(Qt.ItemDataRole.UserRole, row)  # Store original index
            self.table.setItem(row, 0, check_item)

            # Word (mined) — what becomes the Anki Expression
            # (source-orthography dictionary form for verbs/adjectives,
            # surface for nouns)
            self.table.setItem(row, 1, self._make_readonly_item(word.mined_form, japanese=True))

            # Form in subtitle — the raw surface as it appeared
            self.table.setItem(row, 2, self._make_readonly_item(word.surface, japanese=True))

            # Reading
            self.table.setItem(row, 3, self._make_readonly_item(word.reading, japanese=True))

            # Sentence, truncated for the cell but copied and hovered in full.
            # A trailing "(N)" flags words with N alternative example sentences.
            n_candidates = len(word.sentence_candidates)
            self.table.setItem(
                row,
                4,
                self._make_readonly_item(
                    self._sentence_display(word.sentence, n_candidates),
                    tooltip=self._sentence_tooltip(word.sentence, n_candidates),
                    copy_text=word.sentence,
                    japanese=True,
                ),
            )

            # Frequency Rank — sort numerically, not lexically (issue #6).
            # An unranked word carries inf so it stays last ascending.
            rank = word.frequency_rank
            self.table.setItem(
                row,
                5,
                self._make_readonly_item(
                    "-" if rank is None else str(rank),
                    role=CellRole.NUMBER,
                    sort_value=float("inf") if rank is None else float(rank),
                ),
            )

            # Occurrences — times the word appears in this episode; sort
            # numerically so 15 ranks above 2 (Issue #88).
            occ = word.occurrence_count
            self.table.setItem(
                row,
                6,
                self._make_readonly_item(str(occ), role=CellRole.NUMBER, sort_value=float(occ)),
            )

        self.table.blockSignals(False)
        self.table.setSortingEnabled(True)

        # Re-apply AFTER sorting is re-enabled: re-enabling sorting resets the
        # vertical-header resize mode to Interactive, which drops the shared
        # Fixed row height. Re-applying here keeps it in effect.
        self._apply_data_surface()

    def _make_readonly_item(
        self,
        text: str,
        *,
        role: CellRole = CellRole.TEXT,
        sort_value: float | str | None = None,
        tooltip: str | None = None,
        copy_text: str | None = None,
        japanese: bool = False,
    ) -> QTableWidgetItem:
        """Build a non-editable cell on the shared data-surface contract.

        ``japanese`` gives the cell the Japanese face and nothing else: an item
        font carrying no size resolves against the view's own, so kanji take
        Japanese rather than Chinese glyph shapes while the row stays exactly as
        tall as the shared data-surface rule made it. Larger Japanese content
        sizes belong in the detail panel below, never in the rows — the density
        is what makes this table scannable.
        """
        item = make_table_item(text, role, sort_value=sort_value, copy_text=copy_text, tooltip=tooltip)
        item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        if japanese:
            item.setFont(japanese_cell_font())
        return item

    @staticmethod
    def _sentence_display(sentence: str, n_candidates: int) -> str:
        """Truncated sentence for the table cell, with a candidate-count badge."""
        display = sentence if len(sentence) <= 50 else sentence[:47] + "..."
        return f"{display}  ({n_candidates})" if n_candidates > 1 else display

    def _sentence_tooltip(self, sentence: str, n_candidates: int) -> str:
        """Full sentence tooltip, hinting at the picker when alternatives exist."""
        if n_candidates > 1:
            return tr_format(
                self.tr("%1\n\n(%2 sentences available — focus the row, then pick one under “Sentences”)"),
                sentence,
                n_candidates,
            )
        return sentence

    # ------------------------------------------------------------------
    # Signal handlers — checkboxes and search
    # ------------------------------------------------------------------

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        """Called when any table item changes (e.g. checkbox toggled)."""
        if item.column() == 0:
            self._refresh_summary()

    def _on_search_changed(self, _text: str) -> None:
        """Restart the debounce timer on each keystroke.

        The actual row-visibility update runs in :meth:`_apply_search` after
        the 150 ms single-shot timer fires, so rapid typing collapses into one
        pass over the table instead of one per character.
        """
        self._search_debounce_timer.start()

    def _apply_search(self) -> None:
        """Filter visible rows based on the current search input text.

        Reads :attr:`search_input` directly (not the signal argument) so this
        method can be called both by the debounce timer and directly in tests.
        """
        text = self.search_input.text()
        text_lower = text.lower()
        for row in range(self.table.rowCount()):
            if not text:
                self.table.setRowHidden(row, False)
                continue

            # Check surface, lemma, reading, sentence columns
            visible = False
            for col in (1, 2, 3, 4):
                cell = self.table.item(row, col)
                if cell and text_lower in cell.text().lower():
                    visible = True
                    break
            self.table.setRowHidden(row, not visible)

        # The filter is what "visible" means, so both the bulk target and the
        # counter change under it.
        self._refresh_summary()

    # ------------------------------------------------------------------
    # Signal handlers — row focus → player + dictionary
    # ------------------------------------------------------------------

    def _on_row_focus_changed(self) -> None:
        """Handle a cursor or highlight change — refresh the summary, debounce the panes.

        The detail panel and the target/position summary are pure string work, so
        they update immediately: on the app's most keyboard-driven screen they must
        answer the arrow key, not the debounce timer. Only the expensive panes
        (player seek, page decode, dictionary lookup) go through the timer.

        MUST NOT read or write checkbox state; checkbox changes are handled by
        _on_item_changed (itemChanged signal) and kept independent.
        """
        self._refresh_summary()

        word, original_index = self._focused_word()
        if word is None or original_index is None:
            self._render_detail(None)
            return

        self._render_detail(self._chosen.get(original_index, word))

        self._pending_word = word
        self._pending_index = original_index
        # (Re)start the debounce timer — rapid arrow-key scrolling only fires once.
        self._focus_timer.start()

    def _focused_word(self) -> tuple[TokenizedWord | None, int | None]:
        """The word under the cursor and its original index, or ``(None, None)``.

        Resolves through the col-0 ``UserRole`` index because the table is
        sortable, so the visual row is not the word's index.
        """
        current_row = self.table.currentRow()
        if current_row < 0:
            return None, None
        check_item = self.table.item(current_row, 0)
        if check_item is None:
            return None, None
        original_index = check_item.data(Qt.ItemDataRole.UserRole)
        if original_index is None or not (0 <= original_index < len(self._words)):
            return None, None
        return self._words[original_index], original_index

    def _render_detail(self, word: TokenizedWord | None) -> None:
        """Fill (or clear) the detail strip. ``word`` is the user's chosen variant."""
        expression = word.mined_form if word is not None else ""
        reading = word.reading if word is not None else ""
        sentence = word.sentence if word is not None else ""
        self.detail_expression.setText(expression)
        self.detail_reading.setText(reading)
        self.detail_sentence.setText(sentence)
        # The strip is two lines tall by design; the tooltip carries the rest.
        self.detail_sentence.setToolTip(sentence)

    def _on_focus_timer_fired(self) -> None:
        """Debounced handler: refresh sentence picker, seek player, look up definition."""
        word = self._pending_word
        idx = self._pending_index
        if word is None or idx is None:
            return

        # Sentence picker: list the focused word's candidate sentences (no-op
        # for single-occurrence words). Done first so seeking uses the chosen
        # candidate's timing below.
        if self._has_candidates:
            self._populate_candidate_list(word, idx)

        # The scene to preview follows the user's pick (defaults to the word).
        chosen = self._chosen.get(idx, word)

        # Player pane: seek to the chosen sentence's offset-adjusted video position
        # and pause (show the frame without autoplaying). start_time is already
        # raw+offset from the mining parse; ctx.offset only aligns the subtitle
        # overlay — see the set_source call in _create_player_widget. This handler
        # already runs from the debounce timer (outside any active event handler),
        # so the seek can be issued directly — see _on_candidate_chosen.
        self._preview_scene(chosen.start_time)

        # Dictionary pane: look up by mined_form (the card-front spelling, same
        # primary key Phase 4 uses) with a miss-only lemma retry — unidic's
        # canonical lemma collapses kanji variants (殺る → 遣る), so a
        # lemma-keyed pane showed the wrong homograph's entry.
        if self._show_dict and hasattr(self, "definition_view"):
            self._lookup_and_render(word.mined_form, word.lemma)

    def _lookup_and_render(self, term: str, fallback_term: str | None = None) -> None:
        """Show definition entries for ``term``, fetching them off the GUI thread.

        ``lookup_fn`` reaches a SQLite index (and, in the worst case, a chain of
        them), so it cannot run here: this is the app's most keyboard-driven
        screen, and a query on the GUI thread stalls the arrow key that asked
        for it. The 120 ms focus debounce already collapses a scroll into one
        request; this adds the two guarantees a debounce cannot give —

        * at most one request in flight, with only the NEWEST queued behind it,
          so holding the down arrow never queues a backlog of dead lookups;
        * a generation stamp on every request, so a result that arrives after
          the user has moved on is cached but never painted.

        ``fallback_term`` is the miss-only lemma retry: unidic's canonical lemma
        collapses kanji variants (殺る → 遣る), so keying the pane on it showed
        the wrong homograph. Both terms are fetched inside the one background
        job, keeping the retry off the GUI thread as well.
        """
        if self._closing or not self._show_dict:
            return

        # Bump on EVERY request, cache hit included: a newer request must
        # supersede whatever is in flight, or a slower earlier miss would repaint
        # over the row the user is actually looking at.
        self._lookup_gen += 1

        entries = self._cached_entries(term, fallback_term)
        if entries is not None:
            self._pending_lookup = None
            self._render_definitions(term, entries)
            return

        if self._lookup_inflight:
            self._pending_lookup = (term, fallback_term)
            return

        self._dispatch_lookup(term, fallback_term, self._lookup_gen)

    def _cached_entries(self, term: str, fallback_term: str | None) -> list[tuple[str, str]] | None:
        """Entries resolvable from the cache alone, or ``None`` if a fetch is needed.

        An empty list is a real answer (a cached miss), which is why the
        "unresolved" signal is ``None`` rather than falsiness.
        """
        if term not in self._lookup_cache:
            return None
        entries = self._lookup_cache[term]
        if entries or not fallback_term or fallback_term == term:
            return entries
        if fallback_term not in self._lookup_cache:
            return None
        return self._lookup_cache[fallback_term]

    def _dispatch_lookup(self, term: str, fallback_term: str | None, gen: int) -> None:
        """Run the (possibly two-term) query on a worker thread."""
        lookup_fn = self._lookup_fn
        assert lookup_fn is not None  # guarded by self._show_dict
        self._lookup_inflight = True

        def work() -> dict[str, list[tuple[str, str]]]:
            fetched = {term: lookup_fn(term)}
            if not fetched[term] and fallback_term and fallback_term != term:
                fetched[fallback_term] = lookup_fn(fallback_term)
            return fetched

        run_off_thread(
            self,
            work,
            lambda fetched: self._on_lookup_done(gen, term, fallback_term, fetched),
            lambda message: self._on_lookup_failed(gen, term, message),
        )

    def _on_lookup_done(
        self,
        gen: int,
        term: str,
        fallback_term: str | None,
        fetched: object,
    ) -> None:
        """GUI-thread landing point for a completed lookup."""
        self._lookup_inflight = False
        # Cache even a superseded result: it was a correct answer for its term,
        # and scrolling back to that row must not re-query.
        if isinstance(fetched, dict):
            self._lookup_cache.update(fetched)
        if gen == self._lookup_gen:
            self._render_definitions(term, self._cached_entries(term, fallback_term) or [])
        self._drain_pending_lookup()

    def _on_lookup_failed(self, gen: int, term: str, message: str) -> None:
        """GUI-thread landing point for a failed lookup."""
        self._lookup_inflight = False
        logger.warning("definition lookup failed for %s: %s", term, message)
        if gen == self._lookup_gen:
            self._render_definitions(term, [])
        self._drain_pending_lookup()

    def _drain_pending_lookup(self) -> None:
        """Start the newest request that arrived while one was in flight."""
        pending = self._pending_lookup
        self._pending_lookup = None
        if pending is not None and not self._closing:
            self._lookup_and_render(*pending)

    def _render_definitions(self, term: str, entries: list[tuple[str, str]]) -> None:
        """Paint ``entries`` into the definition pane (GUI thread only)."""
        if self._closing or not hasattr(self, "definition_view"):
            return
        if not entries:
            escaped = html.escape(term)
            self.definition_view.setHtml(f'<p style="color:gray">No offline dictionary entry for <b>{escaped}</b></p>')
            return

        parts: list[str] = []
        for name, entry_html in entries:
            escaped_name = html.escape(name)
            parts.append(f'<p style="font-weight:bold">{escaped_name}</p>')
            parts.append(entry_html)

        self.definition_view.setHtml("".join(parts))

    def _populate_candidate_list(self, word: TokenizedWord, idx: int) -> None:
        """Fill the sentence picker for the focused word and select its current pick.

        Repopulation is programmatic, so signals are blocked to avoid the
        ``currentRowChanged`` handler treating it as a user pick. Words with no
        alternatives clear the list.
        """
        if not hasattr(self, "sentence_list"):
            return
        candidates = word.sentence_candidates
        self._populating_candidates = True
        self.sentence_list.blockSignals(True)
        self.sentence_list.clear()
        self._candidate_list_index = idx
        self._candidate_list_words = candidates

        if len(candidates) > 1:
            chosen = self._chosen.get(idx, word)
            selected_row = 0
            for i, cand in enumerate(candidates):
                list_item = QListWidgetItem(cand.sentence)
                list_item.setToolTip(cand.sentence)
                list_item.setFont(japanese_cell_font())
                self.sentence_list.addItem(list_item)
                if self._same_pick(cand, chosen):
                    selected_row = i
            self.sentence_list.setCurrentRow(selected_row)
            self.sentence_list.setEnabled(True)
        else:
            self.sentence_list.setEnabled(False)

        self.sentence_list.blockSignals(False)
        self._populating_candidates = False

    @staticmethod
    def _same_pick(a: TokenizedWord, b: TokenizedWord) -> bool:
        """Whether two variants refer to the same example line (sentence + timing)."""
        return a.sentence == b.sentence and a.start_time == b.start_time

    def _on_candidate_chosen(self, list_row: int) -> None:
        """Apply the user's sentence pick: record it, refresh the cell, seek the scene."""
        if self._populating_candidates or list_row < 0:
            return
        idx = self._candidate_list_index
        if idx is None or not (0 <= list_row < len(self._candidate_list_words)):
            return
        chosen = self._candidate_list_words[list_row]
        self._chosen[idx] = chosen

        # The detail strip restates what will be mined, so it follows the pick.
        self._render_detail(chosen)

        # Refresh the table's Sentence cell for this word (its visual row may
        # differ from idx because the table is sortable).
        n_candidates = len(self._words[idx].sentence_candidates)
        row = self._visual_row_for_index(idx)
        if row is not None:
            item = self.table.item(row, 4)
            if item is not None:
                self.table.blockSignals(True)
                item.setText(self._sentence_display(chosen.sentence, n_candidates))
                item.setToolTip(self._sentence_tooltip(chosen.sentence, n_candidates))
                self.table.blockSignals(False)

        # Preview the chosen scene. Defer the seek to the next event-loop tick:
        # this handler runs synchronously inside the list's currentRowChanged
        # emission (mid mouse-press), and an in-event setPosition+pause doesn't
        # reliably present the new frame — it took a couple of clicks to land.
        # The word-focus path already seeks from a (debounce) timer, i.e. outside
        # any active event handler; deferring here makes the two paths identical.
        start_time = chosen.start_time
        QTimer.singleShot(0, lambda: self._preview_scene(start_time))

    def _preview_scene(self, start_time: float) -> None:
        """Preview the scene for ``start_time``: seek the player / show the page.

        The single funnel for both the debounced focus path and the sentence
        candidate pick path — for manga, ``int(start_time)`` is the reading
        unit index (the parser stamps ``start_time = float(unit.index)``).
        """
        if self._show_player and hasattr(self, "player_widget"):
            self.player_widget.seek_seconds(start_time)
            self.player_widget.pause()
        if self._show_image:
            self._request_page_image(int(start_time))

    def _request_page_image(self, unit_index: int) -> None:
        """Show the page image (with block highlight) for ``unit_index``.

        Loads off-thread with a generation-counter stale-guard; decoded pages
        are LRU-cached so consecutive words on one page render instantly.
        """
        if self._closing or not hasattr(self, "page_image_view"):
            return
        # Bump on EVERY request (hit or miss): a newer request must supersede
        # any in-flight load, otherwise a cache hit could be clobbered by a
        # slower earlier miss that still carries the current generation.
        self._page_request_gen += 1
        gen = self._page_request_gen

        assert self._page_units is not None  # guarded by self._show_image
        unit = self._page_units.get(unit_index)
        if unit is None or unit.image_ref is None:
            caption = unit.location_label if unit is not None else ""
            self.page_image_view.show_message(self.tr("No page image for this word"), caption)
            return
        ref = unit.image_ref
        box = unit.block_box
        caption = unit.location_label

        cached = self._page_cache.get(ref)
        if cached is not None:
            self._page_cache.move_to_end(ref)
            self.page_image_view.show_page(QPixmap.fromImage(cached), box, caption)
            return

        def on_done(image: object) -> None:
            # Gen check FIRST: it reads only a plain Python attribute, so a
            # late result after dialog teardown returns before touching any
            # Qt object (teardown bumps the generation).
            if gen != self._page_request_gen:
                return
            assert isinstance(image, QImage)
            self._page_cache[ref] = image
            while (
                len(self._page_cache) > _PAGE_CACHE_CAP
                or sum(cached.sizeInBytes() for cached in self._page_cache.values()) > _PAGE_CACHE_MAX_BYTES
            ):
                self._page_cache.popitem(last=False)
            self.page_image_view.show_page(QPixmap.fromImage(image), box, caption)

        def on_error(message: str) -> None:
            if gen != self._page_request_gen:
                return
            logger.warning("page image load failed for %s: %s", ref, message)
            self.page_image_view.show_message(self.tr("Could not load page image"), caption)

        # QImage decodes off-thread (thread-safe); QPixmap conversion happens
        # in on_done on the GUI thread (QPixmap is GUI-thread-only).
        run_off_thread(self, lambda: load_page_qimage(ref), on_done, on_error)

    def _visual_row_for_index(self, idx: int) -> int | None:
        """Find the table row whose col-0 UserRole holds original word index ``idx``."""
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) == idx:
                return row
        return None

    def _stop_player(self) -> None:
        """Release preview resources when the dialog closes (any exit path).

        ``release`` (not ``stop``) so an in-flight ffprobe probe is joined: Qt
        does not forward the dialog's close to the child player widget, so a
        still-running probe worker would otherwise outlive it.

        Teardown ordering is load-bearing: the dialog is deleteLater()'d right
        after exec() returns, and destroying a running QThread child aborts the
        process. ``_closing`` is set FIRST so a pending ``_focus_timer`` tick or
        the uncancelable ``QTimer.singleShot(0)`` from ``_on_candidate_chosen``
        — either can fire after this drain but before the deferred delete — can
        no longer dispatch a fresh worker onto the dying dialog
        (``_request_page_image`` and ``_lookup_and_render`` early-return on it).

        The drain is UNCONDITIONAL. It used to run only for the manga-image
        pane, which was correct while that pane owned the only background work;
        dictionary lookups are dispatched the same way now, and a dialog with no
        tracked worker at all just drains an empty set.
        """
        self._closing = True
        self._focus_timer.stop()
        self._search_debounce_timer.stop()
        # Late results are dropped before touching any widget: every callback
        # checks its generation (a plain Python attribute) first.
        self._page_request_gen += 1
        self._lookup_gen += 1
        self._pending_lookup = None
        laggards = join_tracked_workers(self, timeout_ms=200)
        for worker in laggards:
            # Neither a PIL decode nor a dictionary query is cancelable mid-call;
            # detach laggards so the dialog's destruction never destroys a
            # running QThread. Detached workers finish harmlessly and the global
            # off-thread registry still reaps them at app close.
            worker.setParent(None)
        if self._show_player and hasattr(self, "player_widget"):
            self.player_widget.release()

    # ------------------------------------------------------------------
    # Right-click context menu (#43)
    # ------------------------------------------------------------------

    def _on_table_context_menu(self, pos: QPoint) -> None:
        """Show a context menu with copy actions for the focused row."""
        row = self.table.rowAt(pos.y())
        if row < 0:
            return

        check_item = self.table.item(row, 0)
        if check_item is None:
            return
        original_index = check_item.data(Qt.ItemDataRole.UserRole)
        if original_index is None or not (0 <= original_index < len(self._words)):
            return

        word = self._words[original_index]
        menu = QMenu(self)

        copy_lemma_action = menu.addAction(self.tr("Copy lemma"))
        copy_sentence_action = menu.addAction(self.tr("Copy sentence"))

        vp = self.table.viewport()
        if vp is None:
            return
        action = menu.exec(vp.mapToGlobal(pos))
        clipboard = QApplication.clipboard()
        if clipboard is None:
            return
        if action == copy_lemma_action:
            clipboard.setText(word.lemma)
        elif action == copy_sentence_action:
            # Resolve the user's sentence pick (self._chosen), else the default —
            # same "chosen, else original" pattern as get_selected_words. Without
            # this the menu always copied the primary sentence (Issue #95).
            chosen = self._chosen.get(original_index, word)
            clipboard.setText(chosen.sentence)

    # ------------------------------------------------------------------
    # Bulk-action helpers
    # ------------------------------------------------------------------

    def _visible_rows(self) -> list[int]:
        """Rows the search filter is currently showing, in visual order."""
        return [row for row in range(self.table.rowCount()) if not self.table.isRowHidden(row)]

    def _highlighted_rows(self) -> list[int]:
        """Visible rows in the table's selection (Ctrl/Shift+Click), in visual order."""
        selection_model = self.table.selectionModel()
        if selection_model is None:
            return []
        return sorted(
            {index.row() for index in selection_model.selectedRows() if not self.table.isRowHidden(index.row())}
        )

    def _select_all(self) -> None:
        """Include every visible row (decision D32).

        There is no longer a target *mode*. This verb, its exclude twin and
        :meth:`_include_highlighted` each own one fixed set, named and counted on
        their own button. The rule they replace — "highlighted rows if 2+, else
        all visible" — meant highlighting a single word and pressing a bulk
        button silently acted on the whole filtered list, and nothing on screen
        said which had happened.
        """
        self._set_check_state(self._visible_rows(), Qt.CheckState.Checked)

    def _deselect_all(self) -> None:
        """Exclude every visible row."""
        self._set_check_state(self._visible_rows(), Qt.CheckState.Unchecked)

    def _include_highlighted(self) -> None:
        """Include exactly the highlighted rows.

        The mirror verb — exclude the highlight — is the S key, which toggles it;
        with every row checked by default, that IS the exclude gesture, so a
        fourth button would only restate it.
        """
        self._set_check_state(self._highlighted_rows(), Qt.CheckState.Checked)

    def _set_check_state(self, rows: list[int], state: Qt.CheckState) -> None:
        self.table.blockSignals(True)
        for row in rows:
            item = self.table.item(row, 0)
            if item and self._is_checkable(item):
                item.setCheckState(state)
        self.table.blockSignals(False)
        self._refresh_summary()

    @staticmethod
    def _is_checkable(item: QTableWidgetItem) -> bool:
        """Whether a checkbox item still accepts toggling.

        Rows added to the known/ignore list have their checkable flag stripped so
        bulk actions and the S toggle key can't re-include them (Issue #42).
        """
        return bool(item.flags() & Qt.ItemFlag.ItemIsUserCheckable)

    def _toggle_selected_rows(self) -> None:
        """Toggle checkboxes for highlighted rows, or the current row when none.

        If any target row is unchecked, all flip to Checked; otherwise all flip
        to Unchecked. Falls back to the focused row when the selection is empty
        so the S key on a single-cursor view still toggles that one row
        (Space is now play/pause — Issue #55).
        """
        rows = self._highlighted_rows()
        if not rows:
            current = self.table.currentRow()
            if current < 0 or self.table.isRowHidden(current):
                return
            rows = [current]

        items = [item for row in rows if (item := self.table.item(row, 0)) is not None and self._is_checkable(item)]
        if not items:
            return
        any_unchecked = any(item.checkState() != Qt.CheckState.Checked for item in items)
        new_state = Qt.CheckState.Checked if any_unchecked else Qt.CheckState.Unchecked

        self.table.blockSignals(True)
        for item in items:
            item.setCheckState(new_state)
        self.table.blockSignals(False)
        self._refresh_summary()

    _toggle_current_row = _toggle_selected_rows

    def _known_target_rows(self) -> list[int]:
        """Rows for "Add to Known Words": highlighted rows, else the current row.

        Unlike :meth:`_target_rows`, this never falls back to every visible row —
        ignoring an entire filtered list with one click would be too easy to
        trigger by accident.
        """
        highlighted = self._highlighted_rows()
        if highlighted:
            return highlighted
        current = self.table.currentRow()
        if current >= 0 and not self.table.isRowHidden(current):
            return [current]
        return []

    def _on_add_to_known(self) -> None:
        """Add the target rows to the local known/ignore list (Issue #42).

        Persists immediately via the callback, then strikes through and unchecks
        the rows so they are excluded from this run and can't be re-checked.
        """
        rows = [row for row in self._known_target_rows() if self._row_is_active(row)]
        if not rows:
            return

        forms: set[str] = set()
        for row in rows:
            word_item = self.table.item(row, 1)  # "Word (mined)" column
            if word_item:
                forms.add(word_item.text())
        if not forms:
            return

        if self._mark_known_callback is not None:
            self._mark_known_callback(forms)

        self.table.blockSignals(True)
        for row in rows:
            self._mark_row_known(row)
        self.table.blockSignals(False)
        self._refresh_summary()

    def _row_is_active(self, row: int) -> bool:
        """Whether a row hasn't already been marked known (checkbox still toggles)."""
        item = self.table.item(row, 0)
        return item is not None and self._is_checkable(item)

    def _mark_row_known(self, row: int) -> None:
        """Visually mark a row as ignored: strikethrough, grey, unchecked, locked."""
        check_item = self.table.item(row, 0)
        if check_item:
            check_item.setCheckState(Qt.CheckState.Unchecked)
            # Strip the checkable flag so bulk actions / the S toggle key can't re-include it.
            check_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        grey = QColor(128, 128, 128)
        for col in range(1, self.table.columnCount()):
            item = self.table.item(row, col)
            if item:
                font = item.font()
                font.setStrikeOut(True)
                item.setFont(font)
                item.setForeground(grey)

    def _refresh_summary(self) -> None:
        """Re-derive everything on screen that describes the current state.

        Called after every selection, sort, filter and checkbox change, because
        the bulk-button labels and the counter are the only places the user can
        read what a bulk action is about to do.
        """
        self._refresh_bulk_labels()
        self._update_word_count()

    def _refresh_bulk_labels(self) -> None:
        """Put each bulk verb's own live count on its own button."""
        visible = len(self._visible_rows())
        highlighted = len(self._highlighted_rows())
        for button, text in (
            (self.select_all_button, tr_format(self.tr("Include visible (%1)"), visible)),
            (self.deselect_all_button, tr_format(self.tr("Exclude visible (%1)"), visible)),
            (self.include_highlighted_button, tr_format(self.tr("Include highlighted (%1)"), highlighted)),
        ):
            button.setText(text)
            button.setAccessibleName(text)
        # A verb with an empty target is a dead control, not a silent no-op.
        self.include_highlighted_button.setEnabled(highlighted > 0)

    def _update_word_count(self) -> None:
        """Update the counter line: position, included total, filtered total."""
        included = sum(
            1
            for row in range(self.table.rowCount())
            if (item := self.table.item(row, 0)) and item.checkState() == Qt.CheckState.Checked
        )
        total = len(self._words)
        visible = self._visible_rows()
        shown = len(visible)
        current = self.table.currentRow()
        if current in visible:
            text = tr_format(
                self.tr("Word %1 of %2 · %3 included · %4 shown of %5"),
                visible.index(current) + 1,
                shown,
                included,
                shown,
                total,
            )
        else:
            text = tr_format(self.tr("%1 included · %2 shown of %3"), included, shown, total)
        self.word_count_label.setText(text)

    def get_selected_words(self) -> list[TokenizedWord]:
        """Return the checked words, each as the sentence variant the user picked.

        Falls back to the original word when no alternative sentence was chosen
        (the common case — single-occurrence words, or untouched multi-occurrence
        words keep their default pick).
        """
        selected = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item and item.checkState() == Qt.CheckState.Checked:
                original_index = item.data(Qt.ItemDataRole.UserRole)
                if original_index is not None and 0 <= original_index < len(self._words):
                    selected.append(self._chosen.get(original_index, self._words[original_index]))
        return selected
