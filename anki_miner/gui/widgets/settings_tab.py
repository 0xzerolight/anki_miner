"""Settings tab with category organization using extracted panels."""

import os
import re
from dataclasses import replace
from functools import partial
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from anki_miner.config import AnkiMinerConfig, AudioSourceEntry, ChainEntry
from anki_miner.gui.controllers.anki_probe_controller import AnkiProbeController
from anki_miner.gui.controllers.audio_pack_import_flow import AudioPackImportFlow
from anki_miner.gui.controllers.dictionary_import_flow import DictionaryImportFlow
from anki_miner.gui.controllers.zip_import_flow import YomitanCsvLabels, ZipImportFlow
from anki_miner.gui.resources.styles import SPACING
from anki_miner.gui.widgets.enhanced import ModernButton
from anki_miner.gui.widgets.panels import (
    AnkiSettingsPanel,
    AudioPackSettingsPanel,
    DictionarySettingsPanel,
    FilteringSettingsPanel,
    MediaSettingsPanel,
    ThemesPanel,
    YouTubeSettingsPanel,
)
from anki_miner.gui.workers.yomitan_csv_import_worker import YomitanCsvImportWorker
from anki_miner.services.frequency import import_yomitan_freq_zip
from anki_miner.services.known_word_db import KnownWordDB
from anki_miner.services.pitch_accent import import_yomitan_pitch_zip


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
        self._setup_ui()
        # Controllers (T-66) own worker lifecycles + dialogs; the tab keeps
        # widgets, signal wiring, and config assembly. Dependency is one-way:
        # tab → controller → workers/services (tab-owned collaboration points
        # are injected as callables).
        # Modal pitch/frequency zip import engine: owns the import workers,
        # the ``.pending`` staging files, and the deferred-promotion closures;
        # the tab keeps the per-flow wrappers + save-time ordering.
        self._zip_import_flow = ZipImportFlow(self)
        # Dictionary add/reimport orchestration, incl. the Reimport-All
        # chained state machine and its predecessor-join (T-09).
        self._dict_import_flow = DictionaryImportFlow(
            parent=self,
            panel=self.dictionary_panel,
            get_config=lambda: self.config,
            persist_chain=self._persist_chain_change,
            notify_config_changed=lambda: self.config_changed.emit(self.config),
        )
        # Audio pack add/reimport orchestration.
        self._audio_pack_import_flow = AudioPackImportFlow(
            parent=self,
            panel=self.audio_panel,
            get_config=lambda: self.config,
            persist_chain=self._persist_audio_chain_change,
        )
        # AnkiConnect probe workers (fetch fields / fetch decks / styling);
        # their live handles surface through iter_close_workers (T-12).
        self._anki_probe = AnkiProbeController(
            parent=self,
            anki_panel=self.anki_panel,
            filtering_panel=self.filtering_panel,
            get_config=lambda: self.config,
        )
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
        self.audio_panel = AudioPackSettingsPanel(self.config.audio_packs_root)
        self.filtering_panel = FilteringSettingsPanel()
        self.youtube_panel = YouTubeSettingsPanel()
        self.themes_panel = ThemesPanel(self.config.themes_root)

        # Add tabs with scroll areas for each panel
        self.tab_widget.addTab(self._wrap_in_scroll_area(self.anki_panel), "Anki")
        self.tab_widget.addTab(self._wrap_in_scroll_area(self.media_panel), "Media")
        self.tab_widget.addTab(self._wrap_in_scroll_area(self.dictionary_panel), "Dictionary")
        self.tab_widget.addTab(self._wrap_in_scroll_area(self.audio_panel), "Audio")
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

        # Inline, non-modal save confirmation (replaces the old "Settings Saved"
        # popup). Flashed by _flash_save_status() and auto-cleared by a timer.
        self.save_status_label = QLabel("")
        self.save_status_label.setObjectName("settings-save-status")
        button_layout.addWidget(self.save_status_label)

        self._save_status_timer = QTimer(self)
        self._save_status_timer.setSingleShot(True)
        self._save_status_timer.timeout.connect(lambda: self.save_status_label.setText(""))

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
        self.anki_panel.fetch_fields_requested.connect(self._anki_probe.fetch_fields)
        self.anki_panel.apply_styling_requested.connect(self._anki_probe.apply_styling)
        self.anki_panel.remove_styling_requested.connect(self._anki_probe.remove_styling)

        # Dictionary panel signals — wire Add/Reimport to the import flow
        # controller, which owns the worker dialogs (T-66).
        self.dictionary_panel.add_dict_requested.connect(self._dict_import_flow.add_dict)
        self.dictionary_panel.reimport_jmdict_requested.connect(self._dict_import_flow.reimport_jmdict)
        self.dictionary_panel.reimport_dict_requested.connect(self._dict_import_flow.reimport_dict)
        self.dictionary_panel.reimport_all_requested.connect(self._dict_import_flow.reimport_all)
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

        # Audio panel signals — wire Add/Reimport to the import flow controller.
        self.audio_panel.add_pack_requested.connect(self._audio_pack_import_flow.add_pack)
        self.audio_panel.reimport_pack_requested.connect(self._audio_pack_import_flow.reimport_pack)
        # Persist chain immediately after reorder/toggle or destructive remove.
        # Removal also emits chain_changed (after pack deletion succeeds), so
        # this single wiring covers it; wiring pack_removed too would persist
        # the same chain twice. pack_removed stays unconnected until a
        # consumer needs the removal-specific notification.
        self.audio_panel.chain_changed.connect(lambda: self._persist_audio_chain_change(self.audio_panel.get_chain()))

        # Filtering panel: excluded-decks picker + known-words cache rebuild (Issue #38).
        self.filtering_panel.fetch_decks_requested.connect(self._anki_probe.fetch_decks)
        self.filtering_panel.rebuild_known_words_requested.connect(self._on_rebuild_known_words)
        self.filtering_panel.manage_known_words_requested.connect(self._on_manage_known_words)

        # Themes panel persists immediately on any change (live-preview model).
        self.themes_panel.state_changed.connect(self._on_theme_state_changed)
        self.themes_panel.font_scale_changed.connect(self._on_font_scale_changed)

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

        # Expression audio toggle (Issue #73)
        self.anki_panel.set_expression_audio_enabled(self.config.expression_audio_enabled)

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

        # Audio source chain
        self.audio_panel.set_chain(self.config.expression_audio_chain)

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
            # Expression audio toggle (Issue #73)
            expression_audio_enabled=self.anki_panel.get_expression_audio_enabled(),
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
            # Audio source chain
            expression_audio_chain=self.audio_panel.get_chain(),
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
        self._flash_save_status("✓ Saved")

    def _flash_save_status(self, text: str) -> None:
        """Show a transient, non-modal confirmation beside the Save button.

        Restarts the auto-clear timer on each call so repeated saves keep the
        message visible for the full duration.
        """
        self.save_status_label.setText(text)
        self._save_status_timer.stop()
        self._save_status_timer.start(2500)

    def _resolve_pitch_accent_path(self) -> Path | None:
        """Resolve the pitch-accent selector for persistence (see the engine).

        Thin wrapper over :meth:`ZipImportFlow.run_modal_zip_import`; see it
        for the full staging/deferred-promotion contract and the meaning of
        each return.
        """
        return self._zip_import_flow.run_modal_zip_import(
            selector=self.dictionary_panel.pitch_accent_selector,
            dest_name="pitch_accent.csv",
            worker_factory=partial(YomitanCsvImportWorker, import_yomitan_pitch_zip),
            worker_slot_attr="_active_pitch_worker",
            commit_slot_attr="_pending_pitch_commit",
            decline_fallback=self.config.pitch_accent_path,
            labels=YomitanCsvLabels(
                progress="Importing pitch accent dictionary…",
                overwrite_title="Overwrite Pitch Accent File?",
                failure_title="Pitch Accent Import Failed",
                success_title="Pitch accent dictionary imported",
            ),
        )

    def _resolve_frequency_path(self) -> Path | None:
        """Resolve the frequency selector for persistence (see the engine).

        Thin wrapper over :meth:`ZipImportFlow.run_modal_zip_import`; see it
        for the full staging/deferred-promotion contract and the meaning of
        each return.
        """
        return self._zip_import_flow.run_modal_zip_import(
            selector=self.filtering_panel.frequency_selector,
            dest_name="frequency.csv",
            worker_factory=partial(YomitanCsvImportWorker, import_yomitan_freq_zip),
            worker_slot_attr="_active_freq_worker",
            commit_slot_attr="_pending_freq_commit",
            decline_fallback=self.config.frequency_list_path,
            labels=YomitanCsvLabels(
                progress="Importing frequency dictionary…",
                overwrite_title="Overwrite Frequency List?",
                failure_title="Frequency Import Failed",
                success_title="Frequency dictionary imported",
            ),
        )

    def _commit_pending_csv_imports(self) -> None:
        """Promote any staged pitch/frequency CSV imports (delegates to the flow)."""
        self._zip_import_flow.commit_pending_csv_imports()

    def _discard_pending_csv_imports(self) -> None:
        """Drop staged pitch/frequency promotions (delegates to the flow)."""
        self._zip_import_flow.discard_pending_csv_imports()

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
            self._flash_save_status("✓ Reset to defaults")

    def update_config(self, config: AnkiMinerConfig) -> None:
        """Update configuration from external source.

        Args:
            config: New configuration to load
        """
        self.config = config
        self._load_config()

    def iter_close_workers(self) -> tuple:
        """Live worker handles MainWindow must join on close (T-12).

        Stable hook consumed by ``MainWindow.closeEvent``: yields the three
        short-lived AnkiConnect probe workers (fetch fields, fetch decks,
        apply/remove styling), which T-66 lifted into
        :class:`AnkiProbeController` — this method delegates there. See
        :meth:`AnkiProbeController.iter_close_workers` for the join-policy
        rationale.
        """
        return self._anki_probe.iter_close_workers()

    def shutdown(self) -> None:
        """Cancel every running AnkiConnect worker (cancel only, no wait).

        Explicit-teardown entry point mirroring the YouTube tab; delegates to
        :class:`AnkiProbeController`, which owns the workers (T-66).
        """
        self._anki_probe.shutdown()

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

    # === Dictionary chain persistence ===

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

    def _persist_audio_chain_change(self, new_chain: tuple[AudioSourceEntry, ...]) -> None:
        """Save an audio chain mutation to disk and notify listeners.

        Called after a successful audio pack import or a destructive remove so
        the freshly-imported pack is reachable on the very next lookup without
        requiring the user to click Save in Settings.
        """
        new_config = replace(self.config, expression_audio_chain=new_chain)
        self.config = new_config
        self.config_changed.emit(new_config)

    # === Known words handlers (Issues #38 / #42) ===

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
