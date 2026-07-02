"""Dialog for curating words before card creation."""

from __future__ import annotations

import html
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from anki_miner.gui.widgets.subtitle_player_widget import SubtitlePlayerWidget

from PyQt6.QtCore import QPoint, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
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
from anki_miner.gui.resources.styles.theme import Theme
from anki_miner.gui.utils.fonts import make_scaled_font
from anki_miner.gui.widgets.enhanced import ModernButton
from anki_miner.models import TokenizedWord
from anki_miner.utils.i18n import tr_format


@dataclass(frozen=True)
class CurationMediaContext:
    """Media context for the word curation dialog's embedded player.

    Carries the video source and pre-parsed subtitle entries so the dialog
    can seek to the correct frame when the user focuses a word row.
    """

    video_file: Path | None
    subtitle_entries: list[tuple[float, float, str]]  # parsed, offset-zeroed
    offset: float = 0.0
    audio_track_override: int | None = None
    ffprobe_cmd: str = "ffprobe"  # resolved ffprobe path/literal for audio-track auto-detection


class _NumericTableWidgetItem(QTableWidgetItem):
    """QTableWidgetItem that sorts by a numeric key instead of display text.

    Avoids the default lexicographic sort that places "100" before "20".
    Missing values use ``inf`` so unranked rows cluster at one end.
    """

    _SORT_ROLE = Qt.ItemDataRole.UserRole + 1

    def __init__(self, text: str, sort_key: float) -> None:
        super().__init__(text)
        self.setData(self._SORT_ROLE, sort_key)

    def __lt__(self, other: QTableWidgetItem) -> bool:
        own = self.data(self._SORT_ROLE)
        theirs = other.data(self._SORT_ROLE)
        if own is None or theirs is None:
            return super().__lt__(other)
        return float(own) < float(theirs)


class WordCurationDialog(QDialog):
    """Dialog for selecting which words to include in card creation.

    Shows a table of words with checkboxes. Users can search/filter,
    select/deselect all, and confirm their selection.

    When ``media_context`` is supplied and its video file exists, an embedded
    ``SubtitlePlayerWidget`` is shown in the right pane so the user can preview
    the scene for each word. When ``lookup_fn`` is supplied, a ``QTextBrowser``
    below the player shows offline dictionary entries for the focused word.
    Both panes are optional and backward-compatible; existing callers that pass
    only ``words`` receive the same pure-table behaviour as before.
    """

    # Base table row height at font scale 1.0; scaled with the global UI font
    # scale so rows grow with the (QSS-driven) cell font instead of clipping it.
    _BASE_ROW_HEIGHT = 32

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

        # Lookup result cache keyed by lemma (empty results are cached too).
        self._lookup_cache: dict[str, list[tuple[str, str]]] = {}

        # Debounce timer for row-focus changes (avoid hammering lookup on arrow-key scroll).
        self._focus_timer = QTimer(self)
        self._focus_timer.setSingleShot(True)
        self._focus_timer.setInterval(120)
        self._focus_timer.timeout.connect(self._on_focus_timer_fired)
        self._pending_word: TokenizedWord | None = None
        self._pending_index: int | None = None

        # Debounce search keystrokes so a fast typist doesn't run setRowHidden
        # N times for N characters typed.  150 ms matches WordPreviewDialog.
        self._search_debounce_timer = QTimer(self)
        self._search_debounce_timer.setSingleShot(True)
        self._search_debounce_timer.setInterval(150)
        self._search_debounce_timer.timeout.connect(self._apply_search)

        self._setup_ui()
        self._populate_table()
        self._update_word_count()
        self.finished.connect(self._stop_player)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        self.setWindowTitle(self.tr("Word Curation"))
        self.setMinimumWidth(900)
        self.setMinimumHeight(600)
        if self._show_player or self._show_dict or self._has_candidates:
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

        if self._show_player or self._show_dict or self._has_candidates:
            # Horizontal splitter: left = word table, right = player + sentences + dict
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
        self._setup_shortcuts()

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

        _bulk_tooltip = self.tr(
            "Acts on highlighted rows when 2 or more are selected "
            "(Ctrl+Click or Shift+Click to select). Otherwise acts on all visible rows."
        )
        self.select_all_button = ModernButton(self.tr("Select All"), variant="secondary")
        self.select_all_button.clicked.connect(self._select_all)
        self.select_all_button.setToolTip(_bulk_tooltip)
        controls_layout.addWidget(self.select_all_button)

        self.deselect_all_button = ModernButton(self.tr("Deselect All"), variant="secondary")
        self.deselect_all_button.clicked.connect(self._deselect_all)
        self.deselect_all_button.setToolTip(_bulk_tooltip)
        controls_layout.addWidget(self.deselect_all_button)

        # Add to local known/ignore list (Issue #42). Acts on the highlighted
        # rows, or the current row when nothing is highlighted — deliberately NOT
        # all visible rows, to avoid ignoring the whole list by accident.
        self.add_known_button = ModernButton(self.tr("Add to Known Words"), variant="secondary")
        self.add_known_button.clicked.connect(self._on_add_to_known)
        self.add_known_button.setToolTip(
            self.tr(
                "Permanently ignore the highlighted row(s) — adds them to your local "
                "Known Words list so they are never mined again. Falls back to the "
                "current row when none are highlighted."
            )
        )
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
            header_view.resizeSection(0, 40)
            header_view.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
            header_view.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
            header_view.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
            header_view.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
            header_view.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
            header_view.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)

        self._apply_fixed_row_height()

        self.table.itemChanged.connect(self._on_item_changed)

        # Row-focus wiring — independent of checkbox state (itemSelectionChanged only).
        if self._show_player or self._show_dict or self._has_candidates:
            self.table.itemSelectionChanged.connect(self._on_row_focus_changed)

        # Right-click context menu (always present; useful for #43)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_table_context_menu)

        vbox.addWidget(self.table)
        return container

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
        # where Qt multimedia may not be available or needs patching.
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
            ffprobe_cmd=ctx.ffprobe_cmd,
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

        # Ctrl+A: Select all words (scoped to table so it doesn't override text selection in search)
        select_all_shortcut = QShortcut(QKeySequence("Ctrl+A"), self.table)
        select_all_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        select_all_shortcut.activated.connect(self._select_all)

        # Ctrl+D: Deselect all words (scoped to table)
        deselect_all_shortcut = QShortcut(QKeySequence("Ctrl+D"), self.table)
        deselect_all_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        deselect_all_shortcut.activated.connect(self._deselect_all)

        # Enter/Return: Confirm selection
        enter_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Return), self.table)
        enter_shortcut.activated.connect(self.accept)

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

    def _apply_fixed_row_height(self) -> None:
        """Set Fixed resize mode, deriving the row height from the global font scale.

        Scaling the base height by ``Theme.get_font_scale()`` tracks the same
        scale the QSS cell font uses, so enlarged fonts no longer clip. Computed
        when the (modal, per-open) dialog is built — no live re-scaling needed.
        """
        vh = self.table.verticalHeader()
        if vh:
            row_h = round(self._BASE_ROW_HEIGHT * Theme.get_font_scale())
            vh.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
            vh.setDefaultSectionSize(row_h)
            vh.setMinimumSectionSize(max(1, row_h - 4))

    def _populate_table(self) -> None:
        """Fill the table with words, all checked by default."""
        self.table.setSortingEnabled(False)
        self.table.blockSignals(True)
        self.table.setRowCount(len(self._words))

        for row, word in enumerate(self._words):
            # Checkbox column
            check_item = QTableWidgetItem()
            check_item.setFlags(
                Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
            )
            check_item.setCheckState(Qt.CheckState.Checked)
            check_item.setData(Qt.ItemDataRole.UserRole, row)  # Store original index
            self.table.setItem(row, 0, check_item)

            # Word (mined) — what becomes the Anki Expression
            # (source-orthography dictionary form for verbs/adjectives,
            # surface for nouns)
            self.table.setItem(row, 1, self._make_readonly_item(word.mined_form))

            # Form in subtitle — the raw surface as it appeared
            self.table.setItem(row, 2, self._make_readonly_item(word.surface))

            # Reading
            self.table.setItem(row, 3, self._make_readonly_item(word.reading))

            # Sentence (truncated). A trailing "(N)" flags words with N
            # alternative example sentences the user can pick from.
            n_candidates = len(word.sentence_candidates)
            item = self._make_readonly_item(self._sentence_display(word.sentence, n_candidates))
            item.setToolTip(self._sentence_tooltip(word.sentence, n_candidates))
            self.table.setItem(row, 4, item)

            # Frequency Rank — sort numerically, not lexically (issue #6)
            if word.frequency_rank is not None:
                rank_item = _NumericTableWidgetItem(str(word.frequency_rank), float(word.frequency_rank))
            else:
                rank_item = _NumericTableWidgetItem("-", float("inf"))
            rank_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.table.setItem(row, 5, rank_item)

            # Occurrences — times the word appears in this episode; sort
            # numerically so 15 ranks above 2 (Issue #88).
            occ = word.occurrence_count
            occ_item = _NumericTableWidgetItem(str(occ), float(occ))
            occ_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.table.setItem(row, 6, occ_item)

        self.table.blockSignals(False)
        self.table.setSortingEnabled(True)

        # Re-apply AFTER sorting is re-enabled: re-enabling sorting resets the
        # vertical-header resize mode to Interactive, which drops the scaled
        # Fixed row height. Re-applying here keeps it in effect.
        self._apply_fixed_row_height()

    def _make_readonly_item(self, text: str) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
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
            self._update_word_count()

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

    # ------------------------------------------------------------------
    # Signal handlers — row focus → player + dictionary
    # ------------------------------------------------------------------

    def _on_row_focus_changed(self) -> None:
        """Handle itemSelectionChanged — debounce and schedule _on_focus_timer_fired.

        MUST NOT read or write checkbox state; checkbox changes are handled by
        _on_item_changed (itemChanged signal) and kept independent.
        """
        current_row = self.table.currentRow()
        if current_row < 0:
            return

        # Resolve focused row → original word via the col-0 UserRole index.
        # The table is sortable, so visual row ≠ original word index.
        check_item = self.table.item(current_row, 0)
        if check_item is None:
            return
        original_index = check_item.data(Qt.ItemDataRole.UserRole)
        if original_index is None or not (0 <= original_index < len(self._words)):
            return

        self._pending_word = self._words[original_index]
        self._pending_index = original_index
        # (Re)start the debounce timer — rapid arrow-key scrolling only fires once.
        self._focus_timer.start()

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

        # Dictionary pane: look up by lemma (definitions key on lemma, not mined_form).
        if self._show_dict and hasattr(self, "definition_view"):
            self._lookup_and_render(word.lemma)

    def _lookup_and_render(self, lemma: str) -> None:
        """Fetch definition entries for ``lemma`` (with cache) and render into the view."""
        if lemma not in self._lookup_cache:
            assert self._lookup_fn is not None  # guarded by self._show_dict
            self._lookup_cache[lemma] = self._lookup_fn(lemma)

        entries = self._lookup_cache[lemma]
        if not entries:
            escaped = html.escape(lemma)
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
        """Seek the player to ``start_time`` and pause, showing the frame."""
        if self._show_player and hasattr(self, "player_widget"):
            self.player_widget.seek_seconds(start_time)
            self.player_widget.pause()

    def _visual_row_for_index(self, idx: int) -> int | None:
        """Find the table row whose col-0 UserRole holds original word index ``idx``."""
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) == idx:
                return row
        return None

    def _stop_player(self) -> None:
        """Release the embedded player when the dialog closes (any exit path).

        ``release`` (not ``stop``) so an in-flight ffprobe probe is joined: Qt
        does not forward the dialog's close to the child player widget, so a
        still-running probe worker would otherwise outlive it.
        """
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
            clipboard.setText(word.sentence)

    # ------------------------------------------------------------------
    # Bulk-action helpers
    # ------------------------------------------------------------------

    def _target_rows(self) -> list[int]:
        """Return rows for bulk actions: highlighted rows if 2+, else all visible.

        Uses the QTableWidget multi-row selection (Ctrl/Shift+Click) when the
        user has selected at least two rows. Falls back to every visible row so
        legacy single-click + Select All behaviour is preserved.
        """
        selection_model = self.table.selectionModel()
        if selection_model is not None:
            selected = sorted(
                {index.row() for index in selection_model.selectedRows() if not self.table.isRowHidden(index.row())}
            )
            if len(selected) >= 2:
                return selected
        return [row for row in range(self.table.rowCount()) if not self.table.isRowHidden(row)]

    def _select_all(self) -> None:
        """Check rows in the current bulk-action target set."""
        self.table.blockSignals(True)
        for row in self._target_rows():
            item = self.table.item(row, 0)
            if item and self._is_checkable(item):
                item.setCheckState(Qt.CheckState.Checked)
        self.table.blockSignals(False)
        self._update_word_count()

    def _deselect_all(self) -> None:
        """Uncheck rows in the current bulk-action target set."""
        self.table.blockSignals(True)
        for row in self._target_rows():
            item = self.table.item(row, 0)
            if item and self._is_checkable(item):
                item.setCheckState(Qt.CheckState.Unchecked)
        self.table.blockSignals(False)
        self._update_word_count()

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
        selection_model = self.table.selectionModel()
        rows: list[int] = []
        if selection_model is not None:
            rows = sorted(
                {index.row() for index in selection_model.selectedRows() if not self.table.isRowHidden(index.row())}
            )
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
        self._update_word_count()

    _toggle_current_row = _toggle_selected_rows

    def _known_target_rows(self) -> list[int]:
        """Rows for "Add to Known Words": highlighted rows, else the current row.

        Unlike :meth:`_target_rows`, this never falls back to every visible row —
        ignoring an entire filtered list with one click would be too easy to
        trigger by accident.
        """
        selection_model = self.table.selectionModel()
        if selection_model is not None:
            selected = sorted(
                {index.row() for index in selection_model.selectedRows() if not self.table.isRowHidden(index.row())}
            )
            if selected:
                return selected
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
        self._update_word_count()

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

    def _update_word_count(self) -> None:
        """Update the word count label."""
        selected = sum(
            1
            for row in range(self.table.rowCount())
            if (item := self.table.item(row, 0)) and item.checkState() == Qt.CheckState.Checked
        )
        total = len(self._words)
        self.word_count_label.setText(tr_format(self.tr("%1 of %2 words selected"), selected, total))

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
