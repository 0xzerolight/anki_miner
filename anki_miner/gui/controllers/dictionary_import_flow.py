"""Dictionary import orchestration (add / per-row reimport / JMdict / Reimport All).

Extracted from ``SettingsTab`` (T-66). Owns the ``ImportWorker``
lifecycles and every dialog in the import flows — including the Reimport-All
chained state machine and its predecessor deferral (T-09). The tab keeps the
panel widgets, the signal wiring, and the narrow chain persist
(``_persist_chain_change``), injected here as callables so the dependency
stays one-way: tab → controller → workers/services.
"""

import logging
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from PyQt6.QtCore import QCoreApplication
from PyQt6.QtWidgets import QMessageBox, QWidget

from anki_miner.config import AnkiMinerConfig, ChainEntry
from anki_miner.gui.controllers.import_flow_common import (
    ModalImportFlowMixin,
    _begin_import_trace,
    _ChainedImportResult,
    _log_import_persist,
    _log_import_picker_enter,
    _log_import_picker_return,
    _OnceCallback,
    format_batch_summary,
)
from anki_miner.gui.utils import file_dialogs
from anki_miner.gui.utils.dialog_paths import resolve_start_dir
from anki_miner.gui.widgets.panels import DictionarySettingsPanel
from anki_miner.gui.widgets.panels.chain_settings_panel_base import MutationToken
from anki_miner.gui.workers.import_worker import ImportWorker
from anki_miner.languages.registry import config_language
from anki_miner.services._sqlite_index import (
    language_kwarg,
    prove_owned_slot,
    resolve_managed_slot,
    slot_language_kwarg,
)
from anki_miner.services.dictionary.importers.yomitan_importer import (
    derive_dict_id_from_zip,
    read_yomitan_title,
)
from anki_miner.services.dictionary.registry import DictionaryRegistry, DictMeta
from anki_miner.services.dictionary.storage import read_meta
from anki_miner.services.dictionary.superseded import strip_date_bracket
from anki_miner.services.resource_catalog import CATALOG_DICT_SLOT_IDS, LEGACY_DICT_SLOT_IDS
from anki_miner.utils.i18n import tr_format

logger = logging.getLogger(__name__)

# Slots whose on-disk id is pinned (stable, not title-derived): current catalog
# dicts plus former catalog dicts existing users still have installed.
_PINNED_DICT_SLOT_IDS = CATALOG_DICT_SLOT_IDS | LEGACY_DICT_SLOT_IDS


class DictionaryImportFlow(ModalImportFlowMixin):
    """Drives dictionary zip/XML imports for the Settings → Dictionary panel.

    Args:
        parent: Widget used as the Qt parent for dialogs (the settings tab),
            preserving modality and ``findChild`` discoverability.
        panel: The dictionary settings panel (chain state, registry refresh,
            resource-release hook, import-trigger buttons).
        get_config: Returns the tab's *current* config (it is reassigned on
            every save/persist, so a snapshot would go stale).
        persist_chain: The tab's narrow chain persist
            (``SettingsTab._persist_chain_change``) — saves a chain mutation
            to disk and notifies listeners without running the full Save
            pipeline.
        notify_config_changed: Re-emits ``config_changed`` with the current
            config so cached DefinitionService instances rebuild after a
            reimport rewrites an index in place (no chain change).
    """

    def __init__(
        self,
        parent: QWidget,
        panel: DictionarySettingsPanel,
        get_config: Callable[[], AnkiMinerConfig],
        persist_chain: Callable[[tuple[ChainEntry, ...]], None],
        notify_config_changed: Callable[[], None],
    ) -> None:
        self._parent = parent
        self._panel = panel
        self._get_config = get_config
        self._persist_chain = persist_chain
        self._notify_config_changed = notify_config_changed
        # Long-lived worker reference; ImportWorker is a QThread and would be
        # destroyed mid-run if it fell out of scope before joining.
        self._active_import_worker: ImportWorker | None = None
        self._retained_import_workers: list[ImportWorker] = []
        self._mutation_token: MutationToken | None = None

    def iter_close_workers(self) -> tuple:
        """Live worker handles MainWindow must join on close.

        Returns active and retained import workers so ``SettingsTab.iter_close_workers``
        can chain it into the single
        ``BackgroundTaskController._join_worker_for_close`` policy (cancel +
        bounded grace join + laggard deferral).  A ``None`` entry (idle flow) is
        filtered by ``_join_worker_for_close``.
        """
        return self._iter_import_workers()

    def _import_notes(self, meta: dict) -> str:
        """Trailing note about malformed-skipped entries and media warnings.

        Empty when the import was clean; otherwise a blank-line-separated block
        appended to the success dialog so a drastically-reduced or media-lossy
        import is visible to the user (plan 4.7/4.8) rather than silent. Full
        per-file media warnings are also logged.
        """
        notes: list[str] = []
        skipped = meta.get("skipped_malformed", 0)
        if skipped:
            notes.append(
                tr_format(
                    QCoreApplication.translate("DictionaryImportFlow", "Skipped %1 malformed entries."),
                    f"{skipped:,}",
                )
            )
        media_warnings = meta.get("media_warnings") or []
        if media_warnings:
            notes.append(
                tr_format(
                    QCoreApplication.translate("DictionaryImportFlow", "%1 media file(s) could not be imported."),
                    f"{len(media_warnings):,}",
                )
            )
            for warning in media_warnings:
                logger.warning("Dictionary media skipped: %s", warning)
        return ("\n\n" + "\n".join(notes)) if notes else ""

    def _set_import_buttons_enabled(self, enabled: bool) -> None:
        """Acquire/release the panel token that gates every mutation control."""
        if enabled:
            token = self._mutation_token
            self._mutation_token = None
            if token is not None:
                self._panel.release(token)
        elif self._mutation_token is None:
            self._mutation_token = self._panel.hold_mutation("import")

    def _begin_mutation(self, kind: str) -> bool:
        if self._mutation_token is not None or not self._panel.prepare_for_mutation():
            return False
        self._mutation_token = self._panel.hold_mutation(kind)
        return True

    def _with_dict_at_top(self, dict_id: str) -> tuple[ChainEntry, ...]:
        """Return the current chain with ``dict_id`` placed (or moved) to the top."""
        return self._with_dicts_at_top([dict_id])

    def _with_dicts_at_top(self, dict_ids: list[str]) -> tuple[ChainEntry, ...]:
        """Place a newly imported batch at the top, preserving picker order."""
        unique_ids = list(dict.fromkeys(dict_ids))
        selected = set(unique_ids)
        chain = [
            entry for entry in self._panel.get_chain() if not (entry.kind == "indexed" and entry.dict_id in selected)
        ]
        return tuple([ChainEntry(kind="indexed", dict_id=dict_id, enabled=True) for dict_id in unique_ids] + chain)

    def add_dict(self) -> None:
        """Prompt for one or more Yomitan zips and import them sequentially."""
        if not self._begin_mutation("add"):
            return
        trace_id = _begin_import_trace("dictionary add")
        picker_started = _log_import_picker_enter(trace_id, "dictionary zip")
        file_dialogs.pick_open_files(
            self._parent,
            QCoreApplication.translate("DictionaryImportFlow", "Choose Yomitan dictionary zips"),
            resolve_start_dir(None, file_mode=True, default_dir=self._get_config().dicts_root),
            QCoreApplication.translate("DictionaryImportFlow", "Yomitan zip (*.zip)"),
            on_done=lambda chosen: self._add_dict_picked(trace_id, picker_started, chosen),
        )

    def _add_dict_picked(self, trace_id: str, picker_started: float, zip_path_strs: list[str]) -> None:
        """Run a chained import for the zips ``add_dict``'s picker returned."""
        _log_import_picker_return(trace_id, "dictionary zip", picker_started, "; ".join(zip_path_strs))
        if not zip_path_strs:
            self._set_import_buttons_enabled(True)
            return

        jobs = [Path(path) for path in zip_path_strs]

        def make_worker(zip_path: Path) -> ImportWorker:
            config = self._get_config()
            # A freshly added dictionary is stamped — and folded — for the
            # language it is being added for. Reimport All (below) replays the
            # slot's own stamp instead, so an existing index is never relabelled.
            return ImportWorker.for_yomitan(
                zip_path,
                config.dicts_root,
                **language_kwarg(config_language(config)),
            )

        def format_label(index: int, total: int, zip_path: Path, message: str | None) -> str:
            label = tr_format(
                QCoreApplication.translate("DictionaryImportFlow", "Dictionary %1 of %2: %3"),
                index,
                total,
                zip_path.name,
            )
            return f"{label}\n{message}" if message is not None else label

        def on_finished(result: _ChainedImportResult[Path]) -> None:
            imported = [dict_id for _job, dict_id, _meta in result.successes]
            if imported:
                new_chain = self._with_dicts_at_top(imported)
                self._panel.refresh_registry()
                self._panel.set_chain(new_chain)
                _log_import_persist(trace_id, "start")
                self._persist_chain(new_chain)
                _log_import_persist(trace_id, "done")

            if len(jobs) == 1 and result.cancelled and not result.successes and not result.failures:
                return

            # A failed single pick keeps the pre-batch contract: a banner, not a
            # success box with a "Failed:" section buried in it.
            if len(jobs) == 1 and result.failures and not result.successes:
                self._report_import_issue(
                    QCoreApplication.translate("DictionaryImportFlow", "The dictionary could not be imported."),
                    result.failures[0][1],
                )
                return

            if len(result.successes) == 1 and not result.failures and not result.cancelled:
                _job, dict_id, meta = result.successes[0]
                QMessageBox.information(
                    self._parent,
                    QCoreApplication.translate("DictionaryImportFlow", "Dictionary added"),
                    tr_format(
                        QCoreApplication.translate("DictionaryImportFlow", "Imported %1 (%2 entries)"),
                        dict_id,
                        f"{meta.get('entry_count', 0):,}",
                    )
                    + self._import_notes(meta),
                )
                return

            summary = format_batch_summary(
                [
                    (
                        tr_format(
                            QCoreApplication.translate("DictionaryImportFlow", "Imported %1 dictionaries:"),
                            len(imported),
                        ),
                        [f"  • {dict_id}" for dict_id in imported],
                    ),
                    (
                        QCoreApplication.translate("DictionaryImportFlow", "Failed:"),
                        [f"  • {job.name}: {message}" for job, message in result.failures],
                    ),
                ],
                cancelled_note=(
                    QCoreApplication.translate("DictionaryImportFlow", "Cancelled before remaining dictionaries.")
                    if result.cancelled
                    else None
                ),
                empty=QCoreApplication.translate("DictionaryImportFlow", "Done."),
            )
            QMessageBox.information(
                self._parent,
                QCoreApplication.translate("DictionaryImportFlow", "Dictionaries added"),
                summary,
            )

        def on_finished_error(exc: Exception, _result: _ChainedImportResult[Path]) -> None:
            self._report_import_issue(
                QCoreApplication.translate(
                    "DictionaryImportFlow",
                    "The import finished, but the settings could not be updated.",
                ),
                str(exc),
            )

        self._run_chained_imports(
            jobs=jobs,
            make_worker=make_worker,
            format_label=format_label,
            cancel_label=QCoreApplication.translate("DictionaryImportFlow", "Cancel"),
            determinate=True,
            join_noun="dictionary import worker",
            failure_summary=QCoreApplication.translate("DictionaryImportFlow", "The dictionary could not be imported."),
            cancelling_label=QCoreApplication.translate("DictionaryImportFlow", "Cancelling…"),
            missing_result_message=QCoreApplication.translate(
                "DictionaryImportFlow", "The import worker finished without a completion result."
            ),
            trace_id=trace_id,
            on_finished=on_finished,
            on_finished_error=on_finished_error,
        )

    def _catalog_slot_base_matches(self, slot_id: str, zip_path: Path) -> bool:
        """True when ``zip_path`` is a newer, same-dictionary copy of catalog slot.

        Compares the picked zip's title base against the existing slot's stored
        ``source_name`` base (both stripped of a trailing ``[YYYY-MM-DD]`` tag,
        both required to have carried one). This lets a fresh Jitendex whose id
        derives to a new dated id (JMdict → ``jmdict-<newdate>-...``, legacy
        Jitendex → ``jitendex-org-<newdate>``) re-import into the pinned
        ``jmdict-english``/``jitendex`` slot while still rejecting an
        unrelated dictionary. Any read
        failure (bad zip, missing/corrupt slot index) → False (reject, safe).
        """
        try:
            zip_title = read_yomitan_title(zip_path)
        except Exception as exc:  # noqa: BLE001 — bucket A: saved source silently becomes ineligible.
            logger.warning(
                "Dictionary source unavailable: source=%s error=%s",
                slot_id,
                type(exc).__name__,
            )
            return False
        db = self._get_config().dicts_root / slot_id / "index.sqlite"
        if not db.exists():
            return False
        try:
            existing_name = read_meta(db).get("source_name", "")
        except Exception as exc:  # noqa: BLE001 — bucket A: corrupt metadata silently drops the source.
            logger.warning(
                "Dictionary metadata unavailable: source=%s error=%s",
                slot_id,
                type(exc).__name__,
            )
            return False
        zip_base, zip_had = strip_date_bracket(zip_title)
        cur_base, cur_had = strip_date_bracket(existing_name)
        return zip_had and cur_had and zip_base == cur_base

    def _saved_yomitan_source_matches(self, slot_id: str, zip_path: Path) -> bool:
        """Return whether a saved Yomitan zip is safe to pin to ``slot_id``."""
        try:
            derived_id = derive_dict_id_from_zip(zip_path)
        except Exception as exc:  # noqa: BLE001 — bucket A: invalid saved source silently disappears.
            logger.warning(
                "Dictionary source unavailable: source=%s error=%s",
                slot_id,
                type(exc).__name__,
            )
            return False
        return derived_id == slot_id or (
            slot_id in _PINNED_DICT_SLOT_IDS and self._catalog_slot_base_matches(slot_id, zip_path)
        )

    def reimport_dict(
        self,
        slot_id: str,
        *,
        _scan_result: tuple[Path, str, bool] | None = None,
        _trace_id: str | None = None,
    ) -> None:
        """Re-import one slot from its saved source, preferring Yomitan."""
        trace_id = _trace_id or _begin_import_trace("dictionary reimport")
        if _scan_result is None:
            if not self._begin_mutation("reimport"):
                return
            config = self._get_config()

            def _scan() -> tuple[Path, str, bool]:
                try:
                    slot = resolve_managed_slot(config.dicts_root, slot_id)
                except ValueError:
                    return config.dicts_root, "", False
                source_zip = slot / "source.zip"
                if source_zip.is_file() and self._saved_yomitan_source_matches(slot_id, source_zip):
                    return source_zip, "yomitan", True
                if slot_id == "jmdict-english" and config.jmdict_path.is_file():
                    return config.jmdict_path, "jmdict", True
                return source_zip, "", False

            def _on_done(result: object) -> None:
                assert isinstance(result, tuple)
                self.reimport_dict(slot_id, _scan_result=result, _trace_id=trace_id)

            def _on_error(message: str) -> None:
                self._set_import_buttons_enabled(True)
                self._report_import_issue(
                    QCoreApplication.translate("DictionaryImportFlow", "That folder could not be scanned."),
                    message,
                )

            self._run_latest_scan(_scan, _on_done, _on_error)
            return

        source_path, source_kind, recoverable = _scan_result
        if not recoverable:
            self._report_import_issue(
                tr_format(
                    QCoreApplication.translate(
                        "DictionaryImportFlow",
                        "No recoverable source was found for '%1'. Restore its saved source.zip or configured JMdict XML and try again.",
                    ),
                    slot_id,
                ),
            )
            self._set_import_buttons_enabled(True)
            return

        if not self._panel.request_resource_release():
            self._report_import_issue(
                QCoreApplication.translate(
                    "DictionaryImportFlow",
                    "Indexed resources are in use by mining, startup prewarm, or card backfill. "
                    "Wait for the active task to finish and try again.",
                ),
            )
            self._set_import_buttons_enabled(True)
            return

        try:
            if source_kind == "yomitan":
                worker = ImportWorker.for_yomitan_repair(
                    source_path,
                    self._get_config().dicts_root,
                    dict_id=slot_id,
                )
            else:
                worker = ImportWorker.for_jmdict_repair(source_path, self._get_config().dicts_root)
        except Exception:  # noqa: BLE001 — bucket C: release UI, then re-raise the same failure.
            self._set_import_buttons_enabled(True)
            raise

        def on_success(dict_id: str, meta: dict) -> None:
            # Refresh registry so the stale-flag warning clears on the row.
            current_chain = self._panel.get_chain()
            self._panel.refresh_registry()
            self._panel.set_chain(current_chain)
            # Notify listeners so cached DefinitionService instances rebuild
            # with the freshly-rebuilt SQLite index.
            _log_import_persist(trace_id, "start")
            self._notify_config_changed()
            _log_import_persist(trace_id, "done")
            QMessageBox.information(
                self._parent,
                QCoreApplication.translate("DictionaryImportFlow", "Dictionary re-imported"),
                tr_format(
                    QCoreApplication.translate("DictionaryImportFlow", "Re-imported %1 (%2 entries)"),
                    dict_id,
                    f"{meta.get('entry_count', 0):,}",
                )
                + self._import_notes(meta),
            )

        self._run_modal_import(
            worker=worker,
            progress_label=QCoreApplication.translate("DictionaryImportFlow", "Re-importing dictionary…"),
            cancel_label=QCoreApplication.translate("DictionaryImportFlow", "Cancel"),
            determinate=True,
            join_noun="dictionary import worker",
            failure_summary=QCoreApplication.translate(
                "DictionaryImportFlow", "The dictionary could not be re-imported."
            ),
            refusal_message=QCoreApplication.translate(
                "DictionaryImportFlow", "Another import is still finishing. Wait for it to finish and try again."
            ),
            cancelling_label=QCoreApplication.translate("DictionaryImportFlow", "Cancelling…"),
            missing_result_message=QCoreApplication.translate(
                "DictionaryImportFlow", "The import worker finished without a completion result."
            ),
            trace_id=trace_id,
            on_success=on_success,
        )

    def reimport_jmdict(self) -> None:
        """Reimport JMdict from the configured XML path."""
        if not self._begin_mutation("reimport"):
            return
        trace_id = _begin_import_trace("JMdict reimport")
        xml = self._get_config().jmdict_path
        if not xml.exists():
            self._report_import_issue(
                tr_format(
                    QCoreApplication.translate(
                        "DictionaryImportFlow", "No JMdict XML at %1. Download from EDRDG and place it there."
                    ),
                    xml,
                ),
            )
            self._set_import_buttons_enabled(True)
            return

        # Drop sqlite handles before the importer renames the dict folder
        # (Issue #32 — same root cause as #30). Without this, the rename
        # at yomitan_importer.py:215 fails with "Access denied" on Windows.
        if not self._panel.request_resource_release():
            self._report_import_issue(
                QCoreApplication.translate(
                    "DictionaryImportFlow",
                    "Indexed resources are in use by mining, startup prewarm, or card backfill. "
                    "Wait for the active task to finish and try again.",
                ),
            )
            self._set_import_buttons_enabled(True)
            return

        try:
            worker = ImportWorker.for_jmdict(xml, self._get_config().dicts_root)
        except Exception:  # noqa: BLE001 — bucket C: release UI, then re-raise the same failure.
            self._set_import_buttons_enabled(True)
            raise

        def on_success(_dict_id: str, _meta: dict) -> None:
            # Re-render chain so the (refreshed) entry count is reflected.
            current_chain = self._panel.get_chain()
            self._panel.refresh_registry()
            self._panel.set_chain(current_chain)
            # Notify listeners so cached DefinitionService instances rebuild
            # with the freshly-rebuilt SQLite index.
            _log_import_persist(trace_id, "start")
            self._notify_config_changed()
            _log_import_persist(trace_id, "done")

        self._run_modal_import(
            worker=worker,
            progress_label=QCoreApplication.translate("DictionaryImportFlow", "Reimporting JMdict…"),
            cancel_label=QCoreApplication.translate("DictionaryImportFlow", "Cancel"),
            determinate=True,
            join_noun="dictionary import worker",
            failure_summary=QCoreApplication.translate(
                "DictionaryImportFlow", "The dictionaries could not be re-imported."
            ),
            refusal_message=QCoreApplication.translate(
                "DictionaryImportFlow", "Another import is still finishing. Wait for it to finish and try again."
            ),
            cancelling_label=QCoreApplication.translate("DictionaryImportFlow", "Cancelling…"),
            missing_result_message=QCoreApplication.translate(
                "DictionaryImportFlow", "The import worker finished without a completion result."
            ),
            trace_id=trace_id,
            on_success=on_success,
        )

    def reimport_all(
        self,
        *,
        only_ids: frozenset[str] | None = None,
        on_complete: Callable[[], None] | None = None,
        _scan_result: tuple[list[tuple[str, str, str, Path]], list[str]] | None = None,
        _trace_id: str | None = None,
    ) -> None:
        """Reimport dictionaries in the chain from their saved sources.

        For each indexed ChainEntry, prefer an owned slot's ``source.zip``.
        The fixed ``jmdict-english`` slot falls back to ``config.jmdict_path``
        when its registry metadata proves it is a raw JMdict import. Slots
        without ownership proof are reported but never auto-repaired.

        ``only_ids`` scopes upgrade repair to dictionary IDs found stale by the
        startup scan. ``None`` preserves the manual Reimport All behavior,
        including disabled chain entries. Missing-source reporting follows the
        same scope.

        Dicts ineligible for automatic repair are skipped and surfaced in the
        final summary dialog.

        Runs sequentially: each worker's native finish chains the next dispatch
        so a single ApplicationModal QProgressDialog tracks the whole batch.
        Per-dict failures accumulate into ``errors`` and don't abort the
        loop. ``config_changed`` is emitted once at the end so cached
        DefinitionService instances rebuild a single time.

        ``on_complete`` fires exactly once on every terminal path, including the
        refusals and the nothing-to-do case, so the startup prompt can run the
        frequency and pitch batches after this one instead of racing them.
        """
        done = _OnceCallback(on_complete)
        trace_id = _trace_id or _begin_import_trace("dictionary reimport all")
        if _scan_result is None:
            if not self._begin_mutation("reimport-all"):
                done()
                return
            config = self._get_config()
            chain = self._panel.get_chain()

            def _scan() -> tuple[list[tuple[str, str, str, Path]], list[str]]:
                registry = DictionaryRegistry(config.dicts_root)
                registry.load()
                jobs: list[tuple[str, str, str, Path]] = []
                missing_legacy: list[str] = []
                for entry in chain:
                    if entry.kind != "indexed" or entry.dict_id is None:
                        continue
                    if only_ids is not None and entry.dict_id not in only_ids:
                        continue
                    try:
                        slot = resolve_managed_slot(config.dicts_root, entry.dict_id)
                    except ValueError:
                        missing_legacy.append(entry.dict_id)
                        continue
                    meta = registry.get(entry.dict_id)
                    display_name = meta.source_name if meta is not None else entry.dict_id
                    owned = prove_owned_slot(config.dicts_root, entry.dict_id, "dictionary")
                    source_zip = slot / "source.zip"
                    if owned and source_zip.is_file() and self._saved_yomitan_source_matches(entry.dict_id, source_zip):
                        jobs.append(("yomitan", entry.dict_id, display_name, source_zip))
                        continue
                    if (
                        meta is not None
                        and entry.dict_id == "jmdict-english"
                        and meta.format == "jmdict"
                        and config.jmdict_path.is_file()
                        and owned
                    ):
                        jobs.append(("jmdict", entry.dict_id, display_name, config.jmdict_path))
                        continue
                    missing_legacy.append(display_name)
                return jobs, missing_legacy

            def _on_done(result: object) -> None:
                assert isinstance(result, tuple)
                self.reimport_all(
                    only_ids=only_ids,
                    on_complete=on_complete,
                    _scan_result=result,
                    _trace_id=trace_id,
                )

            def _on_error(message: str) -> None:
                self._set_import_buttons_enabled(True)
                self._report_import_issue(
                    QCoreApplication.translate("DictionaryImportFlow", "That folder could not be scanned."),
                    message,
                )
                done()

            self._run_latest_scan(_scan, _on_done, _on_error)
            return

        jobs, missing_legacy = _scan_result

        if not jobs:
            if missing_legacy:
                body = QCoreApplication.translate(
                    "DictionaryImportFlow",
                    "No dictionaries eligible for automatic repair were found.\n\n"
                    "Skipped (not eligible for automatic repair; use per-row Re-import…):\n",
                ) + "\n".join(f"  • {n}" for n in missing_legacy)
            else:
                body = QCoreApplication.translate("DictionaryImportFlow", "No dictionaries in the chain.")
            QMessageBox.information(
                self._parent, QCoreApplication.translate("DictionaryImportFlow", "Nothing to reimport"), body
            )
            self._set_import_buttons_enabled(True)
            done()
            return

        # Drop sqlite handles before any worker touches the dict folders.
        # On Windows the importer's directory rename fails with "Access
        # denied" while a DefinitionService still holds its read-only
        # connection open (Issue #32; same hook as the remove flow in #30).
        if not self._panel.request_resource_release():
            self._report_import_issue(
                QCoreApplication.translate(
                    "DictionaryImportFlow",
                    "Indexed resources are in use by mining, startup prewarm, or card backfill. "
                    "Wait for the active task to finish and try again.",
                ),
            )
            self._set_import_buttons_enabled(True)
            done()
            return

        def make_worker(job: tuple[str, str, str, Path]) -> ImportWorker:
            kind, dict_id, _display, source_path = job
            dicts_root = self._get_config().dicts_root
            if kind == "jmdict":
                return ImportWorker.for_jmdict(source_path, dicts_root)
            # Pin the existing slot id so a saved source whose title embeds a
            # changing release date (e.g. Jitendex) rebuilds the index in the
            # SAME folder instead of forking a new date-named dir — which would
            # orphan the chained slot and permanently wedge the stale-schema
            # pre-run gate (it could never clear the old slot).
            return ImportWorker.for_yomitan(
                source_path,
                dicts_root,
                overwrite=True,
                dict_id=dict_id,
                **slot_language_kwarg(dicts_root / dict_id),
            )

        def format_label(
            index: int,
            total: int,
            job: tuple[str, str, str, Path],
            message: str | None,
        ) -> str:
            _kind, _dict_id, display, _source_path = job
            label = tr_format(
                QCoreApplication.translate("DictionaryImportFlow", "Dictionary %1 of %2: %3"),
                index,
                total,
                display,
            )
            return f"{label}\n{message}" if message is not None else label

        def on_finished(result: _ChainedImportResult[tuple[str, str, str, Path]]) -> None:
            # One refresh + one config_changed for the whole batch so
            # DefinitionService rebuilds once, not N times.
            _log_import_persist(trace_id, "start")
            current_chain = self._panel.get_chain()
            self._panel.refresh_registry()
            self._panel.set_chain(current_chain)
            self._notify_config_changed()
            _log_import_persist(trace_id, "done")

            reimported = [job[2] for job, _dict_id, _meta in result.successes]
            errors = [(job[2], message) for job, message in result.failures]

            summary = format_batch_summary(
                [
                    (
                        tr_format(
                            QCoreApplication.translate(
                                "DictionaryImportFlow", "Reimported %1 dictionary/dictionaries:"
                            ),
                            len(reimported),
                        ),
                        [f"  • {n}" for n in reimported],
                    ),
                    (
                        QCoreApplication.translate(
                            "DictionaryImportFlow",
                            "Skipped (not eligible for automatic repair; use per-row Re-import…):",
                        ),
                        [f"  • {n}" for n in missing_legacy],
                    ),
                    (
                        QCoreApplication.translate("DictionaryImportFlow", "Failed:"),
                        [f"  • {name}: {msg}" for name, msg in errors],
                    ),
                ],
                cancelled_note=(
                    QCoreApplication.translate("DictionaryImportFlow", "Cancelled before remaining dictionaries.")
                    if result.cancelled
                    else None
                ),
                empty=QCoreApplication.translate("DictionaryImportFlow", "Done."),
            )

            QMessageBox.information(
                self._parent,
                QCoreApplication.translate("DictionaryImportFlow", "Reimport All"),
                summary,
            )
            done()

        def on_finished_error(
            exc: Exception,
            _result: _ChainedImportResult[tuple[str, str, str, Path]],
        ) -> None:
            self._report_import_issue(
                QCoreApplication.translate(
                    "DictionaryImportFlow",
                    "The import finished, but the settings could not be updated.",
                ),
                str(exc),
            )
            done()

        self._run_chained_imports(
            jobs=jobs,
            make_worker=make_worker,
            format_label=format_label,
            cancel_label=QCoreApplication.translate("DictionaryImportFlow", "Cancel"),
            cancelling_label=QCoreApplication.translate("DictionaryImportFlow", "Cancelling…"),
            determinate=True,
            join_noun="dictionary import worker",
            failure_summary=QCoreApplication.translate(
                "DictionaryImportFlow", "The dictionaries could not be re-imported."
            ),
            missing_result_message=QCoreApplication.translate(
                "DictionaryImportFlow", "The import worker finished without a completion result."
            ),
            trace_id=trace_id,
            on_finished=on_finished,
            on_finished_error=on_finished_error,
        )

    def restore_unlisted(self, *, _scan_result: list[DictMeta] | None = None) -> None:
        """Re-add on-disk dictionaries that are absent from the chain config.

        Recovers dicts present in ``dicts_root`` (with a valid, current-schema
        index.sqlite) but missing from ``config.dictionary_chain`` — for example
        after a config reset that overwrote ``gui_config.json``.  No re-import
        is performed; the indexes already exist on disk and only the chain config
        needs updating.
        """
        if _scan_result is None:
            if not self._begin_mutation("restore"):
                return
            config = self._get_config()
            panel_config = replace(config, dictionary_chain=self._panel.get_chain())

            def _scan() -> list[DictMeta]:
                registry = DictionaryRegistry(config.dicts_root)
                registry.load()
                return registry.unlisted(panel_config)

            def _on_done(result: object) -> None:
                assert isinstance(result, list)
                self.restore_unlisted(_scan_result=result)

            def _on_error(message: str) -> None:
                self._set_import_buttons_enabled(True)
                self._report_import_issue(
                    QCoreApplication.translate("DictionaryImportFlow", "That folder could not be scanned."),
                    message,
                )

            self._run_latest_scan(_scan, _on_done, _on_error)
            return

        orphans = _scan_result
        try:
            if not orphans:
                QMessageBox.information(
                    self._parent,
                    QCoreApplication.translate("DictionaryImportFlow", "Nothing to restore"),
                    QCoreApplication.translate("DictionaryImportFlow", "All on-disk dictionaries are already listed."),
                )
                return

            body = (
                QCoreApplication.translate(
                    "DictionaryImportFlow", "Found dictionaries on disk that aren't in your list:\n\n"
                )
                + "\n".join(f"  • {m.source_name}" for m in orphans)
                + "\n\n"
                + QCoreApplication.translate("DictionaryImportFlow", "Add them to the dictionary list?")
            )
            reply = QMessageBox.question(
                self._parent,
                QCoreApplication.translate("DictionaryImportFlow", "Restore from Disk"),
                body,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

            chain = list(self._panel.get_chain())
            new_entries = [ChainEntry(kind="indexed", dict_id=m.dict_id, enabled=True) for m in orphans]
            # Insert before the first jisho entry so the online fallback stays last.
            # The UI only ever creates one jisho row; "first jisho wins" is fine.
            insert_at = next((i for i, e in enumerate(chain) if e.kind == "jisho"), len(chain))
            new_chain = tuple(chain[:insert_at] + new_entries + chain[insert_at:])

            self._panel.refresh_registry()
            self._panel.set_chain(new_chain)
            self._persist_chain(new_chain)
        finally:
            self._set_import_buttons_enabled(True)
