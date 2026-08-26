"""Dialog for curating words before card creation."""

from __future__ import annotations

import contextlib
import dataclasses
import html
import logging
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from anki_miner.gui.widgets.subtitle_player_widget import SubtitlePlayerWidget
    from anki_miner.models.reading import ImageRef, ReadingUnit

from PyQt6.QtCore import QByteArray, QPoint, Qt, QTimer
from PyQt6.QtGui import (
    QCloseEvent,
    QColor,
    QFont,
    QImage,
    QKeySequence,
    QPixmap,
    QShortcut,
    QShowEvent,
)
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from anki_miner.gui.resources.styles import SPACING
from anki_miner.gui.utils import session_state
from anki_miner.gui.utils.fonts import japanese_cell_font, make_scaled_font
from anki_miner.gui.utils.keyboard_shortcuts import disown_default_buttons, primary_action_shortcut
from anki_miner.gui.utils.phrase_wrap import phrase_wrap_ja
from anki_miner.gui.utils.qt_helpers import (
    COPY_ROLE,
    CellRole,
    add_min_max_buttons,
    configure_data_view,
    install_copy_rows,
    make_table_item,
    update_table_item,
)
from anki_miner.gui.utils.run_off_thread import join_tracked_workers, run_off_thread
from anki_miner.gui.widgets.audio_clip_editor import MAX_CLIP_SECONDS, AudioClipEditor
from anki_miner.gui.widgets.base import ScreenIssue, ScreenIssueHost
from anki_miner.gui.widgets.base.eliding_label import ElidingLabel
from anki_miner.gui.widgets.base.sizing import metric_row_height
from anki_miner.gui.widgets.enhanced import ModernButton
from anki_miner.gui.widgets.page_image_view import PageImageView, load_page_qimage
from anki_miner.gui.workers.base_worker import SingleCallWorker
from anki_miner.models import TokenizedWord
from anki_miner.services.dictionary.preview_html import PREVIEW_CSS, to_preview_html
from anki_miner.services.word_filter import MergedLineWindow, find_cue_index, merge_cue_window
from anki_miner.utils.i18n import tr_format

logger = logging.getLogger(__name__)

# Decoded-page LRU cap. A full-res manga page is ~13-22 MB as RGBA, so cap 4
# bounds the cache at ~90 MB worst case — a deliberate memory/simplicity
# tradeoff (vs. downscale-on-load + box rescale); consecutive words usually
# share a page, so 4 pages of backtrack covers real navigation.
_PAGE_CACHE_CAP = 4
_PAGE_CACHE_MAX_BYTES = 64 * 1024 * 1024

#: Table column : side column, as stretch factors. Also the ratio the split
#: opens at, so the first frame and every resize after it agree.
_MAIN_SPLIT_STRETCH = (3, 2)


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
    #: ``config.audio_padding`` — what the default clip window widens the
    #: subtitle line by on each side. Carried here rather than handed to the
    #: dialog separately: this is already the frozen carrier for media facts,
    #: built where the config is in scope. The audio clip strip needs it to
    #: show the user the window that would be cut without an edit.
    audio_padding: float = 0.3
    #: Season curation (batch tab): resolves another episode's video to its own
    #: media context so the player can follow cross-episode word focus. Pure
    #: and thread-safe over a snapshot (never the live worker) — the dialog
    #: calls it off the GUI thread. ``None`` (every single-episode caller)
    #: keeps the player pinned to ``video_file``.
    context_resolver: Callable[[Path], CurationMediaContext | None] | None = None


class WordCurationDialog(ScreenIssueHost, QDialog):
    """Dialog for selecting which words to include in card creation.

    Shows a table of words with checkboxes. Users search/filter, include or
    exclude in bulk, and confirm. It is a primary interactive surface, not a
    confirmation step: the app automates the mining mechanics, and a user
    frequently picks the words by hand, so every bulk verb names and counts its
    own target and a counter states position/included/shown.

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
        commit_known_callback: Callable[[set[str]], int] | None = None,
        media_context: CurationMediaContext | None = None,
        lookup_fn: Callable[..., list[tuple[str, str]]] | None = None,
    ):
        super().__init__(parent)
        self._words = words
        # Known Words are STAGED, not written (D34-B). Add to Known Words marks
        # rows "Known · pending"; only a successful Confirm calls this callback,
        # and it is called ON A WORKER THREAD. Cancel, Esc, the window X, the
        # tab's Cancel button, run teardown and app shutdown all discard the
        # stage, so no exit that abandons the review leaves a durable trace.
        # This reverses the immediate write documented against Issue #42.
        self._commit_known_callback = commit_known_callback
        self._pending_known_forms: set[str] = set()
        # Check state each row carried before it was staged known, keyed by the
        # ORIGINAL word index (col-0 UserRole) so it survives a re-sort.
        # Unstaging restores what the user had rather than assuming Checked: a
        # row they deliberately excluded and then marked known must not come
        # back included.
        self._known_prior_check: dict[int, Qt.CheckState] = {}
        # Stale-guard for the commit: cancel() silences a worker only if it wins
        # the race against an already-queued result signal, so every callback
        # also checks its generation. Bumped by force_reject and by teardown.
        self._known_commit_gen = 0
        self._known_commit_running = False
        self._known_commit_worker: SingleCallWorker | None = None
        self._media_context = media_context
        self._lookup_fn = lookup_fn

        # Determine whether each optional pane should be shown.
        ctx = media_context
        self._show_player = ctx is not None and ctx.video_file is not None and ctx.video_file.exists()
        # Season curation (context_resolver set): the episode the player is
        # currently showing, a cache of resolved per-episode contexts, and a
        # generation counter guarding off-thread resolves. The cache holds
        # plain subtitle-entry tuples for at most a season's worth of episodes
        # — a few hundred KB — so unlike the page-image cache it needs no LRU.
        self._displayed_media_video: Path | None = ctx.video_file if ctx is not None else None
        self._media_ctx_cache: dict[Path, CurationMediaContext] = {}
        if ctx is not None and ctx.video_file is not None:
            self._media_ctx_cache[ctx.video_file] = ctx
        self._media_swap_gen = 0
        self._show_dict = lookup_fn is not None
        # Manga page pane: gated on page_units exactly like the player gates
        # on video_file. Cache holds converted QPixmaps (GUI-thread only);
        # _page_request_gen is the stale-guard for off-thread loads and
        # _closing blocks any dispatch once teardown has run (see _stop_player).
        self._page_units = ctx.page_units if ctx is not None else None
        self._show_image = bool(self._page_units)
        self._page_cache: OrderedDict[ImageRef, tuple[QPixmap, int]] = OrderedDict()
        self._page_request_gen = 0
        self._closing = False
        # Sentence picker: shown when at least one word has alternative example
        # sentences (it appears on >= 2 subtitle lines). The chosen variant per
        # word index lives in self._chosen; get_selected_words falls back to the
        # original word when the user never picks an alternative.
        self._has_candidates = any(len(w.sentence_candidates) > 1 for w in words)
        self._chosen: dict[int, TokenizedWord] = {}
        # Per-word audio clip windows the user edited, keyed by original word
        # index exactly like _chosen. Applied in get_selected_words; empty for
        # every run where nobody touched the strip, which is the common case.
        self._clip_overrides: dict[int, tuple[float, float]] = {}
        # The word the audio clip strip is currently showing, so an edit lands
        # on the right index no matter how the table has been sorted.
        self._clip_index: int | None = None
        # Per-word subtitle-line expansion (prev_count, next_count), keyed by
        # original word index exactly like _chosen/_clip_overrides (Issue
        # #120). Stamped onto the selection as TokenizedWord.line_expansion;
        # the processor materializes the merged sentence/timings.
        self._line_expansions: dict[int, tuple[int, int]] = {}
        # Context for the candidate list while a row is focused: the focused
        # word's index + its candidate variants. Guards programmatic
        # repopulation from being mistaken for a user pick.
        self._candidate_list_index: int | None = None
        self._candidate_list_words: list[TokenizedWord] = []
        self._populating_candidates = False

        # Lookup result cache keyed by (term, scope_lemma) (empty results are
        # cached too). scope_lemma (see _scope_lemma) is part of the key, not
        # just an input: two curator rows can share a mined_form but differ in
        # lemma (kana front ゆう from 言う vs from 結う — upstream dedups by
        # lemma, word_filter.py, so both survive as distinct rows), and a
        # term-only key would serve one row's lemma-scoped entry to the
        # other — the exact wrong-homograph pane bug Rule A' exists to fix.
        # The miss-only fallback-term retry is always unscoped, so it caches
        # under (fallback_term, None). The fetch itself runs off the GUI
        # thread: at most one request is in flight, the newest queued request
        # replaces any older one, and every callback is checked against
        # _lookup_gen so a fast scroll can never paint an entry the user has
        # already scrolled past.
        self._lookup_cache: dict[tuple[str, str | None], list[tuple[str, str]]] = {}
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

        # Layout state (D: remembered between queue items). The side key names
        # the pane composition, so a manga curator never restores a video
        # curator's sizes; a table-only dialog composes none and saves none.
        self._split_ratios_applied = False
        self._main_split_restored = False
        self._side_split_restored = False
        self._layout_state_saved = False
        self._side_key = ""
        self._side_stretch: list[int] = []
        # A raise anywhere below leaves the caller with `dialog = None` and no
        # way to release the player it already built: there is no dialog to
        # close, so `finished` never fires, and the half-built window stays
        # parented to the tab with a live mpv core decoding inside it. The
        # release is idempotent, so the normal path pays nothing.
        try:
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
            # Last, because both calls above go through setWindowFlag, which resets
            # a window's geometry on some platforms.
            self._restore_layout_state()
        except BaseException:
            # getattr twice: the player may not exist yet (the raise came before
            # the pane was built, or there is no player pane at all), and a test
            # stub may not carry release. Never let this cleanup replace the
            # exception the caller has to see.
            release = getattr(getattr(self, "player_widget", None), "release", None)
            if release is not None:
                try:
                    release()
                except Exception:
                    logger.warning("player_widget.release() failed during __init__ cleanup", exc_info=True)
            raise

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
        # The real width floor is the toolbar row's own minimum (~1010px in
        # English), which the layout enforces on its own. It is stated here as
        # the intent, not as the mechanism. What matters is that the row now
        # spans the dialog: a longer locale widens the window instead of
        # starving the media column, which is what it did while the row lived
        # inside the left splitter pane.
        self.setMinimumWidth(900)
        self.setMinimumHeight(600)
        if self._show_player or self._show_image or self._show_dict or self._has_candidates:
            # Taller than it used to be, and for a stated reason: a side column
            # holding a 16:9 frame, a picker and a dictionary entry wants about
            # 720px before anything has to scroll. Clamped, so the gain is only
            # taken where the screen has it.
            self._resize_within_screen(1500, 860)
        else:
            self._resize_within_screen(1100, 700)

        layout = QVBoxLayout()
        layout.setSpacing(SPACING.sm)
        layout.setContentsMargins(SPACING.lg, SPACING.lg, SPACING.lg, SPACING.lg)

        # Header row (outside the splitter — always visible). The title elides
        # because it is chrome; the counter does not, because it is the only
        # statement of position/included/shown on the screen (D32).
        header = ElidingLabel(self.tr("Select words for card creation"))
        header.setFont(self._make_font(16, QFont.Weight.Bold))
        self.word_count_label = QLabel()
        self.word_count_label.setFont(self._make_font(12, QFont.Weight.Medium))
        header_row = QHBoxLayout()
        header_row.setSpacing(SPACING.sm)
        header_row.addWidget(header, 1)
        header_row.addWidget(self.word_count_label)
        layout.addLayout(header_row)

        # The filter/bulk toolbar spans the whole dialog rather than living in
        # the left splitter pane. Its row cannot shrink -- a 200px search field,
        # four full-text verbs and the counter measured a 1254px floor -- and a
        # QSplitter honours a child's minimumSizeHint absolutely, so inside the
        # pane it pinned the media column at its own ~200px minimum at every
        # window size. The floor is font-sized, not screen-sized, which is why
        # maximising never widened the media column. Keep this row out here.
        layout.addLayout(self._build_toolbar_row())

        # Build the left pane (table + key hints)
        left_pane = self._build_left_pane()

        if self._show_player or self._show_image or self._show_dict or self._has_candidates:
            # Horizontal splitter: left = word table, right = player/page + sentences + dict
            self._main_splitter = QSplitter(Qt.Orientation.Horizontal)
            self._main_splitter.addWidget(left_pane)
            self._main_splitter.addWidget(self._build_right_pane())
            # The ratio is a decision, not a leftover: the table reads wide and
            # the side column has to hold a legible video frame and a
            # dictionary entry. Stretch factors are what survive a resize --
            # the previous setSizes([700, 800]) said nothing about growth, and
            # said it about a 1500px window the dialog no longer opens at.
            self._main_splitter.setStretchFactor(0, _MAIN_SPLIT_STRETCH[0])
            self._main_splitter.setStretchFactor(1, _MAIN_SPLIT_STRETCH[1])
            self._main_splitter.setChildrenCollapsible(False)
            layout.addWidget(self._main_splitter, 1)
        else:
            layout.addWidget(left_pane, 1)

        # Footer buttons (outside the splitter — always visible)
        footer_layout = QHBoxLayout()
        footer_layout.addStretch()

        self.cancel_button = ModernButton(self.tr("Cancel"), variant="secondary")
        self.cancel_button.clicked.connect(self.reject)
        self.cancel_button.setMinimumWidth(100)
        footer_layout.addWidget(self.cancel_button)

        self.confirm_button = ModernButton(self.tr("Confirm Selection"), variant="primary")
        self.confirm_button.clicked.connect(self.accept)
        self.confirm_button.setMinimumWidth(140)
        footer_layout.addWidget(self.confirm_button)

        layout.addLayout(footer_layout)
        self.setLayout(layout)
        # A failed Known Words write is recoverable — the user retries Confirm or
        # cancels — so it belongs in a banner, never a modal.
        self.install_issue_banner(layout)
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

    def _build_toolbar_row(self) -> QHBoxLayout:
        """Build the search field and the bulk verbs, full dialog width."""
        controls_layout = QHBoxLayout()
        controls_layout.setSpacing(SPACING.sm)

        search_label = QLabel(self.tr("Search:"))
        controls_layout.addWidget(search_label)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText(self.tr("Filter by any field..."))
        self.search_input.textChanged.connect(self._on_search_changed)
        # The field takes a share of the row's surplus instead of demanding a
        # flat 200px. This row IS the dialog's width floor, and everything else
        # in it -- the label, the four verbs -- is text, so the floor tracks the
        # rendered face: 815px here, 991px on a CI runner whose 'Sans Serif'
        # resolves ~20% wider, which put the whole curator past a 1024px screen
        # without a line of layout changing. A flat 200 was the one part of that
        # floor answering to nothing, so it is measured through the font too and
        # the field grows with the window instead.
        # The cap keeps a filter box from spanning half a maximised window once
        # it is allowed to grow at all.
        self.search_input.setMinimumWidth(self.fontMetrics().height() * 5)
        self.search_input.setMaximumWidth(self.fontMetrics().height() * 16)
        controls_layout.addWidget(self.search_input, 1)

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

        # Stage rows for the local known/ignore list, or take the mark back off
        # them. Acts on the highlighted rows, or the current row when nothing is
        # highlighted — deliberately NOT all visible rows, to avoid ignoring the
        # whole list by accident.
        #
        # This is the one verb in the row that changes meaning with the
        # selection, and it has to: an undo the user cannot see is not an undo.
        # _refresh_known_button re-derives the label and tooltip on every
        # selection change, so the button always names the click it is about to
        # perform instead of leaving the user to infer it.
        self.add_known_button = ModernButton(variant="secondary")
        self.add_known_button.clicked.connect(self._on_add_to_known)
        # The width is pinned to the wider of the two faces, measured through the
        # rendered button. This row IS the dialog's width floor (see the search
        # field above), so a label that flips must not reflow the toolbar under
        # the user's cursor halfway through a review.
        widest = 0
        for label in self._known_button_labels():
            self.add_known_button.setText(label)
            widest = max(widest, self.add_known_button.sizeHint().width())
        self.add_known_button.setMinimumWidth(widest)
        # The table does not exist yet — this row is built before
        # _build_left_pane — so the real label comes from the _refresh_summary in
        # the constructor tail. Seed the add face rather than leaving whichever
        # one the measuring loop set last.
        self.add_known_button.setText(self._known_button_labels()[0])
        # Nowhere to commit means the verb would silently stage marks that can
        # never be written, so it is a dead control rather than a lie.
        self.add_known_button.setEnabled(self._commit_known_callback is not None)
        controls_layout.addWidget(self.add_known_button)

        controls_layout.addStretch()
        return controls_layout

    def _build_left_pane(self) -> QWidget:
        """Build the left pane: the word table and the key hints beneath it."""
        container = QWidget()
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(SPACING.sm)

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
        # Occurrences and the Sentences picker count different things, and a user
        # who reads the first as "example sentences I can pick" reports the gap as
        # a bug. Occurrences counts every appearance, including several on one
        # line; the picker offers one option per line, minus the lines whose
        # inflection would change the card's Word.
        occurrences_header = self.table.horizontalHeaderItem(6)
        if occurrences_header is not None:
            occurrences_header.setToolTip(
                self.tr(
                    "How many times this word appears in this episode.\n\n"
                    "The “Sentences” picker offers one option per subtitle line, so it "
                    "usually lists fewer: repeats on the same line count once here, and "
                    "lines where the word takes a form that would change the card’s Word "
                    "are skipped."
                )
            )

        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.table.setSortingEnabled(True)

        header_view = self.table.horizontalHeader()
        if header_view:
            # The include column normally holds one check indicator and its cell
            # padding, so it is measured from its contents rather than pinned at
            # 40px -- the indicator grows with the platform and with the text
            # scale. It also has to fit the "Known · pending" label a staged row
            # puts there, which is why it is not Fixed: the column widens only
            # while a stage exists and shrinks back when the marks are discarded.
            header_view.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
            for column in (1, 2, 3, 5, 6):  # mined form, surface, reading, rank, count
                header_view.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
            header_view.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)  # sentence

        self._apply_data_surface()
        install_copy_rows(self.table)

        self.table.itemChanged.connect(self._on_item_changed)

        # Row-focus wiring — independent of checkbox state. Always connected: the
        # target/position summary exists even on a plain table-only dialog, and
        # _on_row_focus_changed is what keeps it truthful.
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

        # Nothing between the table and the hint line. A detail strip used to sit
        # here restating the focused row's mined form, reading and sentence
        # (decision D45-B); all three are already columns 1, 3 and 4, so it cost
        # ~90px of the one pane the screen is actually for. The untruncated
        # sentence it alone showed stays reachable on the cell's own tooltip,
        # right-click → Copy sentence, and the Sentences pane.
        vbox.addWidget(self.table, 1)
        vbox.addWidget(self._build_key_hints())
        return container

    def _build_key_hints(self) -> QLabel:
        """One quiet line naming the keys this screen answers to."""
        # "visible" is the word the bulk buttons use, because Ctrl+A/Ctrl+D ARE
        # those buttons: _select_all/_deselect_all act on every visible row, not
        # on the focused one. A bare "include/exclude" read as a per-row verb and
        # went outright false once Search narrowed the list. Keep the two in step.
        #
        # One unsplit literal per variant even past the line limit: pylupdate6
        # extracts the tr() argument as written, so a concatenation reaches the
        # catalogs in pieces. E501 is off project-wide and black leaves strings be.
        if self._show_player:
            text = self.tr(
                "S include/exclude · Space play/pause · Ctrl+A include visible · Ctrl+D exclude visible · Ctrl+Enter confirm"
            )
        else:
            text = self.tr("S include/exclude · Ctrl+A include visible · Ctrl+D exclude visible · Ctrl+Enter confirm")
        self.key_hint_label = QLabel(text)
        self.key_hint_label.setObjectName("curator-key-hints")
        self.key_hint_label.setFont(self._make_font(11))
        # Wraps because an unwrapped QLabel demands its full text width as a
        # MINIMUM -- 581px measured for the player variant, and more in a longer
        # locale. Inside a splitter pane that is a hard floor on the pane, which
        # is the same defect that kept the media column at 200px.
        self.key_hint_label.setWordWrap(True)
        return self.key_hint_label

    def showEvent(self, a0: QShowEvent | None) -> None:  # noqa: N802 - Qt override
        """Set the opening split ratios once, against real geometry.

        Not from ``_setup_ui``: a splitter that has never been laid out reports
        zero width, so a ratio computed there divides nothing. First show is
        the earliest moment the numbers mean anything, and doing it once leaves
        every later drag alone.
        """
        super().showEvent(a0)
        if self._split_ratios_applied:
            return
        self._split_ratios_applied = True
        if not self._main_split_restored:
            self._apply_main_split_ratio()
        if not self._side_split_restored:
            self._apply_side_split_ratio()

    def _apply_main_split_ratio(self) -> None:
        """Open the split at the same ratio the stretch factors defend.

        Sized from the splitter's own width rather than from a pair of
        constants: ``setSizes([700, 800])`` described a window this dialog does
        not open at, and its numbers survived nothing. Anything the layout
        cannot honour (a pane's minimum) is clamped by Qt, which is intended.
        """
        if not hasattr(self, "_main_splitter"):
            return
        left, right = _MAIN_SPLIT_STRETCH
        usable = self._main_splitter.width()
        self._main_splitter.setSizes([usable * left // (left + right), usable * right // (left + right)])

    # ------------------------------------------------------------------
    # Remembered window and split positions
    # ------------------------------------------------------------------

    def _restore_layout_state(self) -> None:
        """Re-apply the window size and split positions from the last review.

        The curator is rebuilt for every item in a mining queue, so without
        this a user who widens the video column widens it again per word.

        Order matters: geometry first, because the split blobs are pixel sizes
        and Qt clamps them to the window they land in.
        """
        geometry, main_split, side_split = session_state.load_curator_layout(self._side_key)
        if geometry is not None and self.restoreGeometry(geometry) and not self._is_on_a_live_screen():
            # restoreGeometry relocates a window whose screen is GONE, but not
            # one whose screen merely shrank, so a rect saved on a larger
            # monitor can land entirely off the current one.
            self._apply_default_geometry()
        self._main_split_restored = self._restore_split(getattr(self, "_main_splitter", None), main_split)
        self._side_split_restored = self._restore_split(getattr(self, "_side_splitter", None), side_split)

    def _is_on_a_live_screen(self) -> bool:
        """True when the window's centre sits on a screen that exists."""
        return QApplication.screenAt(self.frameGeometry().center()) is not None

    def _apply_default_geometry(self) -> None:
        """Shrink to fit the current screen. Position is left to Qt."""
        self._resize_within_screen(self.width(), self.height())

    def _resize_within_screen(self, width: int, height: int) -> None:
        """Resize, but never to more than the screen can show.

        The curator opened at a flat 1500x760 whatever it was opening on, so a
        1366x768 laptop got a window taller than its own desktop with the
        Confirm button under the taskbar.
        """
        screen = self.screen() or QApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            width = min(width, available.width())
            height = min(height, available.height())
        self.resize(width, height)

    def _restore_split(self, splitter: QSplitter | None, blob: QByteArray | None) -> bool:
        """Restore ``splitter`` from ``blob``; answer whether it took.

        ``restoreState`` carries ``childrenCollapsible`` and the stretch
        factors along with the sizes, so a blob written before those were set
        would quietly undo them -- they are re-applied afterwards, always.

        A restored size below a pane's own minimum is rejected wholesale rather
        than clamped: the point of the blob is a layout the user chose, and a
        half-honoured one is not that.
        """
        if splitter is None or blob is None:
            return False
        if not splitter.restoreState(blob) or len(splitter.sizes()) != splitter.count():
            self._reassert_split_policy(splitter)
            return False
        for index, size in enumerate(splitter.sizes()):
            pane = splitter.widget(index)
            floor = pane.minimumSizeHint() if pane is not None else None
            if floor is None:
                continue
            if size < (floor.height() if splitter.orientation() == Qt.Orientation.Vertical else floor.width()):
                self._reassert_split_policy(splitter)
                return False
        self._reassert_split_policy(splitter)
        return True

    def _reassert_split_policy(self, splitter: QSplitter) -> None:
        """Re-apply what ``restoreState`` is allowed to have overwritten."""
        splitter.setChildrenCollapsible(False)
        if splitter is getattr(self, "_main_splitter", None):
            splitter.setStretchFactor(0, _MAIN_SPLIT_STRETCH[0])
            splitter.setStretchFactor(1, _MAIN_SPLIT_STRETCH[1])
        elif splitter is getattr(self, "_side_splitter", None):
            for index, stretch in enumerate(self._side_stretch):
                splitter.setStretchFactor(index, stretch)

    def done(self, a0: int) -> None:  # noqa: D102 - Qt override, documented below
        # Every exit funnels through QDialog::done -- accept, reject, Esc, the
        # window X and force_reject -- and it runs while the window is still
        # visible, so the geometry read here is the one the user is looking at.
        # `finished` would be too late (it fires after hide) and its connection
        # order is frozen by the teardown contract in __init__.
        #
        # Suppressed rather than trusted: a mining queue item is waiting on
        # this dialog closing, and remembering a splitter position is not worth
        # holding it open for.
        if not self._layout_state_saved:
            self._layout_state_saved = True
            main = getattr(self, "_main_splitter", None)
            side = getattr(self, "_side_splitter", None)
            with contextlib.suppress(Exception):
                session_state.save_curator_layout(
                    self.saveGeometry(),
                    main.saveState() if main is not None else None,
                    side.saveState() if side is not None else None,
                    side_key=self._side_key,
                )
        super().done(a0)

    def _build_right_pane(self) -> QWidget:
        """Build the right pane from whichever optional sub-panes are enabled.

        Stacks (top→bottom) the player, the sentence picker, and the definition
        browser — only the enabled ones, so the list is anywhere from one to
        three panes long. Always returns a container, never a bare sub-pane:
        the panes are shared widgets (``SubtitlePlayerWidget`` is also the
        subtitle viewer's, ``PageImageView`` carries its own minimum), and a
        wrapper is what lets this screen size its own column without reaching
        into them.

        Stretch and minimum height ride the tuple rather than the position,
        because the composition varies: manga is image + dictionary, and
        positional factors would hand the dictionary the sentence picker's.
        """
        # (name, widget, stretch, minimum height). The names compose the key a
        # saved side-column position is stored under -- see save_curator_layout.
        panes: list[tuple[str, QWidget, int, int]] = []
        row = metric_row_height(self)

        if self._show_player:
            # The audio clip strip rides WITH the player rather than beside it:
            # it edits the clip the player previews, and a splitter pane of its
            # own would give a collapsed one-line disclosure a draggable handle
            # and a share of the column. Wrapping also keeps the pane named
            # "player", so every side-split layout saved before this feature
            # existed still restores (see _side_key below).
            #
            # _build_player_pane is the SOLE construction site and is what sets
            # self.player_widget — hence pane first, stretch read off it after.
            # Building a player here too left a second one parented to the
            # dialog with no layout: Qt painted it at (0, 0) over the header and
            # _stop_player, which releases only self.player_widget, left its mpv
            # core decoding after the dialog was gone.
            pane = self._build_player_pane()
            # Stretch 3: the frame is the reason this column exists, and its
            # own 16:9 floor keeps it honest when the window is short. With no
            # video surface (preview off) the pane is transport controls and a
            # one-line notice, so it must not keep hogging the column — but the
            # pane STAYS, and so does _side_key, which is what lets every saved
            # splitter layout restore unchanged either way.
            # getattr: tests substitute a bare QWidget for the player, and a
            # stub with no surface to report should keep the normal layout.
            stretch = 3 if getattr(self.player_widget, "video_surface_available", True) else 1
            panes.append(("player", pane, stretch, 0))

        if self._show_image:
            # Mutually exclusive with the player in practice (manga has no
            # video), but the panes-list pattern composes either way.
            self.page_image_view = PageImageView()
            panes.append(("image", self.page_image_view, 3, 0))

        if self._has_candidates:
            # Stretch 0: a picker that shows its candidates is done. Extra
            # height belongs to the frame and the definition, so this asks only
            # for its label and three rows.
            panes.append(("sentences", self._build_sentence_pane(), 0, 4 * row))

        if self._show_dict:
            self.definition_view = QTextBrowser()
            self.definition_view.setReadOnly(True)
            self.definition_view.setOpenExternalLinks(False)
            # Glossary markup is authored for a browser; Qt's rich-text engine
            # implements a small CSS subset, so the card's stylesheet is inert here
            # and the pane rendered raw. See services/dictionary/preview_html.py for
            # what Qt does and does not support. Must precede every setHtml().
            document = self.definition_view.document()
            if document is not None:
                document.setDefaultStyleSheet(PREVIEW_CSS)
            panes.append(("dict", self.definition_view, 2, 4 * row))

        self._side_key = "+".join(name for name, _, _, _ in panes)

        container = QWidget()
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(0)

        if len(panes) == 1:
            vbox.addWidget(panes[0][1])
            return container

        self._side_splitter = QSplitter(Qt.Orientation.Vertical)
        self._side_stretch = [stretch for _, _, stretch, _ in panes]
        for index, (_, widget, stretch, minimum) in enumerate(panes):
            if minimum:
                widget.setMinimumHeight(minimum)
            self._side_splitter.addWidget(widget)
            self._side_splitter.setStretchFactor(index, stretch)
        self._side_splitter.setChildrenCollapsible(False)
        vbox.addWidget(self._side_splitter)
        return container

    def _apply_side_split_ratio(self) -> None:
        """Open the side column at the ratio its stretch factors defend.

        Stretch factors govern a *resize*; the first frame comes from each
        pane's own sizeHint, and a ``QListWidget`` asks for more than the video
        frame it sits under. So the opening sizes are stated: a stretch-0 pane
        keeps its minimum and the rest share what is left by weight.
        """
        if not hasattr(self, "_side_splitter"):
            return
        fixed = {
            index: self._side_splitter.widget(index).minimumHeight()  # type: ignore[union-attr]
            for index, stretch in enumerate(self._side_stretch)
            if stretch == 0
        }
        weights = sum(self._side_stretch)
        spare = max(self._side_splitter.height() - sum(fixed.values()), 0)
        self._side_splitter.setSizes(
            [
                fixed.get(index, spare * stretch // weights if weights else 0)
                for index, stretch in enumerate(self._side_stretch)
            ]
        )

    def _build_player_pane(self) -> QWidget:
        """Build the player pane: the video frame plus the audio clip strip.

        The strip is one row — a slider and a play button — and stays open: it
        is small enough that a disclosure only cost a click and hid it.
        """
        self.player_widget = self._create_player_widget()
        # Last-resort release: `finished -> _stop_player` covers every path the
        # user can take, but not a dialog deleted without ever finishing (a tab
        # destroyed outside the shutdown flow) nor an `__init__` that raises
        # after this line. Either leaves a live mpv core whose event thread
        # keeps firing observe_property callbacks into a dead widget.
        #
        # SAFETY CONSTRAINT — the handler must stay Qt-free. By the time
        # `destroyed` is emitted, ~QWidget has ALREADY deleted the children, so
        # a Qt call on the player here is a call on a deleted C++ object. It is
        # safe only because `release()`/`_teardown_player` touches no Qt on this
        # path: the GL render context was already freed by the
        # `aboutToBeDestroyed` net in mpv_video_widget (so `detach()` early-
        # returns instead of calling makeCurrent), and `terminate_mpv_player` is
        # pure python-mpv. Keep it that way; do not grow this into a teardown.
        #
        # The closure captures the WIDGET's own bound method, never `self` — a
        # lambda holding the dialog would keep the object it is meant to be
        # cleaning up after alive. `release()` is idempotent, so a normal
        # `finished` release makes this a free no-op.
        #
        # getattr for the same reason the stretch factor above uses it: tests
        # substitute a bare QWidget for the player. It also keeps the handler
        # total — an exception raised out of a `destroyed` slot reaches the app
        # excepthook mid-destruction, which is strictly worse than the leak.
        release = getattr(self.player_widget, "release", None)
        if release is not None:
            self.destroyed.connect(lambda *_: release())
        self.clip_editor = AudioClipEditor()
        self.clip_editor.clip_changed.connect(self._on_clip_changed)
        self.clip_editor.clip_reset.connect(self._on_clip_reset)
        self.clip_editor.play_requested.connect(self._on_clip_play_requested)
        self.clip_editor.stop_requested.connect(self._on_clip_stop_requested)
        # The player owns the stop: it fires range_finished when the clip ends
        # AND when anything else takes playback over, so the button cannot be
        # left showing "playing" after a Space press moved the user elsewhere.
        # getattr for the same reason the stretch factor above uses it: tests
        # substitute a bare QWidget for the player.
        range_finished = getattr(self.player_widget, "range_finished", None)
        if range_finished is not None:
            range_finished.connect(lambda: self.clip_editor.set_playing(False))
        # Nothing focused yet; the first row-focus seeds it.
        self.clip_editor.clear_word()

        # Named because the Space play/pause shortcut hangs off THIS container,
        # not off self.player_widget: the clip strip and the expansion buttons
        # below are the player's siblings, so a shortcut scoped to the player
        # never reached them and a focused button ate Space instead (#120).
        self.player_pane = QWidget()
        container = self.player_pane
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(SPACING.xs)
        vbox.addWidget(self.player_widget, 1)
        vbox.addWidget(self.clip_editor)
        # Prev/next subtitle-line expansion (Issue #120). Inside this pane on
        # purpose: a new top-level pane would change _side_key and orphan every
        # saved splitter layout. Entry-less contexts (manga, reading) never
        # build the row.
        if self._media_context is not None and self._media_context.subtitle_entries:
            expand_row = QHBoxLayout()
            expand_row.setContentsMargins(0, 0, 0, 0)
            expand_row.setSpacing(SPACING.xs)
            self.expand_prev_button = ModernButton(self.tr("+ Previous line"), variant="ghost")
            self.expand_next_button = ModernButton(self.tr("+ Next line"), variant="ghost")
            self.expand_reset_button = ModernButton(self.tr("Reset lines"), variant="ghost")
            self.expand_prev_button.setToolTip(
                tr_format(
                    self.tr(
                        "Merge the previous subtitle line into this word's sentence and media clip. "
                        "Disabled when there is no earlier line or the combined clip would exceed %1 seconds."
                    ),
                    int(MAX_CLIP_SECONDS),
                )
            )
            self.expand_next_button.setToolTip(
                tr_format(
                    self.tr(
                        "Merge the next subtitle line into this word's sentence and media clip. "
                        "Disabled when there is no later line or the combined clip would exceed %1 seconds."
                    ),
                    int(MAX_CLIP_SECONDS),
                )
            )
            self.expand_reset_button.setToolTip(
                self.tr("Restore this word's original single-line sentence and clip window.")
            )
            self.expand_prev_button.clicked.connect(lambda: self._on_expand_line(-1))
            self.expand_next_button.clicked.connect(lambda: self._on_expand_line(1))
            self.expand_reset_button.clicked.connect(self._on_expand_reset)
            for button in (self.expand_prev_button, self.expand_next_button, self.expand_reset_button):
                button.setEnabled(False)
                expand_row.addWidget(button)
            expand_row.addStretch(1)
            vbox.addLayout(expand_row)
        return container

    # ------------------------------------------------------------------
    # Audio clip strip
    # ------------------------------------------------------------------

    def _seed_clip_editor(self, word: TokenizedWord | None, idx: int | None) -> None:
        """Point the audio clip strip at ``word`` (or nothing when None)."""
        if not hasattr(self, "clip_editor"):
            return
        self._clip_index = idx
        if word is None or idx is None:
            self.clip_editor.clear_word()
            return
        assert self._media_context is not None  # the strip exists only with one
        start, end = word.start_time, word.end_time
        window = self._expanded_window(word, idx)
        if window is not None:
            # Line expansion active: the strip edits the merged window.
            start, end = window.start, window.end
        self.clip_editor.set_word(
            start,
            end,
            self._media_context.audio_padding,
            self._clip_overrides.get(idx),
        )

    def _on_clip_changed(self, start: float, end: float) -> None:
        if self._clip_index is not None:
            self._clip_overrides[self._clip_index] = (start, end)

    def _on_clip_reset(self) -> None:
        if self._clip_index is not None:
            self._clip_overrides.pop(self._clip_index, None)

    def _on_clip_play_requested(self, start: float, end: float) -> None:
        if not self._show_player or not hasattr(self, "player_widget"):
            return
        if not self._chosen_episode_displayed():
            # Season curation: the source swap for the chosen episode is still
            # in flight — playing now would audition this clip window against
            # the previous episode's video.
            self.clip_editor.set_playing(False)
            return
        self.clip_editor.set_playing(True)
        self.player_widget.play_range(start, end)

    def _on_clip_stop_requested(self) -> None:
        if self._show_player and hasattr(self, "player_widget"):
            self.player_widget.pause()  # cancels the range, which resets the button

    # ------------------------------------------------------------------
    # Prev/next subtitle-line expansion (Issue #120)
    # ------------------------------------------------------------------

    def _expansion_entries(self, chosen: TokenizedWord) -> tuple[list[tuple[float, float, str]], float] | None:
        """``(cue entries, offset)`` for the chosen variant's episode, or None.

        Season curation: a variant from another episode resolves through
        ``_media_ctx_cache``; a miss (the context swap is still in flight)
        disables the buttons until ``_apply_media_context`` lands and
        refreshes them.
        """
        ctx = self._media_context
        if ctx is None:
            return None
        video = chosen.video_file or ctx.video_file
        active = ctx if video is None or ctx.video_file == video else self._media_ctx_cache.get(video)
        if active is None or not active.subtitle_entries:
            return None
        return active.subtitle_entries, active.offset

    def _expanded_window(self, chosen: TokenizedWord, idx: int | None) -> MergedLineWindow | None:
        """``idx``'s active expansion as a VIDEO-timeline window, or None when
        inactive or unresolvable (no entries yet, cue unmatched)."""
        if idx is None:
            return None
        expansion = self._line_expansions.get(idx, (0, 0))
        if expansion == (0, 0):
            return None
        resolved = self._expansion_entries(chosen)
        if resolved is None:
            return None
        entries, offset = resolved
        cue = find_cue_index(entries, chosen.start_time, chosen.sentence, offset=offset)
        if cue is None:
            return None
        window = merge_cue_window(entries, cue, *expansion)
        # Entries are raw-timeline (the context parser zeroes the offset);
        # word timings are raw+offset, with the same max(0, ...) clamp.
        return MergedLineWindow(
            start=max(0.0, window.start + offset),
            end=max(0.0, window.end + offset),
            text=window.text,
            prefix_len=window.prefix_len,
        )

    def _on_expand_line(self, direction: int) -> None:
        word, idx = self._pending_word, self._pending_index
        if word is None or idx is None:
            return
        chosen = self._chosen.get(idx, word)
        prev_count, next_count = self._line_expansions.get(idx, (0, 0))
        if direction < 0:
            prev_count += 1
        else:
            next_count += 1
        self._line_expansions[idx] = (prev_count, next_count)
        self._apply_expansion(chosen, idx, snap=direction < 0)

    def _on_expand_reset(self) -> None:
        word, idx = self._pending_word, self._pending_index
        if word is None or idx is None:
            return
        self._line_expansions.pop(idx, None)
        self._apply_expansion(self._chosen.get(idx, word), idx, snap=True)

    def _apply_expansion(self, chosen: TokenizedWord, idx: int, *, snap: bool) -> None:
        """Shared add/reset tail: drop the stale clip override (the
        sentence-pick precedent — it was measured against the old window),
        reseed the strip, repaint the sentence cell, refresh button states,
        and optionally snap the preview to the (new) start."""
        self._clip_overrides.pop(idx, None)
        self._seed_clip_editor(chosen, idx)
        window = self._expanded_window(chosen, idx)
        display = chosen if window is None else dataclasses.replace(chosen, sentence=window.text)
        self._apply_pick_to_row(idx, display)
        self._refresh_expansion_buttons()
        if snap:
            start = window.start if window is not None else chosen.start_time
            video = chosen.video_file
            # Defer: clicked handlers run mid-event — see _on_candidate_chosen.
            QTimer.singleShot(0, lambda: self._preview_scene(start, video))

    def _refresh_expansion_buttons(self) -> None:
        """Recompute the three expansion buttons' enabled states.

        A direction disables when no cue exists that way or when the would-be
        merged window plus audio padding would exceed the clip strip's
        MAX_CLIP_SECONDS — the single guardrail against merging across a long
        cue gap. Enforced only here, at stamp time: the processor materializes
        the stamped counts verbatim, so what the preview promised is what the
        card gets.
        """
        if not hasattr(self, "expand_prev_button"):
            return
        word, idx = self._pending_word, self._pending_index
        prev_ok = next_ok = reset_ok = False
        if word is not None and idx is not None and self._media_context is not None:
            reset_ok = self._line_expansions.get(idx, (0, 0)) != (0, 0)
            chosen = self._chosen.get(idx, word)
            resolved = self._expansion_entries(chosen)
            if resolved is not None:
                entries, offset = resolved
                cue = find_cue_index(entries, chosen.start_time, chosen.sentence, offset=offset)
                if cue is not None:
                    prev_count, next_count = self._line_expansions.get(idx, (0, 0))
                    padding = self._media_context.audio_padding
                    if cue - (prev_count + 1) >= 0:
                        window = merge_cue_window(entries, cue, prev_count + 1, next_count)
                        prev_ok = (window.end - window.start) + 2 * padding <= MAX_CLIP_SECONDS
                    if cue + next_count + 1 < len(entries):
                        window = merge_cue_window(entries, cue, prev_count, next_count + 1)
                        next_ok = (window.end - window.start) + 2 * padding <= MAX_CLIP_SECONDS
        self.expand_prev_button.setEnabled(prev_ok)
        self.expand_next_button.setEnabled(next_ok)
        self.expand_reset_button.setEnabled(reset_ok)

    def _build_sentence_pane(self) -> QWidget:
        """Build the "Sentences" picker pane (label + candidate list).

        The list is repopulated on row focus with the focused word's candidate
        sentences; selecting one rewrites which sentence/scene gets mined.

        The label carries the option count because the list is unbounded and
        scrolls: without it, a word offering 30 lines looks like it offers the
        five that happen to fit the pane.
        """
        container = QWidget()
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(SPACING.xs)

        self.sentence_pane_label = QLabel(self.tr("Sentences"))
        self.sentence_pane_label.setFont(self._make_font(12, QFont.Weight.Medium))
        vbox.addWidget(self.sentence_pane_label)

        self.sentence_list = QListWidget()
        self.sentence_list.setWordWrap(True)
        # Same surface as the word table beside it (D42). Candidates stay in
        # occurrence order, so sorting is not enabled; copy lifts the sentence.
        configure_data_view(self.sentence_list)
        # The gutter stays reserved: candidate counts swing per focused word, and
        # a scrollbar that comes and goes changes the viewport width, re-wrapping
        # every word-wrapped row left/right on each focus change.
        self.sentence_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        install_copy_rows(self.sentence_list)
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
        its children) has focus — never the Search box. Installed on the table and
        on each preview pane the user clicks into (the player PANE, the sentence
        picker, and the dictionary), so Space keeps reaching the player after focus
        leaves the table. A window-scoped shortcut can't be used: it would swallow
        spaces typed in the Search box (Issue #55).

        For the player it must be the pane container, never ``self.player_widget``:
        the clip strip and the prev/next line buttons are the player's siblings, and
        a ``QPushButton`` that holds focus activates on Space unless a shortcut in
        scope claims the key first (#120). Never install on both — two matching
        WidgetWithChildren shortcuts in one ancestry chain fire
        ``activatedAmbiguously`` and nothing happens at all.
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
        if self._show_player and hasattr(self, "player_pane"):
            # The PANE, not self.player_widget. Installing on both would put two
            # WidgetWithChildren Space shortcuts in one ancestry chain, which Qt
            # resolves as activatedAmbiguously — neither fires and Space dies.
            self._install_play_pause_shortcut(self.player_pane)
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
        """Space: toggle player play/pause (no-op when the player pane is hidden,
        or while a season-curation source swap is still in flight)."""
        if self._show_player and hasattr(self, "player_widget") and self._chosen_episode_displayed():
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

            # Word (mined), Form in subtitle, Reading and Sentence all describe
            # one occurrence, so they are built from the single spec the
            # sentence picker also repaints through (_pick_cell_values). Nothing
            # is picked yet at populate time, so the word is its own variant.
            for column, text, tooltip, copy_text in self._pick_cell_values(word, word):
                self.table.setItem(
                    row,
                    column,
                    self._make_readonly_item(text, tooltip=tooltip, copy_text=copy_text, japanese=True),
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
        tall as the shared data-surface rule made it. No cell may pin a size —
        the density is what makes this table scannable.
        """
        item = make_table_item(text, role, sort_value=sort_value, copy_text=copy_text, tooltip=tooltip)
        item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        if japanese:
            item.setFont(japanese_cell_font())
        return item

    def _pick_cell_values(self, word: TokenizedWord, chosen: TokenizedWord) -> tuple[tuple[int, str, str, str], ...]:
        """``(column, text, tooltip, copy_text)`` for every cell the pick decides.

        The single source of the row's display formulas: :meth:`_populate_table`
        builds cells from it and :meth:`_apply_pick_to_row` repaints cells from
        it, so the two can't drift.

        ``word`` is the primary occurrence — it owns ``sentence_candidates``, so
        the "(N)" badge is counted off it. ``chosen`` is the variant the user
        picked under "Sentences" (``word`` itself until they pick one), and every
        value the row prints comes off it: ``_swap_word_to_line`` rebuilds
        ``surface`` per candidate line, and for surface-mined POS (nouns)
        ``mined_form`` IS the surface, so both move with the pick.

        ``reading`` is not swapped today, so column 3 is a no-op. It stays in the
        spec anyway because the row's contract is "columns 1-4 are the chosen
        variant" — leaving one column out is exactly how the row went half stale
        in the first place (Issue #108 was that leak on ``surface`` alone).
        """
        n_candidates = len(word.sentence_candidates)
        return (
            # Word (mined) — what becomes the Anki Expression (source-orthography
            # dictionary form for verbs/adjectives, surface for nouns).
            (1, chosen.mined_form, chosen.mined_form, chosen.mined_form),
            # Form in subtitle — the raw surface as it appeared.
            (2, chosen.surface, chosen.surface, chosen.surface),
            # Reading.
            (3, chosen.reading, chosen.reading, chosen.reading),
            # Sentence, truncated for the cell but copied and hovered in full.
            # A trailing "(N)" flags words with N alternative example sentences.
            (
                4,
                self._sentence_display(chosen.sentence, n_candidates),
                self._sentence_tooltip(chosen.sentence, n_candidates),
                chosen.sentence,
            ),
        )

    @staticmethod
    def _sentence_display(sentence: str, n_candidates: int) -> str:
        """Truncated sentence for the table cell, with a candidate-count badge."""
        display = sentence if len(sentence) <= 50 else sentence[:47] + "..."
        return f"{display}  ({n_candidates})" if n_candidates > 1 else display

    def _sentence_tooltip(self, sentence: str, n_candidates: int) -> str:
        """Full sentence tooltip, hinting at the picker when alternatives exist."""
        if n_candidates > 1:
            return tr_format(
                self.tr("%1\n\n(%2 example sentences to pick from — focus the row, then choose one under “Sentences”)"),
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
                value = cell.data(COPY_ROLE) if cell and col == 4 else cell.text() if cell else ""
                if text_lower in str(value).lower():
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

        The target/position summary is pure string work, so it updates immediately:
        on the app's most keyboard-driven screen it must answer the arrow key, not
        the debounce timer. Only the expensive panes (player seek, page decode,
        dictionary lookup) go through the timer.

        MUST NOT read or write checkbox state; checkbox changes are handled by
        _on_item_changed (itemChanged signal) and kept independent.
        """
        self._refresh_summary()

        word, original_index = self._focused_word()
        if word is None or original_index is None:
            # Nothing focused: leave the timer alone rather than debouncing a
            # seek/lookup for a row that isn't there.
            return

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
        # so the seek can be issued directly — see _on_candidate_chosen. An
        # active line expansion previews its merged window's start instead.
        window = self._expanded_window(chosen, idx)
        self._preview_scene(window.start if window is not None else chosen.start_time, chosen.video_file)

        # Audio clip strip: the CHOSEN variant's window, for the same reason the
        # dictionary follows the pick — the strip edits the clip this row will
        # actually mine.
        self._seed_clip_editor(chosen, idx)

        # Dictionary pane: the CHOSEN variant, not the primary — for surface-mined
        # POS (nouns) the pick moves mined_form, so a word-keyed pane showed the
        # first occurrence's entry after the user picked another (Issue #108).
        self._refresh_definition(chosen)

        # Line-expansion buttons follow the focused word.
        self._refresh_expansion_buttons()

    def _refresh_definition(self, word: TokenizedWord) -> None:
        """Point the definition pane at ``word``'s card front.

        Looks up by ``mined_form`` (the card-front spelling, the same primary key
        Phase 4 uses) with a miss-only lemma retry — unidic's canonical lemma
        collapses kanji variants (殺る → 遣る), so a lemma-keyed pane showed the
        wrong homograph's entry.

        Called from the focus debounce and, directly, from the sentence pick.
        The pick does not need a debounce of its own: :meth:`_lookup_and_render`
        already keeps one request in flight with only the newest queued behind
        it, and paints only the generation that is still current — so holding a
        key down in the picker cannot pile up or paint a superseded entry.
        """
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

        ``fallback_term`` (the token's lemma) does double duty. First, it
        SCOPES the primary ``term`` lookup (Rule A′): when it differs from
        ``term``, it is threaded to ``lookup_fn`` as the lemma so a kana front
        (mined_form ゆう, lemma 言う) keeps its own lexeme's entry instead of
        every same-reading homograph (有/夕/結う) — matching the card's own
        ``get_definitions_batch(lemma_context=...)`` scope, so the pane beside
        the card agrees with it. Second, it is the MISS-only retry: unidic's
        canonical lemma collapses kanji variants (殺る → 遣る), so on a ``term``
        miss it is looked up as its own (unscoped) term. Both terms are
        fetched inside the one background job, keeping both off the GUI
        thread.
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

    @staticmethod
    def _scope_lemma(term: str, fallback_term: str | None) -> str | None:
        """The Rule A′ scope for ``term``'s own lookup: ``fallback_term`` (the
        token's lemma) when it differs from ``term``, else ``None`` — the
        non-empty convention every lemma-threading call site in this codebase
        shares, so a word whose mined_form already IS its lemma calls
        ``lookup_fn`` arity-1 exactly as before. Also the ``_lookup_cache``
        key discriminant (see its declaration in ``__init__``).
        """
        return fallback_term if fallback_term and fallback_term != term else None

    def _cached_entries(self, term: str, fallback_term: str | None) -> list[tuple[str, str]] | None:
        """Entries resolvable from the cache alone, or ``None`` if a fetch is needed.

        An empty list is a real answer (a cached miss), which is why the
        "unresolved" signal is ``None`` rather than falsiness.
        """
        key = (term, self._scope_lemma(term, fallback_term))
        if key not in self._lookup_cache:
            return None
        entries = self._lookup_cache[key]
        if entries or not fallback_term or fallback_term == term:
            return entries
        fallback_key = (fallback_term, None)
        if fallback_key not in self._lookup_cache:
            return None
        return self._lookup_cache[fallback_key]

    def _dispatch_lookup(self, term: str, fallback_term: str | None, gen: int) -> None:
        """Run the (possibly two-term) query on a worker thread."""
        lookup_fn = self._lookup_fn
        assert lookup_fn is not None  # guarded by self._show_dict
        self._lookup_inflight = True

        def work() -> dict[tuple[str, str | None], list[tuple[str, str]]]:
            scope_lemma = self._scope_lemma(term, fallback_term)
            key = (term, scope_lemma)
            fetched = {key: lookup_fn(term, scope_lemma) if scope_lemma else lookup_fn(term)}
            if not fetched[key] and fallback_term and fallback_term != term:
                fetched[(fallback_term, None)] = lookup_fn(fallback_term)
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
        # _closing is the teardown gate: reject before mutating state; gen staleness alone must still clear/drain below.
        is_gen_current = gen == self._lookup_gen
        if self._closing:
            return
        self._lookup_inflight = False
        # Cache even a superseded result: it was a correct answer for its term,
        # and scrolling back to that row must not re-query.
        if isinstance(fetched, dict):
            self._lookup_cache.update(fetched)
        if is_gen_current:
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

        self.definition_view.setHtml(to_preview_html(entries))

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
            # Season curation: when candidates span several episodes, prefix
            # each row with its episode's filename stem so the user can tell
            # which episode a line (and its scene) comes from.
            multi_episode = len({c.video_file for c in candidates if c.video_file is not None}) > 1
            selected_row = 0
            for i, cand in enumerate(candidates):
                text = cand.sentence
                if multi_episode and cand.video_file is not None:
                    text = f"[{cand.video_file.stem}] {cand.sentence}"
                # Display text carries BudouX word joiners so the row wraps at
                # phrase boundaries; COPY_ROLE and the tooltip keep the pristine
                # string so Ctrl+C never lifts an invisible character.
                list_item = QListWidgetItem(phrase_wrap_ja(text))
                list_item.setToolTip(text)
                list_item.setData(COPY_ROLE, text)
                list_item.setFont(japanese_cell_font())
                self.sentence_list.addItem(list_item)
                if self._same_pick(cand, chosen):
                    selected_row = i
            self.sentence_list.setCurrentRow(selected_row)
            self.sentence_list.setEnabled(True)
            self._set_sentence_pane_count(len(candidates))
        else:
            self.sentence_list.setEnabled(False)
            self._set_sentence_pane_count(0)

        self.sentence_list.blockSignals(False)
        self._populating_candidates = False

    def _set_sentence_pane_count(self, count: int) -> None:
        """Title the picker with how many lines it is offering (blank when off)."""
        if not hasattr(self, "sentence_pane_label"):
            return
        if count > 1:
            self.sentence_pane_label.setText(tr_format(self.tr("Sentences (%1)"), count))
        else:
            self.sentence_pane_label.setText(self.tr("Sentences"))

    @staticmethod
    def _same_pick(a: TokenizedWord, b: TokenizedWord) -> bool:
        """Whether two variants refer to the same example line.

        Sentence + timing + episode: season curation can hold identical lines
        at identical timestamps in different episodes (OP/ED lyrics), and the
        episode is what tells them apart.
        """
        return a.sentence == b.sentence and a.start_time == b.start_time and a.video_file == b.video_file

    def _on_candidate_chosen(self, list_row: int) -> None:
        """Apply the user's sentence pick: record it, refresh the row, seek the scene."""
        if self._populating_candidates or list_row < 0:
            return
        idx = self._candidate_list_index
        if idx is None or not (0 <= list_row < len(self._candidate_list_words)):
            return
        chosen = self._candidate_list_words[list_row]
        self._chosen[idx] = chosen

        # A clip window was measured against the OLD line's timings, so the pick
        # invalidates it: dropping the override and reseeding from the new
        # variant's default is the only reading that cannot mine a window
        # belonging to a different scene. A line expansion was counted against
        # the old cue for the same reason, so it dies with the pick too.
        self._clip_overrides.pop(idx, None)
        self._line_expansions.pop(idx, None)
        self._seed_clip_editor(chosen, idx)

        # Everything that describes the occurrence follows the pick, not just the
        # sentence: the mined word, the form in the subtitle, what a row copy
        # yields (the COPY_ROLE payload, Issue #95 on the row-copy path), and the
        # definition beside it (Issue #108).
        self._apply_pick_to_row(idx, chosen)
        self._refresh_definition(chosen)
        self._refresh_expansion_buttons()

        # Preview the chosen scene. Defer the seek to the next event-loop tick:
        # this handler runs synchronously inside the list's currentRowChanged
        # emission (mid mouse-press), and an in-event setPosition+pause doesn't
        # reliably present the new frame — it took a couple of clicks to land.
        # The word-focus path already seeks from a (debounce) timer, i.e. outside
        # any active event handler; deferring here makes the two paths identical.
        start_time = chosen.start_time
        chosen_video = chosen.video_file
        QTimer.singleShot(0, lambda: self._preview_scene(start_time, chosen_video))

    def _apply_pick_to_row(self, idx: int, chosen: TokenizedWord) -> None:
        """Repaint every pick-dependent cell of ``idx``'s row from ``chosen``.

        The row is found by original index, not by position: the table is
        sortable, so the visual row is not ``idx``.

        Sorting is SUSPENDED for the batch, not merely signal-blocked, and that
        is load-bearing. Blocking the table's signals does not stop the re-sort —
        ``ensureSorted`` runs off the *model's* ``dataChanged`` — so if the sort
        indicator sits on one of these columns, the first of the four writes
        moves the row and the remaining three land on whatever word slid into the
        old position. Suspending sorting pins the row for the batch and re-sorts
        once, on the final values.

        The signal block is released only AFTER sorting is restored: the
        relocation's ``currentCellChanged`` would otherwise restart the focus
        debounce, whose handler clears and rebuilds the very ``sentence_list``
        the user is standing in. The two things the block costs us — scroll
        visibility and the position counter — are restored explicitly below.

        Note the search filter is deliberately NOT re-run: ``_apply_search``
        reads these same columns, so re-running it would hide or reveal rows
        under the user mid-pick. A momentarily stale filter self-corrects on the
        next keystroke; a row vanishing under the cursor does not.
        """
        row = self._visual_row_for_index(idx)
        if row is None:
            return
        word = self._words[idx]
        sorting = self.table.isSortingEnabled()
        self.table.blockSignals(True)
        self.table.setSortingEnabled(False)
        try:
            for column, text, tooltip, copy_text in self._pick_cell_values(word, chosen):
                item = self.table.item(row, column)
                if item is not None:
                    update_table_item(item, text, tooltip=tooltip, copy_text=copy_text)
        finally:
            if sorting:
                self.table.setSortingEnabled(True)
                # Re-enabling sorting resets the vertical header's resize mode to
                # Interactive, which drops the shared Fixed row height — same
                # reason _populate_table re-applies it after its own toggle.
                self._apply_data_surface()
            self.table.blockSignals(False)

        # The re-sort may have moved the row. Qt remaps persistent indexes, so
        # the cursor is still on this word; only the scroll position and the
        # counter need catching up, their signals having been swallowed above.
        moved_to = self._visual_row_for_index(idx)
        if moved_to is not None and moved_to != row:
            anchor = self.table.item(moved_to, 0)
            if anchor is not None:
                self.table.scrollToItem(anchor, QAbstractItemView.ScrollHint.EnsureVisible)
        self._refresh_summary()

    def _preview_scene(self, start_time: float, video_file: Path | None = None) -> None:
        """Preview the scene for ``start_time``: seek the player / show the page.

        The single funnel for both the debounced focus path and the sentence
        candidate pick path — for manga, ``int(start_time)`` is the reading
        unit index (the parser stamps ``start_time = float(unit.index)``).

        ``video_file`` is the chosen variant's episode (season curation): when
        it differs from the displayed one the player source is swapped first.
        ``_ensure_player_source`` returning False means an off-thread context
        resolve is in flight and will re-fire this preview itself.
        """
        if self._closing:
            return
        if self._show_player and hasattr(self, "player_widget") and self._ensure_player_source(video_file):
            self.player_widget.seek_seconds(start_time)
            self.player_widget.pause()
        if self._show_image:
            self._request_page_image(int(start_time))

    def _ensure_player_source(self, video_file: Path | None) -> bool:
        """Point the player at ``video_file``'s episode (season curation).

        True → the player already shows (or is now synchronously loading) the
        right episode; the caller may seek immediately, because
        ``seek_seconds`` self-defers a pre-file-loaded seek via its pending-
        seek mechanism. False → an off-thread resolve is in flight; its
        callback re-fires the preview. Single-episode dialogs (no resolver)
        always return True.
        """
        ctx = self._media_context
        if video_file is None or ctx is None or ctx.context_resolver is None:
            return True
        if video_file == self._displayed_media_video:
            return True
        cached = self._media_ctx_cache.get(video_file)
        if cached is not None:
            self._apply_media_context(cached)
            return True
        resolver = ctx.context_resolver
        self._media_swap_gen += 1
        gen = self._media_swap_gen

        def on_done(result: object) -> None:
            new_ctx = result if isinstance(result, CurationMediaContext) else None
            if self._closing or gen != self._media_swap_gen:
                return
            if new_ctx is None:
                # Table-only degradation for this word: keep the current
                # episode showing rather than blocking curation on media.
                logger.warning("Season curation: no media context for %s", video_file.name)
                return
            self._media_ctx_cache[video_file] = new_ctx
            self._apply_media_context(new_ctx)
            # Re-fire the preview for the still-focused word (its chosen
            # variant may have moved on; only seek if it still matches).
            word, idx = self._pending_word, self._pending_index
            if word is not None and idx is not None:
                chosen = self._chosen.get(idx, word)
                if chosen.video_file == video_file:
                    self._preview_scene(chosen.start_time, video_file)

        def on_error(message: str) -> None:
            logger.warning(
                "Season curation: media context build failed for %s: %s",
                video_file.name,
                message,
            )

        run_off_thread(self, lambda: resolver(video_file), on_done, on_error)
        return False

    def _apply_media_context(self, ctx: CurationMediaContext) -> None:
        """Swap the player to ``ctx``'s episode (source + subtitle overlay)."""
        self.player_widget.set_source(
            ctx.video_file,  # type: ignore[arg-type]  # resolver never maps to None videos
            ctx.subtitle_entries,
            ctx.offset,
            audio_track_override=ctx.audio_track_override,
        )
        self._displayed_media_video = ctx.video_file
        # The landed context may make the focused word's neighbors resolvable.
        self._refresh_expansion_buttons()

    def _chosen_episode_displayed(self) -> bool:
        """Whether the player shows the focused word's episode.

        Always True for single-episode dialogs. Season curation gates the
        clip audition and Space play on this: while a source swap is still in
        flight, playing would audition the chosen clip window against the
        PREVIOUS episode's video.
        """
        word, idx = self._pending_word, self._pending_index
        if word is None or idx is None:
            return True
        chosen = self._chosen.get(idx, word)
        return chosen.video_file is None or chosen.video_file == self._displayed_media_video

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
            pixmap, _byte_count = cached
            self.page_image_view.show_page(pixmap, box, caption)
            return

        def on_done(image: object) -> None:
            # Gen check FIRST: it reads only a plain Python attribute, so a
            # late result after dialog teardown returns before touching any
            # Qt object (teardown bumps the generation).
            if gen != self._page_request_gen:
                return
            assert isinstance(image, QImage)
            pixmap = QPixmap.fromImage(image)
            self._page_cache[ref] = (pixmap, image.sizeInBytes())
            while (
                len(self._page_cache) > _PAGE_CACHE_CAP
                or sum(byte_count for _, byte_count in self._page_cache.values()) > _PAGE_CACHE_MAX_BYTES
            ):
                self._page_cache.popitem(last=False)
            self.page_image_view.show_page(pixmap, box, caption)

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
        self._known_commit_gen += 1
        self._known_commit_running = False
        self._cancel_known_commit_worker()
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

        copy_word_action = menu.addAction(self.tr("Copy word"))
        copy_sentence_action = menu.addAction(self.tr("Copy sentence"))

        vp = self.table.viewport()
        if vp is None:
            return
        action = menu.exec(vp.mapToGlobal(pos))
        clipboard = QApplication.clipboard()
        if clipboard is None:
            return
        # BOTH actions read the user's sentence pick, else the default — the same
        # "chosen, else original" pattern as get_selected_words. Resolving it for
        # the sentence alone is what left the menu handing back the primary
        # occurrence's word (Issue #95 on the sentence, then the same leak on the
        # word): for surface-mined POS (nouns) _swap_word_to_line rebuilds surface
        # per candidate line, and mined_form IS the surface, so the pick moves
        # column 1 too — see _pick_cell_values.
        chosen = self._chosen.get(original_index, word)
        if action == copy_word_action:
            # mined_form, not lemma: it is what column 1 shows and what becomes
            # the card front. unidic's lemma collapses kanji variants (想う→思う,
            # こと→事), so copying it handed back a different word than the one
            # being mined (Issue #107).
            clipboard.setText(chosen.mined_form)
        elif action == copy_sentence_action:
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
        """Stage the target rows for the local known/ignore list, or unstage them (D34-B).

        Writes NOTHING in either direction. Staged rows are marked
        "Known · pending", greyed and excluded from this run; :meth:`accept`
        commits the stage, and every other exit throws it away with the rest of
        the review. The previous behaviour wrote immediately, so a Cancel that
        abandoned the run still excluded those words from every future one.

        The direction is decided by the target rows, not by a mode. Any row
        still active means "add", and a mixed selection stages the rest — the
        additive reading of a button whose label says Add. Only when EVERY
        target row is already staged does the click take the mark back, which is
        the moment the label flips (:meth:`_refresh_known_button` mirrors this
        rule; if the two drift the button lies about what it does).

        Undo has to live here because Cancel is not one: it discards the whole
        review, and MiningTabBase reads a rejected curator as "stop the run".
        """
        if self._commit_known_callback is None or self._known_commit_running:
            return
        targets = self._known_target_rows()
        active = [row for row in targets if self._row_is_active(row)]
        if active:
            self._stage_rows_known(active)
        elif targets:
            self._unstage_rows_known(targets)

    def _stage_rows_known(self, rows: list[int]) -> None:
        """Mark rows Known · pending and re-derive the stage."""
        self.table.blockSignals(True)
        for row in rows:
            self._mark_row_known(row)
        self.table.blockSignals(False)
        self._recompute_pending_known()
        self._refresh_summary()

    def _unstage_rows_known(self, rows: list[int]) -> None:
        """Take the Known · pending mark back off rows and re-derive the stage."""
        self.table.blockSignals(True)
        for row in rows:
            self._unmark_row_known(row)
        self.table.blockSignals(False)
        self._recompute_pending_known()
        self._refresh_summary()

    def pending_known_forms(self) -> set[str]:
        """Mined forms staged for the known list but not yet written."""
        return set(self._pending_known_forms)

    # ------------------------------------------------------------------
    # Confirm / Cancel — the Known Words stage commits here, or nowhere
    # ------------------------------------------------------------------

    def accept(self) -> None:
        """Commit the staged Known Words, then release the card selection (D34-B).

        With nothing staged this is the plain dialog accept. With a stage, the
        write happens FIRST and the dialog stays open until it succeeds: the
        selection must never reach the pipeline on the back of a write that
        failed, or the user would get cards for words they just declared known
        and lose the marks at the same time.
        """
        if self._known_commit_running:
            return  # a second Ctrl+Enter while the write is in flight
        forms = set(self._pending_known_forms)
        if not forms or self._commit_known_callback is None:
            super().accept()
            return
        self._begin_known_commit(forms)

    def reject(self) -> None:
        """Discard the staged marks along with the rest of the review.

        Blocked only while a commit is in flight, where the answer is neither
        "kept" nor "discarded" yet. Shutdown reaches past this through
        :meth:`force_reject`.
        """
        if self._known_commit_running:
            return
        super().reject()

    def closeEvent(self, a0: QCloseEvent | None) -> None:  # noqa: N802 - Qt override
        """Refuse the window X while a Known Words commit is in flight."""
        if self._known_commit_running:
            if a0 is not None:
                a0.ignore()
            return
        super().closeEvent(a0)

    def force_reject(self) -> None:
        """Reject even mid-commit — the forced path for teardown and shutdown.

        A write already running may still land (it is a single sqlite
        transaction with no cancellation point), but the review is abandoned
        and no card selection is released. Bumping the generation is what stops
        a late success from calling ``accept`` on a dialog nobody is waiting on.
        """
        self._known_commit_gen += 1
        self._known_commit_running = False
        self._cancel_known_commit_worker()
        self._set_decision_enabled(True)
        super().reject()

    def _cancel_known_commit_worker(self) -> None:
        """Silence the commit worker if it has not emitted yet. Detach is teardown's job."""
        worker = self._known_commit_worker
        self._known_commit_worker = None
        if worker is not None:
            with contextlib.suppress(RuntimeError):
                worker.cancel()

    def _set_decision_enabled(self, enabled: bool) -> None:
        """Enable/disable every control that would resolve or extend the review."""
        for button in (self.confirm_button, self.cancel_button):
            button.setEnabled(enabled)
        self.add_known_button.setEnabled(enabled and self._commit_known_callback is not None)

    def _begin_known_commit(self, forms: set[str]) -> None:
        """Write the staged forms off the GUI thread; accept only on success."""
        callback = self._commit_known_callback
        if callback is None:  # pragma: no cover - guarded by the caller
            return
        self.clear_screen_issue()
        self._known_commit_gen += 1
        self._known_commit_running = True
        self._set_decision_enabled(False)
        gen = self._known_commit_gen
        self._known_commit_worker = run_off_thread(
            self,
            lambda: callback(forms),
            partial(self._on_known_commit_done, gen),
            partial(self._on_known_commit_failed, gen),
        )

    def _on_known_commit_done(self, gen: int, _result: object) -> None:
        if gen != self._known_commit_gen or self._closing:
            return
        self._known_commit_worker = None
        self._known_commit_running = False
        self._pending_known_forms.clear()
        super().accept()

    def _on_known_commit_failed(self, gen: int, message: str) -> None:
        if gen != self._known_commit_gen or self._closing:
            return
        self._known_commit_worker = None
        self._known_commit_running = False
        self._set_decision_enabled(True)
        logger.error("Known Words commit failed, review left open: %s", message)
        self.show_screen_issue(
            ScreenIssue(
                summary=self.tr(
                    "Your Known Words could not be saved, so no cards were created. "
                    "Confirm again to retry, or Cancel to discard the pending marks."
                ),
                details=message,
            )
        )

    def _row_is_active(self, row: int) -> bool:
        """Whether a row hasn't already been marked known (checkbox still toggles)."""
        item = self.table.item(row, 0)
        return item is not None and self._is_checkable(item)

    def _mark_row_known(self, row: int) -> None:
        """Mark a row staged-known: labelled, struck through, grey, unchecked, locked."""
        check_item = self.table.item(row, 0)
        if check_item:
            # Remembered BEFORE the uncheck, so _unmark_row_known can put back
            # what the user chose instead of a default.
            self._known_prior_check[check_item.data(Qt.ItemDataRole.UserRole)] = check_item.checkState()
            check_item.setCheckState(Qt.CheckState.Unchecked)
            # Strip the checkable flag so bulk actions / the S toggle key can't re-include it.
            check_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            # "pending" is the whole point: nothing has been written yet, and
            # Cancel will discard this. The include column is ResizeToContents,
            # so it widens to fit the label and shrinks back without it.
            check_item.setText(self.tr("Known · pending"))
        grey = QColor(128, 128, 128)
        for col in range(1, self.table.columnCount()):
            item = self.table.item(row, col)
            if item:
                font = item.font()
                font.setStrikeOut(True)
                item.setFont(font)
                item.setForeground(grey)

    def _unmark_row_known(self, row: int) -> None:
        """Undo :meth:`_mark_row_known` — the row rejoins the review.

        The exact inverse, cell for cell. The checkable flag comes back, so the
        bulk verbs and the S key can reach the row again; the "Known · pending"
        label goes, and column 0's ResizeToContents rule shrinks the column back
        on its own.

        The foreground is CLEARED rather than repainted a colour.
        ``make_table_item`` never sets one, so an empty ForegroundRole is the
        state a fresh row is in — and a hard-coded black would survive a theme
        change into an unreadable cell.
        """
        check_item = self.table.item(row, 0)
        if check_item:
            check_item.setFlags(
                Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
            )
            check_item.setText("")
            prior = self._known_prior_check.pop(check_item.data(Qt.ItemDataRole.UserRole), Qt.CheckState.Checked)
            check_item.setCheckState(prior)
        for col in range(1, self.table.columnCount()):
            item = self.table.item(row, col)
            if item:
                font = item.font()
                font.setStrikeOut(False)
                item.setFont(font)
                item.setData(Qt.ItemDataRole.ForegroundRole, None)

    def _recompute_pending_known(self) -> None:
        """Re-derive the stage from the table, which is its single source of truth.

        Stripping the checkable flag is what MAKES a row staged, so the marked
        rows ARE the stage. Re-reading them is cheaper to keep correct than
        reference-counting forms, and it means two rows printing the same mined
        form cannot have one unstage silently clear both.
        """
        forms: set[str] = set()
        for row in range(self.table.rowCount()):
            check_item = self.table.item(row, 0)
            if check_item is None or self._is_checkable(check_item):
                continue
            word_item = self.table.item(row, 1)  # "Word (mined)" column
            if word_item:
                forms.add(word_item.text())
        self._pending_known_forms = forms

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
        self._refresh_known_button()

    def _known_button_labels(self) -> tuple[str, str]:
        """The Known Words verb's two faces: ``(stage, unstage)``.

        One place, because the width pin in :meth:`_build_toolbar_row` measures
        both and :meth:`_refresh_known_button` picks between them.
        """
        return (self.tr("Add to Known Words"), self.tr("Remove from Known Words"))

    def _refresh_known_button(self) -> None:
        """Name the click this button is about to perform, for the current target.

        Mirrors :meth:`_on_add_to_known`'s rule exactly — any active target row
        means "add", every target already staged means "remove". The label is
        the only statement of which of the two a click will do, so the two rules
        are written to be read together.
        """
        add_label, remove_label = self._known_button_labels()
        targets = self._known_target_rows()
        removing = bool(targets) and not any(self._row_is_active(row) for row in targets)
        if removing:
            self.add_known_button.setText(remove_label)
            self.add_known_button.setToolTip(
                self.tr("Take the Known · pending mark back off the highlighted rows and return them to this review.")
            )
        else:
            self.add_known_button.setText(add_label)
            self.add_known_button.setToolTip(
                self.tr("Mark highlighted rows Known · pending. Confirm saves them; Cancel discards them.")
            )
        self.add_known_button.setAccessibleName(self.add_known_button.text())

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

        A word whose audio clip window was edited is returned as a COPY carrying
        that window. The copy matters: variants come from the shared
        ``sentence_candidates`` list, and stamping an override onto one in place
        would attach this run's edit to an object the filter service still owns.
        """
        selected = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item and item.checkState() == Qt.CheckState.Checked:
                original_index = item.data(Qt.ItemDataRole.UserRole)
                if original_index is not None and 0 <= original_index < len(self._words):
                    word = self._chosen.get(original_index, self._words[original_index])
                    override = self._clip_overrides.get(original_index)
                    expansion = self._line_expansions.get(original_index, (0, 0))
                    if override is not None or expansion != (0, 0):
                        word = dataclasses.replace(
                            word,
                            clip_override=override if override is not None else word.clip_override,
                            line_expansion=expansion,
                        )
                    selected.append(word)
        return selected
