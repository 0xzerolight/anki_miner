"""Pitch accent sources settings panel.

Reorderable chain of pitch accent sources, mirroring
:class:`~anki_miner.gui.widgets.panels.frequency_settings_panel.FrequencySettingsPanel`.
Replaces the old single-file "Pitch Accent" picker: the user adds, reorders,
enables/disables, and removes multiple pitch dictionaries, each backed by a
per-source ``index.sqlite`` under ``config.pitch_root/<source_id>/``.

Unlike the additive frequency chain, pitch resolves FIRST-HIT-WINS in chain
order — the top source with an entry for a word wins, and lower sources only
fill words the higher ones miss.

Pitch activation is resource-driven: adding an enabled source here turns the
feature on (``config.pitch_active``). There is no separate on/off checkbox.

Everything it shares with the frequency panel lives in
:class:`~anki_miner.gui.widgets.panels._source_chain_settings_panel._SourceChainSettingsPanel`;
this module keeps the strings, the entry type and the remove confirmations.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from PyQt6.QtWidgets import QMessageBox

from anki_miner.config import PitchSourceEntry
from anki_miner.gui.widgets.panels._source_chain_settings_panel import (
    _SourceChainPanelLabels,
    _SourceChainSettingsPanel,
)
from anki_miner.gui.widgets.panels.chain_settings_panel_base import ChainListLabels, _ChainPanelStrings
from anki_miner.services.pitch_accent.registry import PitchSourceRegistry
from anki_miner.utils.i18n import tr_format


class PitchSettingsPanel(_SourceChainSettingsPanel):
    """Reorderable chain of first-hit-wins pitch accent sources."""

    ANCHOR_NAMESPACE = "pitch"

    _SCAN_ERROR_LABEL = "Pitch registry scan failed"
    _REMOVE_ERROR_NOUN = "pitch source folder"
    _REGISTRY_CLS = PitchSourceRegistry
    _SLOT_KIND = "pitch"
    _FORMAT_LABELS: ClassVar[dict[str, str]] = {
        "yomitan-pitch": "Yomitan",
        "csv": "CSV",
    }

    def __init__(self, pitch_root: Path, parent=None):
        super().__init__(self.tr("Pitch Accent"), pitch_root, parent=parent)
        self._strings = _ChainPanelStrings(
            loading=self.tr("Loading…"),
            retry_label=self.tr("Retry"),
            scan_failed_summary=self.tr("Installed pitch accent sources could not be checked."),
            files_left_summary=self.tr("The pitch source was removed from the chain; no files were deleted from disk."),
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
                "%1 was removed, but its leftover folder could not be deleted. " "Cleanup will be retried at startup."
            ),
        )
        self._labels = _SourceChainPanelLabels(
            section=self.tr("Active Pitch Accent Sources"),
            restore=self.tr("Restore from Disk"),
            restore_tooltip=self.tr(
                "Re-add pitch sources found in the storage folder that aren't in the list above. No re-import needed."
            ),
            reimport_all=self.tr("Reimport All"),
            reimport_all_tooltip=self.tr(
                "Rebuild every pitch source in the list from the copy saved when it was imported. "
                "Needed after an app upgrade changes the index format."
            ),
            chain=ChainListLabels(
                explanation=self.tr("Checked top to bottom — the first source with a pitch entry for a word wins."),
                add=self.tr("Add pitch source…"),
                remove=self.tr("Remove pitch source"),
                remove_tooltip=self.tr("Remove the selected pitch accent source"),
                move_up=self.tr("Move up"),
                move_up_tooltip=self.tr("Move up (wins lookups first)"),
                move_down=self.tr("Move down"),
                move_down_tooltip=self.tr("Move down (checked after the rows above)"),
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
    def _pitch_root(self) -> Path:
        """The pitch storage root this panel scans (``config.pitch_root``)."""
        return self._source_root

    def set_pitch_root(self, pitch_root: Path) -> None:
        """:meth:`set_source_root` under this panel's config field name."""
        self.set_source_root(pitch_root)

    # ------------------------------------------------------------------
    # Chain-panel hooks
    # ------------------------------------------------------------------

    def _entry_with_enabled(self, entry: PitchSourceEntry, enabled: bool) -> PitchSourceEntry:
        return PitchSourceEntry(source_id=entry.source_id, enabled=enabled)

    def _confirm_remove(self, display: str, *, body: str | None = None) -> bool:
        if body is None:
            body = self.tr(
                "Remove '%1' from the pitch accent chain?\n\n"
                "Only the index files are deleted. Adding it back needs the source file."
            )
        reply = QMessageBox.question(
            self,
            self.tr("Remove pitch source"),
            tr_format(body, display),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return reply == QMessageBox.StandardButton.Yes

    def _confirm_chain_only_remove(self, display: str) -> bool:
        return self._confirm_remove(
            display,
            body=self.tr("Remove '%1' from the pitch accent chain?\n\nNo index files are deleted."),
        )
