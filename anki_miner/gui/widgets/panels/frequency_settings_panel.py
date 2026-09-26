"""Frequency sources settings panel.

Reorderable chain of additive frequency sources, mirroring
:class:`~anki_miner.gui.widgets.panels.audio_pack_settings_panel.AudioPackSettingsPanel`.
Replaces the old single-file "Frequency List" picker: the user adds, reorders,
enables/disables, and removes multiple frequency rank lists, each backed by a
per-source ``index.sqlite`` under ``config.freqs_root/<source_id>/``.

Frequency activation is resource-driven: adding an enabled source here turns the
feature on (``config.frequency_active``). There is no separate on/off checkbox.

Everything it shares with the pitch panel lives in
:class:`~anki_miner.gui.widgets.panels._source_chain_settings_panel._SourceChainSettingsPanel`;
this module keeps the strings, the entry type and the remove confirmations.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from PyQt6.QtWidgets import QMessageBox

from anki_miner.config import FreqEntry
from anki_miner.gui.widgets.panels._source_chain_settings_panel import (
    _SourceChainPanelLabels,
    _SourceChainSettingsPanel,
)
from anki_miner.gui.widgets.panels.chain_settings_panel_base import ChainListLabels, _ChainPanelStrings
from anki_miner.services.frequency.registry import FreqSourceMeta, FrequencySourceRegistry
from anki_miner.utils.i18n import tr_format


class FrequencySettingsPanel(_SourceChainSettingsPanel):
    """Reorderable chain of additive frequency sources."""

    ANCHOR_NAMESPACE = "frequency"

    _SCAN_ERROR_LABEL = "Frequency registry scan failed"
    _REMOVE_ERROR_NOUN = "frequency source folder"
    _REGISTRY_CLS = FrequencySourceRegistry
    _SLOT_KIND = "frequency"
    _FORMAT_LABELS: ClassVar[dict[str, str]] = {
        "yomitan-freq": "Yomitan",
        "csv": "CSV",
    }

    def __init__(self, freqs_root: Path, parent=None):
        super().__init__(self.tr("Frequency"), freqs_root, parent=parent)
        self._strings = _ChainPanelStrings(
            loading=self.tr("Loading…"),
            retry_label=self.tr("Retry"),
            scan_failed_summary=self.tr("Installed frequency sources could not be checked."),
            files_left_summary=self.tr(
                "The frequency source was removed from the chain; no files were deleted from disk."
            ),
            intact_failure_summary=self.tr("%1 could not be removed. Its files are intact — try again."),
            partial_failure_summary=self.tr("%1 was only partly removed. Re-import it before retrying."),
            config_pending_failure_summary=self.tr(
                "%1 could not be removed: its settings could not be saved. Restart Anki Miner and try again."
            ),
            post_save_summary=self.tr(
                "%1 was removed, but Anki Miner could not refresh it. "
                "The removal is saved and will remain after a restart."
            ),
            cleanup_pending_summary=self.tr(
                "%1 was removed, but its leftover folder could not be deleted. Cleanup will be retried at startup."
            ),
        )
        self._labels = _SourceChainPanelLabels(
            section=self.tr("Active Frequency Sources"),
            restore=self.tr("Restore from Disk"),
            restore_tooltip=self.tr(
                "Re-add frequency sources found in the storage folder that aren't in the list above. "
                "No re-import needed."
            ),
            reimport_all=self.tr("Reimport All"),
            reimport_all_tooltip=self.tr(
                "Rebuild every frequency source in the list from the copy saved when it was imported. "
                "Needed after an app upgrade changes the index format."
            ),
            chain=ChainListLabels(
                # Not the first-match sentence the other three chains carry:
                # frequency layers every enabled source, while order controls
                # only the source list rendered on cards.
                explanation=self.tr(
                    "Every enabled source counts: filtering uses the lowest rank, Frequency "
                    "Sort the harmonic mean. Order only sets the card's source list."
                ),
                add=self.tr("Add frequency source…"),
                remove=self.tr("Remove frequency source"),
                remove_tooltip=self.tr("Remove the selected frequency source"),
                move_up=self.tr("Move up"),
                move_up_tooltip=self.tr("Move up in the card's source list"),
                move_down=self.tr("Move down"),
                move_down_tooltip=self.tr("Move down in the card's source list"),
                more=self.tr("More"),
                more_tooltip=self.tr("More actions"),
            ),
            entries=self.tr("%1 entries"),
            enabled=self.tr("Enabled"),
            enable=self.tr("Enable %1"),
            enable_or_disable=self.tr("Enable or disable %1"),
            repair=self.tr("Re-import"),
            stale_warning=self.tr("⚠ re-import required (app upgrade)"),
            missing_warning=self.tr("⚠ missing — re-import"),
            resources_in_use=self.tr("Another task is using the indexed resources — try again when it finishes."),
            menu_reimport=self.tr("Re-import…"),
            menu_remove=self.tr("Remove"),
        )
        self._setup_fields()

    @property
    def _freqs_root(self) -> Path:
        """The frequency storage root this panel scans (``config.freqs_root``)."""
        return self._source_root

    def set_freqs_root(self, freqs_root: Path) -> None:
        """:meth:`set_source_root` under this panel's config field name."""
        self.set_source_root(freqs_root)

    # ------------------------------------------------------------------
    # Chain-panel hooks
    # ------------------------------------------------------------------

    def _entry_with_enabled(self, entry: FreqEntry, enabled: bool) -> FreqEntry:
        return FreqEntry(source_id=entry.source_id, enabled=enabled)

    def _extra_row_metadata(self, meta: FreqSourceMeta) -> tuple[tuple[str, ...], str]:
        if not meta.is_categorical:
            return (), ""
        # Word-based sources hold level labels (N5/Basic) shown on the
        # card but excluded from the frequency-rank cutoff.
        return (self.tr("word-based"),), self.tr(
            "Level labels are shown on the card but not used for frequency filtering."
        )

    def _confirm_remove(self, display: str, *, body: str | None = None) -> bool:
        if body is None:
            body = self.tr(
                "Remove '%1' from the frequency chain?\n\n"
                "Only the index files are deleted. Adding it back needs the source file."
            )
        reply = QMessageBox.question(
            self,
            self.tr("Remove frequency source"),
            tr_format(body, display),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return reply == QMessageBox.StandardButton.Yes

    def _confirm_chain_only_remove(self, display: str) -> bool:
        return self._confirm_remove(
            display,
            body=self.tr("Remove '%1' from the frequency chain?\n\nNo index files are deleted."),
        )
