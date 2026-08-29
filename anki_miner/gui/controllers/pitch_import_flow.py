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


def _tr(text: str) -> str:
    return QCoreApplication.translate("PitchImportFlow", text)


class PitchImportFlow(SourceChainImportFlow):
    """Drives pitch-source imports for the Settings → Pitch Accent panel."""

    @property
    def _labels(self) -> SourceFlowLabels:
        return SourceFlowLabels(
            picker_add_caption=_tr("Choose pitch accent source"),
            picker_reimport_caption=_tr("Choose pitch source to re-import"),
            picker_filter_template=_tr("Pitch accent source (%1);;All Files (*)"),
            scan_failed=_tr("That folder could not be scanned."),
            resources_in_use=_tr(
                "Indexed resources are in use by mining, startup prewarm, or card backfill. "
                "Wait for the active task to finish and try again."
            ),
            settings_update_failed=_tr("The import finished, but the settings could not be updated."),
            refusal=_tr("Another import is still finishing. Wait for it to finish and try again."),
            missing_result=_tr("The import worker finished without a completion result."),
            cancel=_tr("Cancel"),
            cancelling=_tr("Cancelling…"),
            add_progress=_tr("Importing pitch source…"),
            add_failure_summary=_tr("The pitch source could not be imported."),
            added_title=_tr("Pitch Source Added"),
            added_body_template=_tr("Imported %1 entries from '%2'."),
            picker_add_multi_caption=_tr("Choose pitch accent sources"),
            added_batch_title=_tr("Pitch Sources Added"),
            added_batch_header_template=_tr("Imported %1 pitch sources:"),
            reimport_progress=_tr("Re-importing pitch source…"),
            reimport_failure_summary=_tr("The pitch source could not be re-imported."),
            reimported_title=_tr("Pitch Source Re-imported"),
            reimported_body_template=_tr("Re-imported %1 successfully."),
            batch_progress_template=_tr("Pitch source %1 of %2: %3"),
            batch_failure_summary=_tr("The pitch sources could not be re-imported."),
            batch_title=_tr("Reimport All"),
            batch_reimported_header_template=_tr("Reimported %1 pitch source(s):"),
            batch_skipped_header=_tr("Skipped (no saved copy to rebuild from; use per-row Re-import…):"),
            batch_failed_header=_tr("Failed:"),
            batch_cancelled=_tr("Cancelled before remaining pitch sources."),
            batch_done=_tr("Done."),
            nothing_title=_tr("Nothing to reimport"),
            nothing_empty_chain=_tr("No pitch sources in the chain."),
            nothing_skipped_header=_tr(
                "No pitch sources could be rebuilt automatically.\n\n"
                "Skipped (no saved copy to rebuild from; use per-row Re-import…):\n"
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
        return tr_format(_tr(" (skipped %1 malformed entries)"), f"{skipped:,}")
