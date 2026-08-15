"""Shared add / reimport orchestration for the single-file source chains.

Frequency and pitch are the same flow with different nouns: both index a
user-picked file into ``<root>/<source_id>/index.sqlite``, both persist the
original input beside it as ``source.<ext>`` so a later reimport can rebuild
without asking for the file again, and both expose a purely-ordered chain of
those sources. This module owns that flow once; :class:`FrequencyImportFlow`
and :class:`PitchImportFlow` supply the differences.

Dictionaries are deliberately NOT folded in. Their reimport has to choose
between a saved Yomitan zip and a raw JMdict XML, and pin slot ids whose title
embeds a release date — real complexity this base would have to grow a second
mode for, to serve exactly one caller.

**Every user-facing string arrives pre-translated in ``SourceFlowLabels``; this
module calls ``tr()`` exactly zero times.** Two reasons, both load-bearing:
``lupdate`` reads literals statically and cannot follow a variable context, and
a literal that moved contexts would orphan its existing entry in all twelve
catalogs. Same rule ``ChainRowSpec`` and ``ChainListLabels`` already follow.
"""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from PyQt6.QtWidgets import QMessageBox, QWidget

from anki_miner.config import AnkiMinerConfig
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
from anki_miner.gui.widgets.panels.chain_settings_panel_base import MutationToken
from anki_miner.gui.workers.import_worker import ImportWorker
from anki_miner.services._sqlite_index import resolve_managed_slot
from anki_miner.utils.i18n import tr_format

#: One reimport-all job: (source_id, display name, source file to rebuild from).
ReimportJob = tuple[str, str, Path]


class _SourceEntry(Protocol):
    """Structural view of ``FreqEntry`` / ``PitchSourceEntry``.

    Read-only properties, not bare attributes: both concrete entries are frozen
    dataclasses, which satisfy a read-only protocol but not a writable one.
    """

    @property
    def source_id(self) -> str: ...

    @property
    def enabled(self) -> bool: ...


@dataclass(frozen=True)
class SourceFlowLabels:
    """Pre-translated strings for one family's flows.

    Built fresh on every access (see :attr:`SourceChainImportFlow._labels`) so a
    mid-session language change is picked up. Fields whose name ends in
    ``_template`` carry ``%1``-style placeholders for :func:`tr_format`.
    """

    # Pickers.
    picker_add_caption: str
    picker_reimport_caption: str
    picker_filter_template: str
    # Shared failure paths.
    scan_failed: str
    resources_in_use: str
    settings_update_failed: str
    refusal: str
    missing_result: str
    cancel: str
    cancelling: str
    # Single add.
    add_progress: str
    add_failure_summary: str
    added_title: str
    added_body_template: str
    # Multi add. The batch progress and cancelled lines are shared with
    # reimport-all: they name the family and the position, which reads the same
    # either way.
    picker_add_multi_caption: str
    added_batch_title: str
    added_batch_header_template: str
    # Single reimport.
    reimport_progress: str
    reimport_failure_summary: str
    reimported_title: str
    reimported_body_template: str
    # Reimport all.
    batch_progress_template: str
    batch_failure_summary: str
    batch_title: str
    batch_reimported_header_template: str
    batch_skipped_header: str
    batch_failed_header: str
    batch_cancelled: str
    batch_done: str
    nothing_title: str
    nothing_empty_chain: str
    nothing_skipped_header: str


class SourceChainImportFlow(ModalImportFlowMixin):
    """Drives add / reimport-one / reimport-all for one single-file source chain.

    Plain (non-Qt) class: owns the :class:`ImportWorker` lifecycle and every
    dialog. The settings tab keeps the panel widgets and the signal wiring, and
    injects the narrow chain persist as a callable so the dependency stays
    one-way (tab -> controller -> workers/services).

    A freshly imported source is *appended* (enabled) to the chain rather than
    inserted at a fixed priority. For frequency that is immaterial (the chain is
    additive); for pitch it means a new source starts as lowest-priority filler
    under first-hit-wins, and the user reorders it upward if it should win
    overlaps.

    Args:
        parent: Widget used as the Qt parent for dialogs (the settings tab).
        panel: The family's settings panel (chain state, registry refresh).
        get_config: Returns the tab's *current* config.
        persist_chain: The tab's narrow chain persist - saves a chain mutation
            to disk and notifies listeners without running the full Save
            pipeline.
        notify_config_changed: Rebuilds cached services after a reimport, which
            changes an index in place rather than the chain.
    """

    def __init__(
        self,
        parent: QWidget,
        panel: Any,
        get_config: Callable[[], AnkiMinerConfig],
        persist_chain: Callable[[Any], None],
        notify_config_changed: Callable[[], None],
    ) -> None:
        self._parent = parent
        self._panel = panel
        self._get_config = get_config
        self._persist_chain = persist_chain
        self._notify_config_changed = notify_config_changed
        # Long-lived worker reference: ImportWorker is a QThread and would be
        # destroyed mid-run if it fell out of scope before joining.
        self._active_import_worker: ImportWorker | None = None
        self._retained_import_workers: list[ImportWorker] = []
        self._mutation_token: MutationToken | None = None

    # ------------------------------------------------------------------
    # Family hooks
    # ------------------------------------------------------------------

    @property
    @abstractmethod
    def _labels(self) -> SourceFlowLabels:
        """Freshly-translated strings for this family."""

    @property
    @abstractmethod
    def _suffixes(self) -> tuple[str, ...]:
        """Accepted input suffixes, also the ``source.<ext>`` probe order."""

    @property
    @abstractmethod
    def _trace_noun(self) -> str:
        """Log-only family noun (``"frequency"`` / ``"pitch"``)."""

    @abstractmethod
    def _dest_root(self, config: AnkiMinerConfig) -> Path:
        """Root folder holding this family's slots."""

    @abstractmethod
    def _make_entry(self, source_id: str) -> _SourceEntry:
        """Build an enabled chain entry of this family's type."""

    @abstractmethod
    def _read_source_name(self, db_path: Path) -> str | None:
        """Read the stored display name, or ``None`` if unreadable."""

    @abstractmethod
    def _make_add_worker(self, source_file: Path, dest_root: Path) -> ImportWorker:
        """Worker importing ``source_file`` as a brand-new slot."""

    @abstractmethod
    def _make_repair_worker(
        self,
        source_file: Path,
        dest_root: Path,
        *,
        source_id: str,
        source_name: str,
    ) -> ImportWorker:
        """Worker rebuilding an existing slot in place."""

    def _extra_add_notes(self, meta: dict) -> str:
        """Family-specific tail appended to the *add* success message."""
        del meta
        return ""

    def _extra_reimport_notes(self, meta: dict) -> str:
        """Family-specific tail appended to the *reimport* success message.

        Split from :meth:`_extra_add_notes` because the two messages disclose
        different things: an add reports what the importer skipped or converted
        about a file the user is seeing for the first time, while a reimport of
        an already-known source only needs the properties that still affect how
        its rows are used.
        """
        del meta
        return ""

    # ------------------------------------------------------------------
    # Mutation gating
    # ------------------------------------------------------------------

    def iter_close_workers(self) -> tuple:
        """Live worker handles MainWindow must join on close.

        A ``None`` entry (idle flow) is filtered by
        ``BackgroundTaskController._join_worker_for_close``.
        """
        return self._iter_import_workers()

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

    # ------------------------------------------------------------------
    # Add
    # ------------------------------------------------------------------

    def _chain_with_new_source_appended(self, source_id: str) -> tuple[Any, ...]:
        """Return the current chain with ``source_id`` appended (enabled).

        Any pre-existing entry with the same source_id is removed first so a
        re-added source moves to the end rather than duplicating.
        """
        current = [e for e in self._panel.get_chain() if e.source_id != source_id]
        current.append(self._make_entry(source_id))
        return tuple(current)

    def _chain_with_new_sources_appended(self, source_ids: list[str]) -> tuple[Any, ...]:
        """Append a whole imported batch, preserving picker order.

        Same re-add rule as the single case: an id already in the chain moves to
        the end rather than duplicating.
        """
        unique_ids = list(dict.fromkeys(source_ids))
        selected = set(unique_ids)
        current = [e for e in self._panel.get_chain() if e.source_id not in selected]
        current.extend(self._make_entry(source_id) for source_id in unique_ids)
        return tuple(current)

    def add_source(self) -> None:
        """Prompt for one or more source files and import them in picker order."""
        if not self._begin_mutation("add"):
            return
        labels = self._labels
        trace_id = _begin_import_trace(f"{self._trace_noun} add")
        picker_started = _log_import_picker_enter(trace_id, f"{self._trace_noun} source")
        file_dialogs.pick_open_files(
            self._parent,
            labels.picker_add_multi_caption,
            resolve_start_dir(None, file_mode=True),
            self._source_picker_filter(),
            on_done=lambda chosen: self._add_source_picked(trace_id, picker_started, chosen),
        )

    def _add_source_picked(self, trace_id: str, picker_started: float, chosen: list[str]) -> None:
        """Import every file ``add_source``'s picker returned, in picker order.

        One modal, one chain write, one persist for the whole batch: a partial
        failure keeps the sources that did import instead of losing the run.
        """
        _log_import_picker_return(trace_id, f"{self._trace_noun} source", picker_started, "; ".join(chosen))
        if not chosen:
            self._set_import_buttons_enabled(True)
            return

        labels = self._labels
        jobs = [Path(path) for path in chosen]

        def make_worker(source_file: Path) -> ImportWorker:
            return self._make_add_worker(source_file, self._dest_root(self._get_config()))

        def format_label(index: int, total: int, source_file: Path, message: str | None) -> str:
            label = tr_format(labels.batch_progress_template, index, total, source_file.name)
            return f"{label}\n{message}" if message is not None else label

        def on_finished(result: _ChainedImportResult[Path]) -> None:
            imported = [source_id for _job, source_id, _meta in result.successes]
            if imported:
                new_chain = self._chain_with_new_sources_appended(imported)
                self._panel.refresh_registry()
                self._panel.set_chain(new_chain)
                _log_import_persist(trace_id, "start")
                self._persist_chain(new_chain)
                _log_import_persist(trace_id, "done")

            # A cancelled single pick is the user changing their mind: say nothing.
            if len(jobs) == 1 and result.cancelled and not result.successes and not result.failures:
                return
            # A failed single pick keeps the pre-batch contract: a banner, not a
            # success box with a "Failed:" section buried in it.
            if len(jobs) == 1 and result.failures and not result.successes:
                self._report_import_issue(labels.add_failure_summary, result.failures[0][1])
                return
            if len(result.successes) == 1 and not result.failures and not result.cancelled:
                _job, source_id, meta = result.successes[0]
                QMessageBox.information(
                    self._parent,
                    labels.added_title,
                    tr_format(
                        labels.added_body_template,
                        f"{meta.get('entry_count', 0):,}",
                        meta.get("source_name", source_id),
                    )
                    + self._extra_add_notes(meta),
                )
                return

            summary = format_batch_summary(
                [
                    (
                        tr_format(labels.added_batch_header_template, len(result.successes)),
                        [
                            f"  • {meta.get('source_name', source_id)} ({meta.get('entry_count', 0):,} entries)"
                            for _job, source_id, meta in result.successes
                        ],
                    ),
                    (
                        labels.batch_failed_header,
                        [f"  • {job.name}: {message}" for job, message in result.failures],
                    ),
                ],
                cancelled_note=labels.batch_cancelled if result.cancelled else None,
                empty=labels.batch_done,
            )
            QMessageBox.information(self._parent, labels.added_batch_title, summary)

        def on_finished_error(exc: Exception, _result: _ChainedImportResult[Path]) -> None:
            self._report_import_issue(labels.settings_update_failed, str(exc))

        self._run_chained_imports(
            jobs=jobs,
            make_worker=make_worker,
            format_label=format_label,
            cancel_label=labels.cancel,
            cancelling_label=labels.cancelling,
            determinate=False,
            join_noun=f"{self._trace_noun} import worker",
            failure_summary=labels.add_failure_summary,
            missing_result_message=labels.missing_result,
            trace_id=trace_id,
            on_finished=on_finished,
            on_finished_error=on_finished_error,
        )

    # ------------------------------------------------------------------
    # Reimport one
    # ------------------------------------------------------------------

    def reimport_source(
        self,
        source_id: str,
        *,
        _scan_result: tuple[Path, Path | None, str | None] | None = None,
        _trace_id: str | None = None,
    ) -> None:
        """Re-import an existing source into the same id.

        The importer copied the original input alongside the index as
        ``source.<ext>`` on first import, so a re-import can re-run without the
        user re-picking the file. If that copy is gone (older import / moved
        folder), prompt the user to re-pick.
        """
        labels = self._labels
        trace_id = _trace_id or _begin_import_trace(f"{self._trace_noun} reimport")
        if _scan_result is None:
            if not self._begin_mutation("reimport"):
                return
            dest_root = self._dest_root(self._get_config())
            source_dir = dest_root / source_id

            def _scan() -> tuple[Path, Path | None, str | None]:
                source_file = self._find_source_copy(source_dir)
                stored_name = self._read_source_name(source_dir / "index.sqlite")
                return dest_root, source_file, stored_name or source_id

            def _on_done(result: object) -> None:
                assert isinstance(result, tuple)
                self.reimport_source(source_id, _scan_result=result, _trace_id=trace_id)

            def _on_error(message: str) -> None:
                self._set_import_buttons_enabled(True)
                self._report_import_issue(labels.scan_failed, message)

            self._run_latest_scan(_scan, _on_done, _on_error)
            return

        dest_root, source_file, existing_name = _scan_result
        if source_file is None:
            # No persisted copy — ask, then rejoin the shared tail from the
            # callback. The picker no longer blocks, so the rest of this flow
            # cannot simply fall through to it.
            picker_started = _log_import_picker_enter(trace_id, f"{self._trace_noun} source")

            def _on_picked(chosen: str) -> None:
                _log_import_picker_return(trace_id, f"{self._trace_noun} source", picker_started, chosen)
                if not chosen:
                    self._set_import_buttons_enabled(True)
                    return
                self._continue_reimport(source_id, trace_id, dest_root, Path(chosen), existing_name)

            file_dialogs.pick_open_file(
                self._parent,
                labels.picker_reimport_caption,
                resolve_start_dir(None, file_mode=True),
                self._source_picker_filter(),
                on_done=_on_picked,
            )
            return

        self._continue_reimport(source_id, trace_id, dest_root, source_file, existing_name)

    def _continue_reimport(
        self,
        source_id: str,
        trace_id: str,
        dest_root: Path,
        source_file: Path,
        existing_name: str | None,
    ) -> None:
        """Repair ``source_id`` from ``source_file``.

        Shared tail of :meth:`reimport_source`: reached directly when the
        persisted ``source.<ext>`` copy exists, and from the picker callback
        when the user had to re-pick. ``existing_name`` preserves the display
        name across the rebuild; corrupt or missing metadata falls back to the
        stable source id instead of the persisted copy's generic "source" stem.
        """
        labels = self._labels
        if not self._panel.request_resource_release():
            self._report_import_issue(labels.resources_in_use)
            self._set_import_buttons_enabled(True)
            return

        try:
            worker = self._make_repair_worker(
                source_file,
                dest_root,
                source_id=source_id,
                source_name=existing_name or source_id,
            )
        except Exception:
            self._set_import_buttons_enabled(True)
            raise

        def on_success(imported_id: str, meta: dict) -> None:
            current_chain = self._panel.get_chain()
            self._panel.refresh_registry()
            self._panel.set_chain(current_chain)
            _log_import_persist(trace_id, "start")
            self._notify_config_changed()
            _log_import_persist(trace_id, "done")
            QMessageBox.information(
                self._parent,
                labels.reimported_title,
                tr_format(labels.reimported_body_template, imported_id) + self._extra_reimport_notes(meta),
            )

        self._run_modal_import(
            worker=worker,
            progress_label=labels.reimport_progress,
            cancel_label=labels.cancel,
            determinate=False,
            join_noun=f"{self._trace_noun} import worker",
            failure_summary=labels.reimport_failure_summary,
            refusal_message=labels.refusal,
            cancelling_label=labels.cancelling,
            missing_result_message=labels.missing_result,
            trace_id=trace_id,
            on_success=on_success,
        )

    # ------------------------------------------------------------------
    # Reimport all
    # ------------------------------------------------------------------

    def reimport_all(
        self,
        *,
        only_ids: frozenset[str] | None = None,
        on_complete: Callable[[], None] | None = None,
        _scan_result: tuple[list[ReimportJob], list[str]] | None = None,
        _trace_id: str | None = None,
    ) -> None:
        """Rebuild every chained source from its persisted ``source.<ext>`` copy.

        ``only_ids`` scopes the batch to the ids the startup stale scan found,
        leaving the manual button's ``None`` to mean "everything in the chain,
        including disabled entries" — matching Reimport All for dictionaries.

        A slot with no persisted copy is *reported*, never prompted for: a batch
        that stopped on a file picker per source would strand the user
        mid-upgrade. Those land in the summary pointing at the per-row
        Re-import…, which does prompt.

        Runs sequentially so one ApplicationModal progress dialog tracks the
        whole batch; per-source failures accumulate rather than aborting the
        loop, and ``config_changed`` fires once at the end so cached services
        rebuild a single time.

        ``on_complete`` fires exactly once on every terminal path, including the
        refusals and the nothing-to-do case. The startup prompt uses it to run
        one family after another: three ApplicationModal progress dialogs racing
        each other is what firing them together would produce.
        """
        done = _OnceCallback(on_complete)
        labels = self._labels
        trace_id = _trace_id or _begin_import_trace(f"{self._trace_noun} reimport all")
        if _scan_result is None:
            if not self._begin_mutation("reimport-all"):
                done()
                return
            dest_root = self._dest_root(self._get_config())
            chain = self._panel.get_chain()

            def _scan() -> tuple[list[ReimportJob], list[str]]:
                jobs: list[ReimportJob] = []
                skipped: list[str] = []
                for entry in chain:
                    source_id = entry.source_id
                    if not source_id:
                        continue
                    if only_ids is not None and source_id not in only_ids:
                        continue
                    try:
                        slot = resolve_managed_slot(dest_root, source_id)
                    except ValueError:
                        skipped.append(source_id)
                        continue
                    display = self._read_source_name(slot / "index.sqlite") or source_id
                    source_file = self._find_source_copy(slot)
                    if source_file is None:
                        skipped.append(display)
                        continue
                    jobs.append((source_id, display, source_file))
                return jobs, skipped

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
                self._report_import_issue(labels.scan_failed, message)
                done()

            self._run_latest_scan(_scan, _on_done, _on_error)
            return

        jobs, skipped = _scan_result

        if not jobs:
            if skipped:
                body = labels.nothing_skipped_header + "\n".join(f"  • {n}" for n in skipped)
            else:
                body = labels.nothing_empty_chain
            QMessageBox.information(self._parent, labels.nothing_title, body)
            self._set_import_buttons_enabled(True)
            done()
            return

        # Drop sqlite handles before any worker touches the slot folders. On
        # Windows the importer's directory rename fails with "Access denied"
        # while a service still holds its read-only connection open (Issue #32).
        if not self._panel.request_resource_release():
            self._report_import_issue(labels.resources_in_use)
            self._set_import_buttons_enabled(True)
            done()
            return

        def make_worker(job: ReimportJob) -> ImportWorker:
            source_id, display, source_file = job
            return self._make_repair_worker(
                source_file,
                self._dest_root(self._get_config()),
                source_id=source_id,
                source_name=display,
            )

        def format_label(index: int, total: int, job: ReimportJob, message: str | None) -> str:
            _source_id, display, _source_file = job
            label = tr_format(labels.batch_progress_template, index, total, display)
            return f"{label}\n{message}" if message is not None else label

        def on_finished(result: _ChainedImportResult[ReimportJob]) -> None:
            # One refresh + one config_changed for the whole batch so cached
            # services rebuild once, not N times.
            _log_import_persist(trace_id, "start")
            current_chain = self._panel.get_chain()
            self._panel.refresh_registry()
            self._panel.set_chain(current_chain)
            self._notify_config_changed()
            _log_import_persist(trace_id, "done")

            reimported = [job[1] for job, _source_id, _meta in result.successes]
            errors = [(job[1], message) for job, message in result.failures]

            summary = format_batch_summary(
                [
                    (
                        tr_format(labels.batch_reimported_header_template, len(reimported)),
                        [f"  • {n}" for n in reimported],
                    ),
                    (labels.batch_skipped_header, [f"  • {n}" for n in skipped]),
                    (labels.batch_failed_header, [f"  • {name}: {msg}" for name, msg in errors]),
                ],
                cancelled_note=labels.batch_cancelled if result.cancelled else None,
                empty=labels.batch_done,
            )
            QMessageBox.information(self._parent, labels.batch_title, summary)
            done()

        def on_finished_error(exc: Exception, _result: _ChainedImportResult[ReimportJob]) -> None:
            self._report_import_issue(labels.settings_update_failed, str(exc))
            done()

        self._run_chained_imports(
            jobs=jobs,
            make_worker=make_worker,
            format_label=format_label,
            cancel_label=labels.cancel,
            cancelling_label=labels.cancelling,
            determinate=True,
            join_noun=f"{self._trace_noun} import worker",
            failure_summary=labels.batch_failure_summary,
            missing_result_message=labels.missing_result,
            trace_id=trace_id,
            on_finished=on_finished,
            on_finished_error=on_finished_error,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _find_source_copy(self, source_dir: Path) -> Path | None:
        """Return the persisted ``source.<ext>`` original input, if present."""
        for suffix in self._suffixes:
            candidate = source_dir / ("source" + suffix)
            if candidate.is_file():
                return candidate
        return None

    def _source_picker_filter(self) -> str:
        suffix_globs = " ".join(f"*{suffix}" for suffix in self._suffixes)
        return tr_format(self._labels.picker_filter_template, suffix_globs)
