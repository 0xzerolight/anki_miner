"""Settings tab with category organization using extracted panels."""

import os
import re
from collections.abc import Callable
from dataclasses import replace
from functools import partial
from pathlib import Path
from typing import NamedTuple

from PyQt6.QtCore import QEventLoop, Qt, pyqtSignal
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QMessageBox,
    QProgressDialog,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from anki_miner.config import AnkiMinerConfig, ChainEntry
from anki_miner.config.paths import ANKI_MINER_HOME
from anki_miner.gui.resources.styles import SPACING
from anki_miner.gui.widgets.enhanced import FileSelector, ModernButton
from anki_miner.gui.widgets.panels import (
    AnkiSettingsPanel,
    DictionarySettingsPanel,
    FilteringSettingsPanel,
    MediaSettingsPanel,
    ThemesPanel,
    YouTubeSettingsPanel,
)
from anki_miner.gui.workers.base_worker import SingleCallWorker
from anki_miner.gui.workers.dictionary_import_worker import DictionaryImportWorker
from anki_miner.gui.workers.fetch_decks_worker import FetchDecksWorker
from anki_miner.gui.workers.fetch_fields_worker import FetchFieldsWorker
from anki_miner.gui.workers.styling_worker import StylingWorker
from anki_miner.gui.workers.yomitan_csv_import_worker import YomitanCsvImportWorker
from anki_miner.services.anki_service import AnkiService
from anki_miner.services.dictionary.importers.yomitan_importer import derive_dict_id_from_zip
from anki_miner.services.dictionary.registry import DictionaryRegistry
from anki_miner.services.frequency import YomitanFreqImportResult, import_yomitan_freq_zip
from anki_miner.services.known_word_db import KnownWordDB
from anki_miner.services.pitch_accent import YomitanPitchImportResult, import_yomitan_pitch_zip


class _YomitanCsvLabels(NamedTuple):
    """User-facing strings that distinguish the pitch vs frequency import flow.

    Everything else in :meth:`SettingsTab._resolve_yomitan_csv_path` (the
    QProgressDialog + QEventLoop scaffold, the overwrite guard, the staged
    ``.pending`` write, cancel suppression) is identical between the two.
    """

    progress: str  # QProgressDialog label, e.g. "Importing pitch accent dictionary…"
    overwrite_title: str  # overwrite-confirm dialog title
    failure_title: str  # import-failure warning title
    success_title: str  # post-import information dialog title


class SettingsTab(QWidget):
    """Settings tab with category organization.

    Uses extracted panel components for cleaner architecture.
    Each category (Anki, Media, Dictionary, Filtering, YouTube, Themes) has its
    own panel.

    Signals:
        validation_requested: Emitted when validation should be triggered
        config_changed: Emitted when configuration is saved (passes new config)
    """

    validation_requested = pyqtSignal()
    config_changed = pyqtSignal(object)  # Emits AnkiMinerConfig

    def __init__(self, config: AnkiMinerConfig, parent=None):
        """Initialize the settings tab.

        Args:
            config: Current configuration
            parent: Optional parent widget
        """
        super().__init__(parent)
        self.config = config
        # Long-lived worker reference; YomitanCsvImportWorker is a QThread and
        # would be destroyed mid-run if the worker fell out of scope before
        # joining. _resolve_frequency_path stores the active worker here.
        self._active_freq_worker: YomitanCsvImportWorker | None = None
        # Same GC-safety rationale as the freq worker above; the pitch worker
        # is a QThread and would be destroyed mid-run if it fell out of scope
        # before joining. _resolve_pitch_accent_path stores it here.
        self._active_pitch_worker: YomitanCsvImportWorker | None = None
        # Deferred CSV-promotion closures (T-10). A pitch/freq zip import stages
        # its CSV to a sibling ``.pending`` file; the promotion (os.replace into
        # place + selector update + success dialog) is held here and run by
        # _commit_pending_csv_imports() only AFTER *both* imports have passed,
        # so a frequency failure can never leave pitch_accent.csv half-written.
        self._pending_pitch_commit: Callable[[], None] | None = None
        self._pending_freq_commit: Callable[[], None] | None = None
        self._setup_ui()
        self._connect_signals()
        self._load_config()

    def _setup_ui(self) -> None:
        """Set up the user interface."""
        layout = QVBoxLayout()
        layout.setSpacing(SPACING.sm)
        layout.setContentsMargins(SPACING.md, SPACING.md, SPACING.md, SPACING.md)

        # Category tabs
        self.tab_widget = QTabWidget()
        self.tab_widget.setObjectName("settings-tabs")

        # Create panels using extracted components
        self.anki_panel = AnkiSettingsPanel()
        self.media_panel = MediaSettingsPanel()
        self.dictionary_panel = DictionarySettingsPanel(self.config.dicts_root)
        self.filtering_panel = FilteringSettingsPanel()
        self.youtube_panel = YouTubeSettingsPanel()
        self.themes_panel = ThemesPanel(self.config.themes_root)

        # Add tabs with scroll areas for each panel
        self.tab_widget.addTab(self._wrap_in_scroll_area(self.anki_panel), "Anki")
        self.tab_widget.addTab(self._wrap_in_scroll_area(self.media_panel), "Media")
        self.tab_widget.addTab(self._wrap_in_scroll_area(self.dictionary_panel), "Dictionary")
        self.tab_widget.addTab(self._wrap_in_scroll_area(self.filtering_panel), "Filtering")
        self.tab_widget.addTab(self._wrap_in_scroll_area(self.youtube_panel), "YouTube")
        # Themes tab — sub-tab index captured so MainWindow / shortcuts can
        # jump straight to it via :meth:`open_themes_subtab`.
        self._themes_subtab_index = self.tab_widget.addTab(self._wrap_in_scroll_area(self.themes_panel), "Themes")
        # Reset preview baseline when the user navigates away from Themes so
        # a later visit reverts to their last-chosen theme, not session start.
        self.tab_widget.currentChanged.connect(self._on_settings_subtab_changed)

        layout.addWidget(self.tab_widget)

        # Updates row — single top-level toggle, no panel needed for one checkbox.
        self.check_for_updates_checkbox = QCheckBox("Check for updates on startup")
        self.check_for_updates_checkbox.setToolTip(
            "When enabled, Anki Miner queries GitHub for new releases on launch."
        )
        layout.addWidget(self.check_for_updates_checkbox)

        # Action buttons at bottom
        button_layout = QHBoxLayout()
        button_layout.setSpacing(SPACING.sm)
        button_layout.addStretch()

        self.reset_button = ModernButton("Reset to Defaults", variant="secondary")
        self.reset_button.clicked.connect(self._on_reset_clicked)
        self.reset_button.setToolTip("Reset all settings to default values (Ctrl+R)")
        button_layout.addWidget(self.reset_button)

        self.save_button = ModernButton("Save Settings", variant="primary")
        self.save_button.clicked.connect(self._on_save_clicked)
        self.save_button.setToolTip("Save settings to disk (Ctrl+S)")
        button_layout.addWidget(self.save_button)

        layout.addLayout(button_layout)

        self.setLayout(layout)

        # Set up keyboard shortcuts
        self._setup_shortcuts()

    def _connect_signals(self) -> None:
        """Connect panel signals to tab handlers."""
        # Anki panel signals
        self.anki_panel.deck_sync_requested.connect(self.validation_requested.emit)
        self.anki_panel.notetype_sync_requested.connect(self.validation_requested.emit)
        self.anki_panel.test_connection_requested.connect(self.validation_requested.emit)
        self.anki_panel.fetch_fields_requested.connect(self._on_fetch_fields_requested)
        self.anki_panel.apply_styling_requested.connect(self._on_apply_styling_requested)
        self.anki_panel.remove_styling_requested.connect(self._on_remove_styling_requested)

        # Dictionary panel signals — wire Add/Reimport to import worker dialogs.
        self.dictionary_panel.add_dict_requested.connect(self._on_add_dict_clicked)
        self.dictionary_panel.reimport_jmdict_requested.connect(self._on_reimport_jmdict_clicked)
        self.dictionary_panel.reimport_dict_requested.connect(self._on_reimport_dict_clicked)
        self.dictionary_panel.reimport_all_requested.connect(self._on_reimport_all_clicked)
        # Persist immediately after a destructive remove so an orphan dict_id
        # doesn't reappear in gui_config.json on next launch (Issue #30). Use a
        # NARROW persist of just the chain — NOT the full Save pipeline (T-08):
        # _on_save_clicked has unrelated early-return aborts (bad dicts_root,
        # missing cookies file, invalid regex, pitch/freq import failure), any
        # of which would skip persisting the removal and leave the deleted
        # dict_id orphaned — the exact Issue #30 bug this wiring prevents — while
        # its success path silently commits every panel's unsaved edits.
        self.dictionary_panel.dictionary_removed.connect(
            lambda: self._persist_chain_change(self.dictionary_panel.get_chain())
        )

        # Filtering panel: excluded-decks picker + known-words cache rebuild (Issue #38).
        self.filtering_panel.fetch_decks_requested.connect(self._on_fetch_decks_requested)
        self.filtering_panel.rebuild_known_words_requested.connect(self._on_rebuild_known_words)
        self.filtering_panel.manage_known_words_requested.connect(self._on_manage_known_words)

        # Themes panel persists immediately on any change (live-preview model).
        self.themes_panel.state_changed.connect(self._on_theme_state_changed)
        self.themes_panel.font_scale_changed.connect(self._on_font_scale_changed)

        # Hold a reference to the fetch-fields worker across its lifetime.
        # Without this attribute, a freshly-spawned QThread can be garbage
        # collected before run() completes — Qt logs "QThread: Destroyed
        # while thread is still running" and the result signal never fires.
        self._fetch_fields_worker: SingleCallWorker | None = None
        # Same GC-safety rationale for the deck-list fetch worker.
        self._fetch_decks_worker: SingleCallWorker | None = None
        # And for the card-styling apply/remove worker (Issue #44).
        self._styling_worker: StylingWorker | None = None

    def _setup_shortcuts(self) -> None:
        """Set up keyboard shortcuts."""
        # Ctrl+S: Save settings
        save_shortcut = QShortcut(QKeySequence("Ctrl+S"), self)
        save_shortcut.activated.connect(self._on_save_clicked)

        # Ctrl+R: Reset to defaults
        reset_shortcut = QShortcut(QKeySequence("Ctrl+R"), self)
        reset_shortcut.activated.connect(self._on_reset_clicked)

    def _wrap_in_scroll_area(self, widget: QWidget) -> QScrollArea:
        """Wrap a widget in a scrollable container.

        Args:
            widget: Widget to wrap

        Returns:
            QScrollArea containing the widget
        """
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll_area.setWidget(widget)
        return scroll_area

    def _load_config(self) -> None:
        """Load current configuration into UI."""
        # Anki settings
        self.anki_panel.deck_input.setText(self.config.anki_deck_name)
        self.anki_panel.note_type_input.setText(self.config.anki_note_type)
        self.anki_panel.ankiconnect_url_input.setText(self.config.ankiconnect_url)
        self.anki_panel.anki_tags_input.setText(self.config.anki_tags)

        # Anki card field mappings
        self.anki_panel.set_card_fields(self.config.anki_fields)

        # Card styling (Issue #44)
        self.anki_panel.set_card_style_preset(self.config.card_style_preset)
        self.anki_panel.set_custom_css(self.config.custom_card_css)

        # Media settings
        self.media_panel.audio_format_combo.setCurrentText(self.config.audio_format)
        self.media_panel.audio_bitrate_spinbox.setValue(self.config.audio_bitrate)
        self.media_panel.audio_padding_spinbox.setValue(self.config.audio_padding)
        self.media_panel.screenshot_offset_spinbox.setValue(self.config.screenshot_offset)
        self.media_panel.max_workers_spinbox.setValue(self.config.max_parallel_workers)

        # Animated screenshot settings
        self.media_panel.animated_checkbox.setChecked(self.config.screenshot_animated)
        self.media_panel.animated_format_combo.setCurrentText(self.config.screenshot_animated_format)
        self.media_panel.animated_duration_spinbox.setValue(self.config.screenshot_animated_clip_duration)
        self.media_panel.animated_match_audio_checkbox.setChecked(self.config.screenshot_animated_match_audio)
        self.media_panel.animated_fps_spinbox.setValue(self.config.screenshot_animated_fps)
        self.media_panel.animated_height_spinbox.setValue(self.config.screenshot_animated_height)
        self.media_panel.animated_quality_spinbox.setValue(self.config.screenshot_animated_quality)
        self.media_panel._set_animated_enabled(self.config.screenshot_animated)
        self.media_panel._set_match_audio(self.config.screenshot_animated_match_audio)

        # Dictionary chain
        self.dictionary_panel.set_dicts_root(self.config.dicts_root)
        self.dictionary_panel.set_chain(self.config.dictionary_chain)

        # Pitch accent settings
        self.dictionary_panel.pitch_accent_selector.set_path(str(self.config.pitch_accent_path))
        self.dictionary_panel.use_pitch_accent_checkbox.setChecked(self.config.use_pitch_accent)
        self.anki_panel.set_pitch_category_format(self.config.pitch_category_format)

        # Frequency settings
        self.filtering_panel.frequency_selector.set_path(str(self.config.frequency_list_path))
        self.filtering_panel.use_frequency_checkbox.setChecked(self.config.use_frequency_data)
        self.filtering_panel.max_frequency_spinbox.setValue(self.config.max_frequency_rank)

        # Known words database settings
        self.filtering_panel.use_known_words_db_checkbox.setChecked(self.config.use_known_words_db)
        self.filtering_panel.set_excluded_decks(self.config.excluded_decks)
        self.filtering_panel.set_excluded_wordsets(self.config.excluded_wordsets)

        # Word list settings. Always set the selector — including to "" when the
        # path is None — so Reset-to-Defaults (or any update_config that drops
        # the path) clears the field. Without the else-branch the stale path
        # stayed visible and the next Save read it back via get_path(), silently
        # re-persisting it (T-11).
        self.filtering_panel.blacklist_selector.set_path(
            str(self.config.blacklist_path) if self.config.blacklist_path else ""
        )
        self.filtering_panel.use_blacklist_checkbox.setChecked(self.config.use_blacklist)
        self.filtering_panel.whitelist_selector.set_path(
            str(self.config.whitelist_path) if self.config.whitelist_path else ""
        )
        self.filtering_panel.use_whitelist_checkbox.setChecked(self.config.use_whitelist)

        # Subtitle text filtering settings (Issue #8)
        self.filtering_panel.subtitle_regex_edit.setText(self.config.subtitle_regex_filter)
        self.filtering_panel.subtitle_replacement_edit.setText(self.config.subtitle_regex_replacement)
        self.filtering_panel.use_subtitle_regex_checkbox.setChecked(self.config.use_subtitle_regex_filter)

        # Deduplication settings
        self.filtering_panel.deduplicate_sentences_checkbox.setChecked(self.config.deduplicate_sentences)

        # Script-type filters (Issue #57)
        self.filtering_panel.exclude_hiragana_only_checkbox.setChecked(self.config.exclude_hiragana_only_words)
        self.filtering_panel.exclude_katakana_only_checkbox.setChecked(self.config.exclude_katakana_only_words)

        # i+1 sentence filter setting
        self.filtering_panel.use_i_plus_one_checkbox.setChecked(self.config.use_i_plus_one_filter)

        # Sentence length filter (Issue #33)
        self.filtering_panel.use_sentence_length_checkbox.setChecked(self.config.use_sentence_length_filter)
        self.filtering_panel.max_sentence_duration_spinbox.setValue(self.config.max_sentence_duration_seconds)
        self.filtering_panel.max_sentence_chars_spinbox.setValue(self.config.max_sentence_chars)

        # Card formatting (Issue #20)
        self.filtering_panel.bold_target_in_sentence_checkbox.setChecked(self.config.bold_target_in_sentence)

        # YouTube settings
        self.youtube_panel.set_cookies_from_browser(self.config.youtube_cookies_from_browser)
        self.youtube_panel.set_cookies_file(self.config.youtube_cookies_file)
        self.youtube_panel.set_max_duration_seconds(self.config.youtube_max_duration_s)
        self.youtube_panel.set_playlist_max(self.config.youtube_playlist_max)

        # Update settings
        self.check_for_updates_checkbox.setChecked(self.config.check_for_updates)

    def open_themes_subtab(self) -> None:
        """Switch the settings sub-tab to Themes.

        Called by MainWindow when the user picks the 'All themes…' sentinel
        in the header combo.
        """
        self.tab_widget.setCurrentIndex(self._themes_subtab_index)

    def _on_settings_subtab_changed(self, index: int) -> None:
        """Reset the Themes panel preview baseline when leaving its sub-tab."""
        if index != self._themes_subtab_index:
            self.themes_panel.reset_baseline()

    def _on_theme_state_changed(self, active: str, favorites: tuple) -> None:
        """Forward Themes panel changes through ``config_changed``.

        The Themes panel writes through Theme directly (live preview); this
        slot mirrors the change into ``self.config`` and re-emits so the
        existing ``config_changed`` → ``MainWindow.update_config`` chain
        persists to ``gui_config.json`` without duplicate logic.
        """
        self.config = replace(self.config, theme=active, theme_favorites=tuple(favorites))
        self.config_changed.emit(self.config)

    def _on_font_scale_changed(self, scale: float) -> None:
        """Fold the Themes panel font-scale change into the config and persist.

        Mirrors :meth:`_on_theme_state_changed`: the Themes panel writes
        through Theme directly (live preview); this slot reflects the new
        scale into ``self.config`` and re-emits so the existing
        ``config_changed`` → ``MainWindow.update_config`` chain persists
        ``ui_font_scale`` to ``gui_config.json``.
        """
        self.config = replace(self.config, ui_font_scale=scale)
        self.config_changed.emit(self.config)

    def _on_save_clicked(self) -> None:
        """Handle save button click."""
        # If the user just re-enabled startup checks (False -> True), clear any
        # previously skipped version so a fresh check runs next launch.
        was_enabled = self.config.check_for_updates
        now_enabled = self.check_for_updates_checkbox.isChecked()
        skipped_update_version = self.config.skipped_update_version
        if now_enabled and not was_enabled:
            skipped_update_version = ""

        # Validate dictionary storage folder (Issue #45). Only enforced when
        # the user has changed the path — reuse-of-current always passes so a
        # transiently-unavailable mount (external SSD) doesn't block other
        # unrelated edits from saving.
        new_dicts_root = self.dictionary_panel.get_dicts_root()
        if new_dicts_root != self.config.dicts_root:
            if not new_dicts_root.is_dir():
                QMessageBox.warning(
                    self,
                    "Invalid dictionary folder",
                    f"{new_dicts_root} is not a directory.\n\nPick an existing folder or click Reset to default.",
                )
                return
            if not os.access(new_dicts_root, os.W_OK):
                QMessageBox.warning(
                    self,
                    "Dictionary folder not writable",
                    f"Cannot write to {new_dicts_root}.\n\nPick a folder you own.",
                )
                return

        # Validate the YouTube cookies file (Issue #62). yt-dlp would otherwise
        # fail mid-fetch with a cryptic message; catch a bad path up front.
        # An empty field is valid (no cookies file).
        cookies_file = self.youtube_panel.get_cookies_file()
        if cookies_file and not Path(cookies_file).is_file():
            QMessageBox.warning(
                self,
                "Cookies file not found",
                f"{cookies_file} is not a file.\n\nPick an exported cookies.txt or clear the field.",
            )
            return

        # Validate subtitle regex filter before saving so we never persist a
        # pattern that crashes the parser. Only validate when the user has
        # enabled the filter; an unchecked invalid pattern is harmless.
        subtitle_regex = self.filtering_panel.subtitle_regex_edit.text()
        use_subtitle_regex = self.filtering_panel.use_subtitle_regex_checkbox.isChecked()
        if use_subtitle_regex and subtitle_regex:
            try:
                re.compile(subtitle_regex)
            except re.error as e:
                QMessageBox.warning(
                    self,
                    "Invalid Subtitle Regex",
                    f"Pattern: {subtitle_regex}\n\nFix or disable the filter before saving.\n\nDetails: {e}",
                )
                return

        # Build the candidate config from all panels FIRST, then run the
        # pitch-zip and frequency-zip imports LAST so any current or future
        # pre-import validation step has a chance to abort before we touch
        # ~/.anki_miner/pitch_accent.csv or ~/.anki_miner/frequency.csv on
        # disk. The imports stage to ``.pending`` siblings and only promote
        # over the real CSVs once BOTH have passed (see
        # _commit_pending_csv_imports), so a frequency failure can't leave a
        # half-written pitch_accent.csv (T-10). See review of commit 63ffcd9
        # finding #2.
        new_config = replace(
            self.config,
            # Anki settings
            anki_deck_name=self.anki_panel.deck_input.text(),
            anki_note_type=self.anki_panel.note_type_input.text(),
            ankiconnect_url=self.anki_panel.ankiconnect_url_input.text(),
            anki_tags=self.anki_panel.anki_tags_input.text(),
            anki_fields=self.anki_panel.get_card_fields(),
            anki_word_field=self.anki_panel.get_card_fields().get("word", "Expression"),
            # Card styling (Issue #44)
            card_style_preset=self.anki_panel.get_card_style_preset(),
            custom_card_css=self.anki_panel.get_custom_css(),
            # Media settings
            audio_format=self.media_panel.audio_format_combo.currentText(),
            audio_bitrate=self.media_panel.audio_bitrate_spinbox.value(),
            audio_padding=self.media_panel.audio_padding_spinbox.value(),
            screenshot_offset=self.media_panel.screenshot_offset_spinbox.value(),
            max_parallel_workers=self.media_panel.max_workers_spinbox.value(),
            # Animated screenshot settings
            screenshot_animated=self.media_panel.animated_checkbox.isChecked(),
            screenshot_animated_format=self.media_panel.animated_format_combo.currentText(),
            screenshot_animated_clip_duration=(self.media_panel.animated_duration_spinbox.value()),
            screenshot_animated_match_audio=self.media_panel.animated_match_audio_checkbox.isChecked(),
            screenshot_animated_fps=self.media_panel.animated_fps_spinbox.value(),
            screenshot_animated_height=self.media_panel.animated_height_spinbox.value(),
            screenshot_animated_quality=self.media_panel.animated_quality_spinbox.value(),
            # Dictionary chain — chain is the single source of truth now
            dictionary_chain=self.dictionary_panel.get_chain(),
            # Dictionary storage folder (Issue #45). Validated above; reuse of
            # current value passes through unchanged.
            dicts_root=new_dicts_root,
            # Pitch accent settings — pitch_accent_path is overwritten below
            # with the resolver's result once both staged imports commit.
            pitch_accent_path=self.config.pitch_accent_path,
            use_pitch_accent=self.dictionary_panel.use_pitch_accent_checkbox.isChecked(),
            pitch_category_format=self.anki_panel.get_pitch_category_format(),
            # Frequency settings — frequency_list_path is overwritten below
            # with the resolver's result once both staged imports commit.
            frequency_list_path=self.config.frequency_list_path,
            use_frequency_data=self.filtering_panel.use_frequency_checkbox.isChecked(),
            max_frequency_rank=self.filtering_panel.max_frequency_spinbox.value(),
            # Known words database settings
            use_known_words_db=self.filtering_panel.use_known_words_db_checkbox.isChecked(),
            excluded_decks=self.filtering_panel.get_excluded_decks(),
            excluded_wordsets=self.filtering_panel.get_excluded_wordsets(),
            # Word list settings
            blacklist_path=(
                Path(self.filtering_panel.blacklist_selector.get_path())
                if self.filtering_panel.blacklist_selector.get_path()
                else None
            ),
            use_blacklist=self.filtering_panel.use_blacklist_checkbox.isChecked(),
            whitelist_path=(
                Path(self.filtering_panel.whitelist_selector.get_path())
                if self.filtering_panel.whitelist_selector.get_path()
                else None
            ),
            use_whitelist=self.filtering_panel.use_whitelist_checkbox.isChecked(),
            # Subtitle text filtering settings (Issue #8)
            subtitle_regex_filter=subtitle_regex,
            subtitle_regex_replacement=self.filtering_panel.subtitle_replacement_edit.text(),
            use_subtitle_regex_filter=use_subtitle_regex,
            # Deduplication settings
            deduplicate_sentences=self.filtering_panel.deduplicate_sentences_checkbox.isChecked(),
            # Script-type filters (Issue #57)
            exclude_hiragana_only_words=self.filtering_panel.exclude_hiragana_only_checkbox.isChecked(),
            exclude_katakana_only_words=self.filtering_panel.exclude_katakana_only_checkbox.isChecked(),
            # i+1 sentence filter setting
            use_i_plus_one_filter=self.filtering_panel.use_i_plus_one_checkbox.isChecked(),
            # Sentence length filter (Issue #33)
            use_sentence_length_filter=self.filtering_panel.use_sentence_length_checkbox.isChecked(),
            max_sentence_duration_seconds=self.filtering_panel.max_sentence_duration_spinbox.value(),
            max_sentence_chars=self.filtering_panel.max_sentence_chars_spinbox.value(),
            # Card formatting (Issue #20)
            bold_target_in_sentence=self.filtering_panel.bold_target_in_sentence_checkbox.isChecked(),
            # YouTube settings
            youtube_cookies_from_browser=self.youtube_panel.get_cookies_from_browser(),
            youtube_cookies_file=(
                Path(self.youtube_panel.get_cookies_file()) if self.youtube_panel.get_cookies_file() else None
            ),
            youtube_max_duration_s=self.youtube_panel.get_max_duration_seconds(),
            youtube_playlist_max=self.youtube_panel.get_playlist_max(),
            # Update settings
            check_for_updates=now_enabled,
            skipped_update_version=skipped_update_version,
        )

        # Last steps before commit: import the Yomitan pitch-accent zip and
        # frequency zip if the user picked either. Any pre-import validation
        # belongs ABOVE these calls. None here means an import failed OR was
        # cancelled — abort the whole save (the user already saw the error
        # dialog, if any). Each resolver only STAGES its CSV to a sibling
        # ``.pending`` file and defers the destructive promotion (os.replace +
        # selector update + success dialog) into a commit closure; nothing on
        # disk is clobbered until _commit_pending_csv_imports() runs below,
        # which only happens once BOTH imports have passed. A frequency failure
        # after a successful pitch import therefore leaves the user's existing
        # pitch_accent.csv untouched.
        resolved_pitch_path = self._resolve_pitch_accent_path()
        if resolved_pitch_path is None:
            return

        resolved_freq_path = self._resolve_frequency_path()
        if resolved_freq_path is None:
            # Pitch may have staged a .pending file; drop it so a failed save
            # never leaves stray staging files behind.
            self._discard_pending_csv_imports()
            return

        # Both imports validated — promote both staged CSVs to disk and run
        # their UI feedback now, after which the paths are safe to persist.
        self._commit_pending_csv_imports()
        new_config = replace(
            new_config,
            pitch_accent_path=resolved_pitch_path,
            frequency_list_path=resolved_freq_path,
        )

        # Sync the dictionary panel to the committed root (T-07). Done here —
        # alongside the config assignment, after every validation/import has
        # passed — so the panel never points at a root the save then aborted on.
        # Without this the panel keeps scanning/rmtree-targeting the OLD root
        # until restart: refresh_registry() renders fresh imports as "(missing)"
        # and remove() deletes from the wrong directory.
        if new_dicts_root != self.config.dicts_root:
            self.dictionary_panel.set_dicts_root(new_dicts_root)

        # Emit signal to notify listeners of config change
        self.config = new_config
        self.config_changed.emit(new_config)
        QMessageBox.information(
            self,
            "Settings Saved",
            "Settings saved.",
        )

    def _resolve_yomitan_csv_path(
        self,
        *,
        selector: FileSelector,
        dest_name: str,
        worker_factory: Callable[[Path, Path], YomitanCsvImportWorker],
        worker_slot_attr: str,
        commit_slot_attr: str,
        decline_fallback: Path,
        labels: _YomitanCsvLabels,
    ) -> Path | None:
        """Resolve a Yomitan pitch/frequency selector path for persistence.

        Shared engine behind :meth:`_resolve_pitch_accent_path` and
        :meth:`_resolve_frequency_path` — the two flows are byte-identical
        except for the user-facing ``labels``, the importer fn bound into the
        ``YomitanCsvImportWorker``, and which ``self`` slots hold the
        live-worker GC reference (``worker_slot_attr``) and the
        deferred-promotion closure (``commit_slot_attr``).

        If ``selector`` points at a Yomitan zip, the importer runs in
        ``worker_factory`` (a background QThread driven by a modal
        ``QProgressDialog`` + a local ``QEventLoop`` so this method stays
        blocking for the caller), staging its CSV to a sibling ``.pending``
        file under ``ANKI_MINER_HOME / dest_name``. The destructive promotion
        (os.replace into place, selector update, success dialog) is stored on
        ``self.<commit_slot_attr>`` and run by
        :meth:`_commit_pending_csv_imports` only once *every* import in the
        save has passed (T-10) — so a later failure can never leave the
        existing CSV half-overwritten. CSV/TSV paths pass through unchanged.

        Returns:
            * ``Path("")`` if no path is selected.
            * The original CSV/TSV path if not a zip.
            * The destination CSV path on successful import (promotion deferred).
            * ``decline_fallback`` if the user declined to overwrite an existing
              CSV (other settings still save).
            * ``None`` if the import failed or was cancelled (caller aborts
              the whole save).
        """
        # No staged promotion unless a zip import succeeds below.
        setattr(self, commit_slot_attr, None)

        raw = selector.get_path()
        if not raw:
            return Path("")
        if not raw.lower().endswith(".zip"):
            return Path(raw)

        zip_path = Path(raw)
        dest_csv = ANKI_MINER_HOME / dest_name
        pending_csv = dest_csv.with_suffix(dest_csv.suffix + ".pending")

        # Overwrite guard. atomic_write_csv only protects against mid-write
        # failures, not intentional clobbering of a user's existing CSV.
        if dest_csv.exists() and dest_csv.stat().st_size > 0:
            reply = QMessageBox.question(
                self,
                labels.overwrite_title,
                f"{dest_csv} already exists and will be replaced.\n\nContinue with import?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                # User opted out of THIS setting; let the rest of the save
                # commit with the existing path unchanged.
                return decline_fallback

        dlg = QProgressDialog(labels.progress, "Cancel", 0, 100, self)
        dlg.setWindowModality(Qt.WindowModality.ApplicationModal)
        dlg.show()

        # Import into the staging file, never directly over dest_csv.
        worker = worker_factory(zip_path, pending_csv)
        setattr(self, worker_slot_attr, worker)  # keep alive across QThread lifetime

        result_holder: dict[str, object] = {}
        loop = QEventLoop(self)

        def on_progress(cur: int, total: int, msg: str) -> None:
            dlg.setMaximum(total)
            dlg.setValue(cur)
            dlg.setLabelText(msg)

        def on_done(res: object) -> None:
            result_holder["ok"] = res
            loop.quit()

        def on_failed(err: str) -> None:
            result_holder["err"] = err
            loop.quit()

        worker.progress.connect(on_progress)
        worker.import_finished.connect(on_done)
        worker.failed.connect(on_failed)
        dlg.canceled.connect(worker.cancel)

        worker.start()
        loop.exec()
        dlg.close()
        worker.wait()  # join thread before next save might construct a new worker

        if "err" in result_holder:
            err_msg = str(result_holder["err"])
            if "cancel" not in err_msg.lower():
                QMessageBox.warning(self, labels.failure_title, err_msg)
            # Drop the staging file so a failed import leaves nothing behind.
            pending_csv.unlink(missing_ok=True)
            return None

        result = result_holder["ok"]
        # Both YomitanPitchImportResult and YomitanFreqImportResult expose the
        # entry_count / source_name / skipped_display_only attributes used below.
        assert isinstance(result, (YomitanPitchImportResult, YomitanFreqImportResult))

        def _commit() -> None:
            os.replace(pending_csv, dest_csv)
            # Reflect the imported path back into the UI so subsequent saves
            # don't re-trigger the import every time the user clicks Save.
            selector.set_path(str(dest_csv))
            skipped_note = (
                f" (skipped {result.skipped_display_only:,} display-only entries)"
                if result.skipped_display_only
                else ""
            )
            QMessageBox.information(
                self,
                labels.success_title,
                f"Imported {result.entry_count:,} entries from '{result.source_name}'.{skipped_note}",
            )

        setattr(self, commit_slot_attr, _commit)
        return dest_csv

    def _resolve_pitch_accent_path(self) -> Path | None:
        """Resolve the pitch-accent selector for persistence (see the engine).

        Thin wrapper over :meth:`_resolve_yomitan_csv_path`; see it for the full
        staging/deferred-promotion contract and the meaning of each return.
        """
        return self._resolve_yomitan_csv_path(
            selector=self.dictionary_panel.pitch_accent_selector,
            dest_name="pitch_accent.csv",
            worker_factory=partial(YomitanCsvImportWorker, import_yomitan_pitch_zip),
            worker_slot_attr="_active_pitch_worker",
            commit_slot_attr="_pending_pitch_commit",
            decline_fallback=self.config.pitch_accent_path,
            labels=_YomitanCsvLabels(
                progress="Importing pitch accent dictionary…",
                overwrite_title="Overwrite Pitch Accent File?",
                failure_title="Pitch Accent Import Failed",
                success_title="Pitch accent dictionary imported",
            ),
        )

    def _resolve_frequency_path(self) -> Path | None:
        """Resolve the frequency selector for persistence (see the engine).

        Thin wrapper over :meth:`_resolve_yomitan_csv_path`; see it for the full
        staging/deferred-promotion contract and the meaning of each return.
        """
        return self._resolve_yomitan_csv_path(
            selector=self.filtering_panel.frequency_selector,
            dest_name="frequency.csv",
            worker_factory=partial(YomitanCsvImportWorker, import_yomitan_freq_zip),
            worker_slot_attr="_active_freq_worker",
            commit_slot_attr="_pending_freq_commit",
            decline_fallback=self.config.frequency_list_path,
            labels=_YomitanCsvLabels(
                progress="Importing frequency dictionary…",
                overwrite_title="Overwrite Frequency List?",
                failure_title="Frequency Import Failed",
                success_title="Frequency dictionary imported",
            ),
        )

    def _commit_pending_csv_imports(self) -> None:
        """Promote any staged pitch/frequency CSV imports and clear them.

        Called once both resolvers have succeeded (T-10). Runs each deferred
        commit closure — os.replace(``.pending`` → final), selector update,
        success dialog — then resets the slots so a later save starts clean.
        """
        pitch_commit = self._pending_pitch_commit
        freq_commit = self._pending_freq_commit
        self._pending_pitch_commit = None
        self._pending_freq_commit = None
        if pitch_commit is not None:
            pitch_commit()
        if freq_commit is not None:
            freq_commit()

    def _discard_pending_csv_imports(self) -> None:
        """Drop staged pitch/frequency promotions without touching disk targets.

        Called when the save aborts after a successful pitch import but a failed
        frequency import (T-10). The frequency resolver already removed its own
        ``.pending`` file on failure, so here we only clean up a pitch staging
        file (if pitch succeeded) and clear both deferred commit slots so the
        next save starts clean. No final CSV is touched.
        """
        if self._pending_pitch_commit is not None:
            dest_csv = ANKI_MINER_HOME / "pitch_accent.csv"
            pending_csv = dest_csv.with_suffix(dest_csv.suffix + ".pending")
            pending_csv.unlink(missing_ok=True)
        self._pending_pitch_commit = None
        self._pending_freq_commit = None

    def _on_reset_clicked(self) -> None:
        """Handle reset button click."""
        reply = QMessageBox.question(
            self,
            "Reset Settings",
            "Reset all settings to defaults?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.Yes:
            from anki_miner.config import create_default_config

            self.config = create_default_config()
            self._load_config()
            self.config_changed.emit(self.config)
            QMessageBox.information(
                self,
                "Settings Reset",
                "Settings reset to defaults.",
            )

    def update_config(self, config: AnkiMinerConfig) -> None:
        """Update configuration from external source.

        Args:
            config: New configuration to load
        """
        self.config = config
        self._load_config()

    def iter_close_workers(self) -> tuple:
        """Live worker handles MainWindow must join on close (T-12).

        SettingsTab owns three short-lived AnkiConnect workers — fetch fields,
        fetch decks, apply/remove styling — each a tab-parented QThread that can
        sit in a 15-60 s blocking request. They have no ``worker_thread``
        attribute, so closeEvent discovers them here and routes each through the
        single ``MainWindow._join_worker_for_close`` policy (cancel + bounded
        grace join + laggard deferral). Returning them — rather than waiting
        here — keeps every shutdown join in one place; abandoning them to Qt
        teardown aborts with "QThread: Destroyed while thread is still running".

        (T-66 lifts these workers into a controller exposing the same hook.)
        """
        return (self._fetch_fields_worker, self._fetch_decks_worker, self._styling_worker)

    def shutdown(self) -> None:
        """Cancel every running AnkiConnect worker (cancel only, no wait).

        Explicit-teardown entry point mirroring the YouTube tab. closeEvent
        does the bounded join via :meth:`MainWindow._join_worker_for_close`;
        this is the standalone cancel for any non-close caller (and the future
        controller). ``cancel()`` is idempotent, so the helper re-cancelling is
        harmless.
        """
        for worker in self.iter_close_workers():
            if worker is not None and worker.isRunning():
                worker.cancel()

    # === Expose panel inputs for backward compatibility ===

    @property
    def deck_input(self):
        """Get deck input widget."""
        return self.anki_panel.deck_input

    @property
    def note_type_input(self):
        """Get note type input widget."""
        return self.anki_panel.note_type_input

    @property
    def ankiconnect_url_input(self):
        """Get AnkiConnect URL input widget."""
        return self.anki_panel.ankiconnect_url_input

    @property
    def audio_padding_spinbox(self):
        """Get audio padding spinbox widget."""
        return self.media_panel.audio_padding_spinbox

    @property
    def screenshot_offset_spinbox(self):
        """Get screenshot offset spinbox widget."""
        return self.media_panel.screenshot_offset_spinbox

    @property
    def max_workers_spinbox(self):
        """Get max workers spinbox widget."""
        return self.media_panel.max_workers_spinbox

    # === Dictionary import handlers ===

    def _set_import_buttons_enabled(self, enabled: bool) -> None:
        """Toggle import-trigger buttons. Prevents overlapping import workers."""
        self.dictionary_panel._add_btn.setEnabled(enabled)
        self.dictionary_panel._reimport_btn.setEnabled(enabled)
        self.dictionary_panel.set_per_row_reimport_enabled(enabled)

    def _persist_chain_change(self, new_chain: tuple[ChainEntry, ...]) -> None:
        """Save a chain mutation to disk and notify listeners.

        Called after a successful import so the freshly-imported dictionary
        is reachable on the very next lookup — without requiring the user
        to manually click Save in Settings. Without this, the dict folder
        exists on disk but is absent from dictionary_chain in gui_config,
        i.e. invisible to DictionaryRegistry.build_provider_chain.
        """
        new_config = replace(self.config, dictionary_chain=new_chain)
        self.config = new_config
        self.config_changed.emit(new_config)

    def _on_add_dict_clicked(self) -> None:
        """Prompt for a Yomitan zip and run the import worker."""
        zip_path_str, _ = QFileDialog.getOpenFileName(self, "Choose Yomitan dictionary zip", "", "Yomitan zip (*.zip)")
        if not zip_path_str:
            return

        dest_root = self.config.dicts_root
        dlg = QProgressDialog("Importing dictionary…", "Cancel", 0, 100, self)
        dlg.setWindowModality(Qt.WindowModality.ApplicationModal)
        dlg.show()

        worker = DictionaryImportWorker.for_yomitan(Path(zip_path_str), dest_root)
        self._active_import_worker = worker  # keep alive across QThread lifetime
        self._set_import_buttons_enabled(False)

        def on_progress(cur: int, total: int, msg: str) -> None:
            dlg.setMaximum(total)
            dlg.setValue(cur)
            dlg.setLabelText(msg)

        def on_import_finished(dict_id: str, meta: dict) -> None:
            dlg.close()
            QMessageBox.information(
                self,
                "Dictionary added",
                f"Imported {dict_id} ({meta.get('entry_count', 0):,} entries)",
            )
            new_chain = self._with_dict_at_top(dict_id)
            # New dict folder on disk — invalidate the panel's cached registry
            # scan so the row picks up the entry_count + source_name.
            self.dictionary_panel.refresh_registry()
            self.dictionary_panel.set_chain(new_chain)
            self._persist_chain_change(new_chain)
            self._set_import_buttons_enabled(True)

        def on_failed(err: str) -> None:
            dlg.close()
            QMessageBox.warning(self, "Import Failed", err)
            self._set_import_buttons_enabled(True)

        worker.progress.connect(on_progress)
        worker.import_finished.connect(on_import_finished)
        worker.failed.connect(on_failed)
        dlg.canceled.connect(worker.cancel)
        worker.start()

    def _on_reimport_dict_clicked(self, slot_id: str) -> None:
        """Prompt for a matching Yomitan zip and re-import into an existing slot.

        Slot identity is preserved by validating that the chosen zip's derived
        `dict_id` equals ``slot_id`` before invoking the importer with
        ``overwrite=True``. Picking a different zip would orphan the stale slot
        and silently create a new one — we abort with a warning instead.
        """
        zip_path_str, _ = QFileDialog.getOpenFileName(self, "Choose Yomitan dictionary zip", "", "Yomitan zip (*.zip)")
        if not zip_path_str:
            return

        zip_path = Path(zip_path_str)
        try:
            derived_id = derive_dict_id_from_zip(zip_path)
        except Exception as exc:  # noqa: BLE001 — surface every failure to GUI
            QMessageBox.warning(self, "Invalid Zip", str(exc))
            return

        if derived_id != slot_id:
            QMessageBox.warning(
                self,
                "Zip does not match slot",
                f"This zip is for '{derived_id}', but you are re-importing " f"'{slot_id}'. Pick the matching zip.",
            )
            return

        # Drop sqlite handles before the importer renames the dict folder.
        # On Windows the rename fails with "Access denied" while any
        # DefinitionService still holds its read-only connection open
        # (Issue #32). The remove flow uses the same hook (Issue #30).
        if not self.dictionary_panel.request_resource_release():
            QMessageBox.warning(
                self,
                "Re-import Blocked",
                "A mining run is in progress. Stop it before re-importing dictionaries.",
            )
            return

        dest_root = self.config.dicts_root
        dlg = QProgressDialog("Re-importing dictionary…", "Cancel", 0, 100, self)
        dlg.setWindowModality(Qt.WindowModality.ApplicationModal)
        dlg.show()

        worker = DictionaryImportWorker.for_yomitan(zip_path, dest_root, overwrite=True)
        self._active_import_worker = worker  # keep alive across QThread lifetime
        self._set_import_buttons_enabled(False)

        def on_progress(cur: int, total: int, msg: str) -> None:
            dlg.setMaximum(total)
            dlg.setValue(cur)
            dlg.setLabelText(msg)

        def on_done(dict_id: str, meta: dict) -> None:
            dlg.close()
            QMessageBox.information(
                self,
                "Dictionary re-imported",
                f"Re-imported {dict_id} ({meta.get('entry_count', 0):,} entries)",
            )
            # Refresh registry so the stale-flag warning clears on the row.
            current_chain = self.dictionary_panel.get_chain()
            self.dictionary_panel.refresh_registry()
            self.dictionary_panel.set_chain(current_chain)
            # Notify listeners so cached DefinitionService instances rebuild
            # with the freshly-rebuilt SQLite index.
            self.config_changed.emit(self.config)
            self._set_import_buttons_enabled(True)

        def on_failed(err: str) -> None:
            dlg.close()
            QMessageBox.warning(self, "Re-import Failed", err)
            self._set_import_buttons_enabled(True)

        worker.progress.connect(on_progress)
        worker.import_finished.connect(on_done)
        worker.failed.connect(on_failed)
        dlg.canceled.connect(worker.cancel)
        worker.start()

    def _with_dict_at_top(self, dict_id: str) -> tuple[ChainEntry, ...]:
        """Return the current chain with ``dict_id`` placed (or moved) to the top."""
        chain = list(self.dictionary_panel.get_chain())
        chain = [e for e in chain if not (e.kind == "indexed" and e.dict_id == dict_id)]
        chain.insert(0, ChainEntry(kind="indexed", dict_id=dict_id, enabled=True))
        return tuple(chain)

    def _on_reimport_jmdict_clicked(self) -> None:
        """Reimport JMdict from the configured XML path."""
        xml = self.config.jmdict_path
        if not xml.exists():
            QMessageBox.warning(
                self,
                "JMdict not found",
                f"No JMdict XML at {xml}. Download from EDRDG and place it there.",
            )
            return

        # Drop sqlite handles before the importer renames the dict folder
        # (Issue #32 — same root cause as #30). Without this, the rename
        # at yomitan_importer.py:215 fails with "Access denied" on Windows.
        if not self.dictionary_panel.request_resource_release():
            QMessageBox.warning(
                self,
                "Re-import Blocked",
                "A mining run is in progress. Stop it before re-importing dictionaries.",
            )
            return

        dest_root = self.config.dicts_root
        dlg = QProgressDialog("Reimporting JMdict…", "Cancel", 0, 100, self)
        dlg.setWindowModality(Qt.WindowModality.ApplicationModal)
        dlg.show()

        worker = DictionaryImportWorker.for_jmdict(xml, dest_root)
        self._active_import_worker = worker
        self._set_import_buttons_enabled(False)

        def on_progress(cur: int, total: int, msg: str) -> None:
            dlg.setMaximum(total)
            dlg.setValue(cur)
            dlg.setLabelText(msg)

        def on_done(dict_id: str, meta: dict) -> None:
            dlg.close()
            # Re-render chain so the (refreshed) entry count is reflected.
            current_chain = self.dictionary_panel.get_chain()
            self.dictionary_panel.refresh_registry()
            self.dictionary_panel.set_chain(current_chain)
            # Notify listeners so cached DefinitionService instances rebuild
            # with the freshly-rebuilt SQLite index.
            self.config_changed.emit(self.config)
            self._set_import_buttons_enabled(True)

        def on_failed(err: str) -> None:
            dlg.close()
            QMessageBox.warning(self, "Reimport Failed", err)
            self._set_import_buttons_enabled(True)

        worker.progress.connect(on_progress)
        worker.import_finished.connect(on_done)
        worker.failed.connect(on_failed)
        dlg.canceled.connect(worker.cancel)
        worker.start()

    def _on_reimport_all_clicked(self) -> None:
        """Reimport every dictionary in the chain from its saved source.

        For each indexed ChainEntry, dispatch based on format:
        - jmdict format → reimport from ``config.jmdict_path`` (the XML stays
          on disk between sessions, no copy needed).
        - yomitan format → reimport from ``<dicts_root>/<dict_id>/source.zip``
          if present.

        Dicts with no saved source are skipped and surfaced in the final
        summary dialog. The user can seed them via the per-row stale-reimport
        button (which prompts for the zip and now persists it on success).

        Runs sequentially: each worker's ``on_done`` chains the next dispatch
        so a single ApplicationModal QProgressDialog tracks the whole batch.
        Per-dict failures accumulate into ``errors`` and don't abort the
        loop. ``config_changed`` is emitted once at the end so cached
        DefinitionService instances rebuild a single time.
        """
        # Fresh registry scan so we see source_name / format for the summary.
        registry = DictionaryRegistry(self.config.dicts_root)
        registry.load()

        # Job tuples: ("yomitan", dict_id, display_name, source_zip_path)
        #             ("jmdict",  dict_id, display_name, xml_path)
        jobs: list[tuple[str, str, str, Path]] = []
        missing_legacy: list[str] = []

        for entry in self.dictionary_panel.get_chain():
            if entry.kind != "indexed" or entry.dict_id is None:
                continue
            meta = registry.get(entry.dict_id)
            if meta is None:
                missing_legacy.append(entry.dict_id)
                continue
            if meta.format == "jmdict":
                if self.config.jmdict_path.exists():
                    jobs.append(("jmdict", meta.dict_id, meta.source_name, self.config.jmdict_path))
                else:
                    missing_legacy.append(meta.source_name)
                continue
            # Yomitan and anything else with a saved zip
            source_zip = self.config.dicts_root / meta.dict_id / "source.zip"
            if source_zip.exists():
                jobs.append(("yomitan", meta.dict_id, meta.source_name, source_zip))
            else:
                missing_legacy.append(meta.source_name)

        if not jobs:
            if missing_legacy:
                body = (
                    "No dictionaries with saved sources were found.\n\n"
                    "Skipped (no saved source — right-click a dictionary "
                    "row → Re-import… to seed):\n" + "\n".join(f"  • {n}" for n in missing_legacy)
                )
            else:
                body = "No dictionaries in the chain."
            QMessageBox.information(self, "Nothing to reimport", body)
            return

        # Drop sqlite handles before any worker touches the dict folders.
        # On Windows the importer's directory rename fails with "Access
        # denied" while a DefinitionService still holds its read-only
        # connection open (Issue #32; same hook as the remove flow in #30).
        if not self.dictionary_panel.request_resource_release():
            QMessageBox.warning(
                self,
                "Re-import Blocked",
                "A mining run is in progress. Stop it before re-importing dictionaries.",
            )
            return

        dlg = QProgressDialog("Reimporting dictionaries…", "Cancel", 0, 100, self)
        dlg.setWindowModality(Qt.WindowModality.ApplicationModal)
        dlg.show()

        self._set_import_buttons_enabled(False)

        # Mutable state shared across the chained closures.
        state: dict[str, object] = {
            "index": 0,
            "cancelled": False,
            "reimported": [],
            "errors": [],
        }

        def finish() -> None:
            dlg.close()
            # One refresh + one config_changed for the whole batch so
            # DefinitionService rebuilds once, not N times.
            current_chain = self.dictionary_panel.get_chain()
            self.dictionary_panel.refresh_registry()
            self.dictionary_panel.set_chain(current_chain)
            self.config_changed.emit(self.config)
            self._set_import_buttons_enabled(True)

            reimported = state["reimported"]
            errors = state["errors"]
            assert isinstance(reimported, list)
            assert isinstance(errors, list)

            lines: list[str] = []
            if reimported:
                lines.append(f"Reimported {len(reimported)} dictionar" f"{'y' if len(reimported) == 1 else 'ies'}:")
                lines.extend(f"  • {n}" for n in reimported)
            if missing_legacy:
                if lines:
                    lines.append("")
                lines.append("Skipped (no saved source — right-click a " "dictionary row → Re-import… to seed):")
                lines.extend(f"  • {n}" for n in missing_legacy)
            if errors:
                if lines:
                    lines.append("")
                lines.append("Failed:")
                lines.extend(f"  • {name}: {msg}" for name, msg in errors)
            if state["cancelled"]:
                if lines:
                    lines.append("")
                lines.append("Cancelled before remaining dictionaries.")

            QMessageBox.information(self, "Reimport All", "\n".join(lines) or "Done.")

        def launch_next() -> None:
            idx = state["index"]
            assert isinstance(idx, int)
            if state["cancelled"] or idx >= len(jobs):
                finish()
                return

            kind, dict_id, display, source_path = jobs[idx]
            dlg.setLabelText(f"Dictionary {idx + 1} of {len(jobs)}: {display}")
            dlg.setMaximum(100)
            dlg.setValue(0)

            if kind == "jmdict":
                worker = DictionaryImportWorker.for_jmdict(source_path, self.config.dicts_root)
            else:
                worker = DictionaryImportWorker.for_yomitan(source_path, self.config.dicts_root, overwrite=True)
            # Join the predecessor before dropping its reference (T-09). This
            # closure runs inside the previous worker's queued finished slot,
            # emitted from run() just before the OS thread exits — so its
            # QThread may still be technically running. Reassigning
            # _active_import_worker without waiting drops the only reference to a
            # live, unparented QThread → "QThread: Destroyed while thread is
            # still running". wait() is at most microseconds from returning here.
            prev = getattr(self, "_active_import_worker", None)
            if prev is not None and prev.isRunning():
                prev.wait()
            self._active_import_worker = worker

            def on_progress(cur: int, total: int, msg: str) -> None:
                dlg.setMaximum(total)
                dlg.setValue(cur)

            def on_done(_dict_id: str, _meta: dict) -> None:
                reimported = state["reimported"]
                assert isinstance(reimported, list)
                reimported.append(display)
                state["index"] = idx + 1
                launch_next()

            def on_failed(err: str) -> None:
                errors = state["errors"]
                assert isinstance(errors, list)
                errors.append((display, err))
                state["index"] = idx + 1
                launch_next()

            worker.progress.connect(on_progress)
            worker.import_finished.connect(on_done)
            worker.failed.connect(on_failed)
            worker.start()

        def on_cancel() -> None:
            state["cancelled"] = True
            worker = getattr(self, "_active_import_worker", None)
            if worker is not None and worker.isRunning():
                worker.cancel()

        dlg.canceled.connect(on_cancel)
        launch_next()

    # === Fetch fields handler ===

    def _on_fetch_fields_requested(self) -> None:
        """Fetch the note type's field list from AnkiConnect in a worker thread.

        Reads the current note type and AnkiConnect URL straight from the panel
        inputs (not ``self.config``) so the user can fetch without first hitting
        Save. The button is disabled for the duration to prevent piling up
        concurrent requests. Results land on the main thread via
        :meth:`_on_fetch_fields_finished`.
        """
        # Don't stack worker threads — first request wins until it completes.
        if self._fetch_fields_worker is not None and self._fetch_fields_worker.isRunning():
            return

        note_type = self.anki_panel.note_type_input.text().strip()
        if not note_type:
            self.anki_panel.set_notetype_status(False, "Enter a note type name before fetching fields")
            return

        ankiconnect_url = self.anki_panel.ankiconnect_url_input.text().strip()
        # Patch the live config with the user's in-flight input values so the
        # service hits the URL/note type currently shown in the form, not
        # whatever was last saved to disk.
        probe_config = replace(
            self.config,
            anki_note_type=note_type,
            ankiconnect_url=ankiconnect_url or self.config.ankiconnect_url,
        )

        try:
            service = AnkiService(probe_config)
        except ValueError as e:
            # Misconfigured anki_fields keys — surface, don't crash.
            self.anki_panel.set_notetype_status(False, f"Cannot build AnkiService: {e}")
            return

        self.anki_panel.set_notetype_status(None, "Fetching fields from note type...")
        self.anki_panel.fetch_fields_button.setEnabled(False)

        worker = FetchFieldsWorker(service, note_type, self)
        self._fetch_fields_worker = worker
        worker.result_ready.connect(self._on_fetch_fields_finished)
        worker.error.connect(self._on_fetch_fields_error)
        worker.start()

    def _on_fetch_fields_finished(self, field_names: list[str]) -> None:
        """Populate the panel with the fetched field list (main-thread slot)."""
        self.anki_panel.fetch_fields_button.setEnabled(True)
        if not field_names:
            # Empty list means AnkiConnect rejected the request or returned
            # nothing — most commonly the note type doesn't exist, or Anki
            # isn't running. The status indicator is the existing affordance
            # for note-type problems, so reuse it.
            self.anki_panel.set_notetype_status(
                False, "Could not fetch fields. Is Anki running and the note type spelled right?"
            )
            return
        self.anki_panel.populate_from_field_list(field_names)
        self.anki_panel.set_notetype_status(True, f"Fetched {len(field_names)} fields and auto-mapped them")

    def _on_fetch_fields_error(self, message: str) -> None:
        """Surface an unexpected worker exception via the note-type status line."""
        self.anki_panel.fetch_fields_button.setEnabled(True)
        self.anki_panel.set_notetype_status(False, message)

    # === Card styling handlers (Issue #44) ===

    def _on_apply_styling_requested(self) -> None:
        """Apply the managed CSS block to the note type in a worker thread."""
        self._start_styling_worker("apply")

    def _on_remove_styling_requested(self) -> None:
        """Strip the managed CSS block from the note type in a worker thread."""
        self._start_styling_worker("remove")

    def _start_styling_worker(self, mode: str) -> None:
        """Read the note type's CSS, edit its managed block, and write it back.

        Like :meth:`_on_fetch_fields_requested`, this reads the note type and
        AnkiConnect URL straight from the panel inputs so the user can apply
        without first hitting Save. The buttons are disabled for the duration to
        prevent overlapping writes. Result lands on the main thread via
        :meth:`_on_styling_finished` / :meth:`_on_styling_error`.
        """
        # Don't stack worker threads — first request wins until it completes.
        if self._styling_worker is not None and self._styling_worker.isRunning():
            return

        note_type = self.anki_panel.note_type_input.text().strip()
        if not note_type:
            self.anki_panel.set_styling_status(False, "Enter a note type name before applying styles")
            return

        ankiconnect_url = self.anki_panel.ankiconnect_url_input.text().strip()
        probe_config = replace(
            self.config,
            anki_note_type=note_type,
            ankiconnect_url=ankiconnect_url or self.config.ankiconnect_url,
        )

        try:
            service = AnkiService(probe_config)
        except ValueError as e:
            self.anki_panel.set_styling_status(False, f"Cannot build AnkiService: {e}")
            return

        self.anki_panel.set_styling_buttons_enabled(False)

        worker = StylingWorker(
            service,
            mode="apply" if mode == "apply" else "remove",
            preset=self.anki_panel.get_card_style_preset(),
            custom_css=self.anki_panel.get_custom_css(),
            note_type=note_type,
            parent=self,
        )
        self._styling_worker = worker
        worker.finished_ok.connect(self._on_styling_finished)
        worker.error.connect(self._on_styling_error)
        worker.start()

    def _on_styling_finished(self, message: str) -> None:
        """Re-enable the styling buttons and report success (main-thread slot)."""
        self.anki_panel.set_styling_buttons_enabled(True)
        self.anki_panel.set_styling_status(True, message)

    def _on_styling_error(self, message: str) -> None:
        """Re-enable the styling buttons and surface the failure (main-thread slot)."""
        self.anki_panel.set_styling_buttons_enabled(True)
        self.anki_panel.set_styling_status(False, message)

    # === Excluded decks handlers (Issue #38) ===

    def _on_fetch_decks_requested(self) -> None:
        """Fetch the deck list from AnkiConnect to populate the exclude picker.

        Uses the AnkiConnect URL currently shown in the Anki panel (not the
        last-saved config) so the user can pick decks without hitting Save
        first. The picker opens when results arrive via
        :meth:`_on_fetch_decks_finished`.
        """
        if self._fetch_decks_worker is not None and self._fetch_decks_worker.isRunning():
            return

        ankiconnect_url = self.anki_panel.ankiconnect_url_input.text().strip()
        probe_config = replace(self.config, ankiconnect_url=ankiconnect_url or self.config.ankiconnect_url)
        try:
            service = AnkiService(probe_config)
        except ValueError as e:
            QMessageBox.warning(self, "Add Deck", f"Cannot build AnkiService: {e}")
            return

        self.filtering_panel.add_deck_button.setEnabled(False)
        worker = FetchDecksWorker(service, self)
        self._fetch_decks_worker = worker
        worker.result_ready.connect(self._on_fetch_decks_finished)
        worker.error.connect(self._on_fetch_decks_error)
        worker.start()

    def _on_fetch_decks_finished(self, deck_names: list[str]) -> None:
        """Hand the fetched deck list to the panel, which opens the picker."""
        self.filtering_panel.add_deck_button.setEnabled(True)
        if not deck_names:
            QMessageBox.warning(
                self,
                "Add Deck",
                "Could not fetch decks. Is Anki running with AnkiConnect?",
            )
            return
        self.filtering_panel.set_available_decks(deck_names)

    def _on_fetch_decks_error(self, message: str) -> None:
        """Surface an unexpected deck-fetch worker exception."""
        self.filtering_panel.add_deck_button.setEnabled(True)
        QMessageBox.warning(self, "Add Deck", message)

    def _on_rebuild_known_words(self) -> None:
        """Clear the local known-words cache after user confirmation.

        The cache is additive (see :class:`KnownWordDB`), so removing a deck's
        words after it was already synced requires a full rebuild. The next
        mining run re-syncs from Anki with the current exclusions applied.
        """
        confirm = QMessageBox.question(
            self,
            "Rebuild Known Words DB",
            "Clear the local known-words cache? It will re-sync from Anki on the "
            "next mining run, applying your current deck exclusions. Words you "
            "added yourself from the Word Curator are kept.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        try:
            db = KnownWordDB(self.config.known_words_db_path)
            db.initialize()
            # Preserve the user-curated ignore list (Issue #42); only the
            # Anki-synced rows are rebuilt from Anki on the next run.
            removed = db.clear(preserve_user=True)
        except Exception as e:  # noqa: BLE001 — surface any DB failure to the user
            QMessageBox.warning(self, "Rebuild Known Words DB", f"Could not clear the cache: {e}")
            return

        QMessageBox.information(
            self,
            "Rebuild Known Words DB",
            f"Cleared {removed} cached word(s). The cache will rebuild on the next run.",
        )

    def _on_manage_known_words(self) -> None:
        """Open the Manage Known Words dialog (Issue #42)."""
        from anki_miner.gui.widgets.dialogs.known_words_dialog import KnownWordsManagerDialog

        try:
            db = KnownWordDB(self.config.known_words_db_path)
            KnownWordsManagerDialog(db, self).exec()
        except Exception as e:  # noqa: BLE001 — surface any DB failure to the user
            QMessageBox.warning(self, "Manage Known Words", f"Could not open the known words list: {e}")
