"""Frequency-source import orchestration (add / reimport one / reimport all).

The flow itself lives in
:class:`~anki_miner.gui.controllers.source_chain_import_flow.SourceChainImportFlow`,
shared with pitch. This module supplies only what is specific to frequency: the
root, the accepted suffixes, the worker factories, the chain entry type, the
word-based / occurrence-based disclosures, and every user-facing string.

Those strings stay here on purpose. ``lupdate`` resolves a translation context
statically, and the ``FrequencyImportFlow`` context already carries twelve
catalogs' worth of translations — moving a literal into the shared module would
orphan every one of them.

Frequency sources are purely additive, so a freshly imported source is simply
*appended* (enabled) to the chain; the user reorders later if they care about
tie-breaks.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QCoreApplication

from anki_miner.config import AnkiMinerConfig, FreqEntry
from anki_miner.gui.controllers.source_chain_import_flow import (
    SourceChainImportFlow,
    SourceFlowLabels,
)
from anki_miner.gui.workers.import_worker import ImportWorker
from anki_miner.services.frequency import storage
from anki_miner.services.frequency.source_importer import FREQUENCY_SOURCE_SUFFIXES
from anki_miner.utils.i18n import tr_format


def _tr(text: str) -> str:
    return QCoreApplication.translate("FrequencyImportFlow", text)


class FrequencyImportFlow(SourceChainImportFlow):
    """Drives frequency-source imports for the Settings → Frequency panel."""

    @property
    def _labels(self) -> SourceFlowLabels:
        return SourceFlowLabels(
            picker_add_caption=_tr("Choose frequency source"),
            picker_reimport_caption=_tr("Choose frequency source to re-import"),
            picker_filter_template=_tr("Frequency source (%1);;All Files (*)"),
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
            add_progress=_tr("Importing frequency source…"),
            add_failure_summary=_tr("The frequency source could not be imported."),
            added_title=_tr("Frequency Source Added"),
            added_body_template=_tr("Imported %1 entries from '%2'."),
            picker_add_multi_caption=_tr("Choose frequency sources"),
            added_batch_title=_tr("Frequency Sources Added"),
            added_batch_header_template=_tr("Imported %1 frequency sources:"),
            reimport_progress=_tr("Re-importing frequency source…"),
            reimport_failure_summary=_tr("The frequency source could not be re-imported."),
            reimported_title=_tr("Frequency Source Re-imported"),
            reimported_body_template=_tr("Re-imported %1 successfully."),
            batch_progress_template=_tr("Frequency source %1 of %2: %3"),
            batch_failure_summary=_tr("The frequency sources could not be re-imported."),
            batch_title=_tr("Reimport All"),
            batch_reimported_header_template=_tr("Reimported %1 frequency source(s):"),
            batch_skipped_header=_tr("Skipped (no saved copy to rebuild from; use per-row Re-import…):"),
            batch_failed_header=_tr("Failed:"),
            batch_cancelled=_tr("Cancelled before remaining frequency sources."),
            batch_done=_tr("Done."),
            nothing_title=_tr("Nothing to reimport"),
            nothing_empty_chain=_tr("No frequency sources in the chain."),
            nothing_skipped_header=_tr(
                "No frequency sources could be rebuilt automatically.\n\n"
                "Skipped (no saved copy to rebuild from; use per-row Re-import…):\n"
            ),
        )

    @property
    def _suffixes(self) -> tuple[str, ...]:
        return FREQUENCY_SOURCE_SUFFIXES

    @property
    def _trace_noun(self) -> str:
        return "frequency"

    def _dest_root(self, config: AnkiMinerConfig) -> Path:
        return config.freqs_root

    def _make_entry(self, source_id: str) -> FreqEntry:
        return FreqEntry(source_id=source_id, enabled=True)

    def _read_source_name(self, db_path: Path) -> str | None:
        try:
            stored = storage.read_meta(db_path).get("source_name")
        except Exception:  # noqa: BLE001 — corrupt metadata must not strand a saved source
            return None
        return stored if isinstance(stored, str) else None

    def _make_add_worker(self, source_file: Path, dest_root: Path) -> ImportWorker:
        return ImportWorker.for_source(source_file, dest_root, overwrite=False)

    def _make_repair_worker(
        self,
        source_file: Path,
        dest_root: Path,
        *,
        source_id: str,
        source_name: str,
    ) -> ImportWorker:
        return ImportWorker.for_source_repair(
            source_file,
            dest_root,
            source_id=source_id,
            source_name=source_name,
        )

    def _extra_add_notes(self, meta: dict) -> str:
        """Disclose what the importer did to a file the user is seeing first time.

        Rows it could not parse, counts it converted to ranks, and whether the
        source turned out to be word-based — all three change what the user
        gets, so none is left silent.
        """
        notes = ""
        skipped = meta.get("skipped_malformed", 0)
        if skipped:
            notes += tr_format(_tr(" (skipped %1 malformed entries)"), f"{skipped:,}")
        if meta.get("converted_to_ranks"):
            notes += _tr(" This is an occurrence-based source; its counts were converted to ranks.")
        return notes + self._categorical_note(meta)

    def _extra_reimport_notes(self, meta: dict) -> str:
        """Only the word-based disclosure survives a reimport.

        Skipped-row and count-conversion counts describe a first look at a file;
        on a rebuild of an already-known source they are noise. Being word-based
        still governs whether its ranks filter, so it stays.
        """
        return self._categorical_note(meta)

    @staticmethod
    def _categorical_note(meta: dict) -> str:
        """Note for a word-based source, whose levels are excluded from rank filtering."""
        if not meta.get("is_categorical"):
            return ""
        return _tr(
            " This is a word-based source; its level labels show on the card but don't affect "
            "frequency-rank filtering."
        )
