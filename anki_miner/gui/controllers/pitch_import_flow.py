"""Pitch-source import orchestration (add / reimport one / reimport all).

The flow itself lives in
:class:`~anki_miner.gui.controllers.source_chain_import_flow.SourceChainImportFlow`,
shared with frequency. This module supplies only what is specific to pitch: the
root, the accepted suffixes, the worker factories, the chain entry type, and
every user-facing string.

Those strings stay here on purpose. ``lupdate`` resolves a translation context
statically, and the ``PitchImportFlow`` context already carries twelve catalogs'
worth of translations — moving a literal into the shared module would orphan
every one of them.

A freshly imported source is *appended* (enabled) to the chain. Under
first-hit-wins that means it starts as the lowest-priority filler; the user
reorders it upward if it should win overlaps.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QCoreApplication

from anki_miner.config import AnkiMinerConfig, PitchSourceEntry
from anki_miner.gui.controllers.source_chain_import_flow import (
    SourceChainImportFlow,
    SourceFlowLabels,
)
from anki_miner.gui.workers.import_worker import ImportWorker
from anki_miner.languages.registry import config_language
from anki_miner.services._sqlite_index import language_kwarg
from anki_miner.services.pitch_accent import storage
from anki_miner.services.pitch_accent.source_importer import PITCH_SOURCE_SUFFIXES
from anki_miner.utils.i18n import tr_format


class PitchImportFlow(SourceChainImportFlow):
    """Drives pitch-source imports for the Settings → Pitch Accent panel."""

    @property
    def _labels(self) -> SourceFlowLabels:
        return SourceFlowLabels(
            picker_add_caption=QCoreApplication.translate("PitchImportFlow", "Choose pitch accent source"),
            picker_reimport_caption=QCoreApplication.translate("PitchImportFlow", "Choose pitch source to re-import"),
            picker_filter_template=QCoreApplication.translate(
                "PitchImportFlow", "Pitch accent source (%1);;All Files (*)"
            ),
            scan_failed=QCoreApplication.translate(
                "PitchImportFlow", "Installed pitch accent sources could not be checked."
            ),
            resources_in_use=QCoreApplication.translate(
                "PitchImportFlow", "Another task is using the indexed resources — try again when it finishes."
            ),
            settings_update_failed=QCoreApplication.translate(
                "PitchImportFlow", "The import finished, but the settings could not be updated."
            ),
            refusal=QCoreApplication.translate(
                "PitchImportFlow", "Another import is still finishing. Wait for it to finish and try again."
            ),
            missing_result=QCoreApplication.translate(
                "PitchImportFlow", "The import stopped before it finished. Try again."
            ),
            cancel=QCoreApplication.translate("PitchImportFlow", "Cancel"),
            cancelling=QCoreApplication.translate("PitchImportFlow", "Cancelling…"),
            add_progress=QCoreApplication.translate("PitchImportFlow", "Importing pitch source…"),
            add_failure_summary=QCoreApplication.translate(
                "PitchImportFlow", "The pitch source could not be imported."
            ),
            added_title=QCoreApplication.translate("PitchImportFlow", "Pitch Source Added"),
            added_body_template=QCoreApplication.translate("PitchImportFlow", "Imported %1 entries from '%2'."),
            picker_add_multi_caption=QCoreApplication.translate("PitchImportFlow", "Choose pitch accent sources"),
            added_batch_title=QCoreApplication.translate("PitchImportFlow", "Pitch Sources Added"),
            added_batch_header_template=QCoreApplication.translate("PitchImportFlow", "Imported %1 pitch sources:"),
            added_batch_done=QCoreApplication.translate("PitchImportFlow", "Nothing was imported."),
            reimport_progress=QCoreApplication.translate("PitchImportFlow", "Re-importing pitch source…"),
            reimport_failure_summary=QCoreApplication.translate(
                "PitchImportFlow", "The pitch source could not be re-imported."
            ),
            reimported_title=QCoreApplication.translate("PitchImportFlow", "Pitch Source Re-imported"),
            reimported_body_template=QCoreApplication.translate("PitchImportFlow", "Re-imported %1."),
            batch_progress_template=QCoreApplication.translate("PitchImportFlow", "Pitch source %1 of %2: %3"),
            batch_failure_summary=QCoreApplication.translate(
                "PitchImportFlow", "The pitch sources could not be re-imported."
            ),
            batch_title=QCoreApplication.translate("PitchImportFlow", "Reimport All"),
            batch_reimported_header_template=QCoreApplication.translate(
                "PitchImportFlow", "Reimported %1 pitch source(s):"
            ),
            batch_skipped_header=QCoreApplication.translate(
                "PitchImportFlow", "Skipped (no saved copy to rebuild from; use per-row Re-import…):"
            ),
            batch_failed_header=QCoreApplication.translate("PitchImportFlow", "Failed:"),
            batch_cancelled=QCoreApplication.translate("PitchImportFlow", "Cancelled before remaining pitch sources."),
            batch_done=QCoreApplication.translate("PitchImportFlow", "Nothing was re-imported."),
            nothing_title=QCoreApplication.translate("PitchImportFlow", "Nothing to reimport"),
            nothing_empty_chain=QCoreApplication.translate("PitchImportFlow", "No pitch sources in the chain."),
            nothing_skipped_header=QCoreApplication.translate(
                "PitchImportFlow", "Skipped (no saved copy to rebuild from; use per-row Re-import…):\n"
            ),
        )

    @property
    def _suffixes(self) -> tuple[str, ...]:
        return PITCH_SOURCE_SUFFIXES

    @property
    def _trace_noun(self) -> str:
        return "pitch"

    def _dest_root(self, config: AnkiMinerConfig) -> Path:
        return config.pitch_root

    def _make_entry(self, source_id: str) -> PitchSourceEntry:
        return PitchSourceEntry(source_id=source_id, enabled=True)

    def _read_source_name(self, db_path: Path) -> str | None:
        try:
            stored = storage.read_meta(db_path).get("source_name")
        except Exception:  # noqa: BLE001 — corrupt metadata must not strand a saved source
            return None
        return stored if isinstance(stored, str) else None

    def _make_add_worker(self, source_file: Path, dest_root: Path) -> ImportWorker:
        # Stamped with the language it is added for; the repair factory below
        # takes none, so a rebuild keeps the slot's own stamp.
        return ImportWorker.for_pitch_source(
            source_file,
            dest_root,
            overwrite=False,
            **language_kwarg(config_language(self._get_config())),
        )

    def _make_repair_worker(
        self,
        source_file: Path,
        dest_root: Path,
        *,
        source_id: str,
        source_name: str,
    ) -> ImportWorker:
        return ImportWorker.for_pitch_source_repair(
            source_file,
            dest_root,
            source_id=source_id,
            source_name=source_name,
        )

    def _extra_add_notes(self, meta: dict) -> str:
        """Report malformed rows the importer skipped, so a partial import isn't silent."""
        skipped = meta.get("skipped_malformed", 0)
        if not skipped:
            return ""
        return tr_format(
            QCoreApplication.translate("PitchImportFlow", " (skipped %1 malformed entries)"), f"{skipped:,}"
        )
