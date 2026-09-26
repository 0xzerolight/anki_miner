"""Settings → Resources ▾: one-file transfer of a mining language's resources.

Drives :mod:`anki_miner.services.resource_bundle`. Each direction takes the
chain panels' mutation lock at the click (``acquire`` -- which also commits
pending Settings edits) and releases it on every exit; the mixin's
``_set_import_buttons_enabled(True)`` is the release point after a worker
run. Scans and manifest reads go through ``run_off_thread``; the write and
the install run in the generic ``ImportWorker`` behind the shared modal dialog
(:class:`ModalImportFlowMixin`), so cancel, the no-progress watchdog, the
close-time join and the failure banner behave like every other Settings import.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from pathlib import Path
from typing import Any, Concatenate, ParamSpec, cast

from PyQt6.QtCore import QCoreApplication, QLocale
from PyQt6.QtWidgets import QDialog, QMessageBox, QWidget

from anki_miner import __version__
from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.controllers.import_flow_common import (
    ModalImportFlowMixin,
    _begin_import_trace,
    format_batch_summary,
)
from anki_miner.gui.utils import file_dialogs
from anki_miner.gui.utils.dialog_paths import resolve_start_dir
from anki_miner.gui.utils.progress_telemetry import format_data_size
from anki_miner.gui.utils.run_off_thread import run_off_thread
from anki_miner.gui.utils.service_factory import resolve_known_words_db_path
from anki_miner.gui.widgets.base.screen_issue_banner import clear_reported_issue
from anki_miner.gui.widgets.dialogs.resource_bundle_dialog import BundleChoice, ResourceBundleDialog
from anki_miner.gui.workers.import_worker import CancelFn, ImportWorker, ProgressFn
from anki_miner.languages.registry import config_language, language_display_name
from anki_miner.services.resource_bundle import (
    BundleInstallResult,
    BundleItem,
    BundleManifest,
    BundleWriteResult,
    ExportCandidate,
    ImportCandidate,
    collect_export_candidates,
    install_resource_bundle,
    plan_import,
    read_bundle_manifest,
    write_resource_bundle,
)
from anki_miner.utils.i18n import tr_format


def _read_and_plan(path: Path, config: AnkiMinerConfig) -> tuple[BundleManifest, list[ImportCandidate]]:
    manifest = read_bundle_manifest(path)
    return manifest, plan_import(manifest, config)


_P = ParamSpec("_P")


def _releases_lock_on_error(
    step: Callable[Concatenate[ResourceBundleFlow, _P], None],
) -> Callable[Concatenate[ResourceBundleFlow, _P], None]:
    """Release the lock if a step between the click and the worker raises.

    A leaked lock is not cosmetic: while any chain panel holds a token,
    ``SettingsTab._commit_settings`` refuses, so every later Settings edit
    would silently stop saving for the rest of the session.
    """

    @functools.wraps(step)
    def guarded(self: ResourceBundleFlow, *args: _P.args, **kwargs: _P.kwargs) -> None:
        try:
            step(self, *args, **kwargs)
        except BaseException:
            self._unlock()
            raise

    return guarded


class ResourceBundleFlow(ModalImportFlowMixin):
    """Export Resources… / Import Resources… for the Settings footer."""

    def __init__(
        self,
        *,
        parent: QWidget,
        get_config: Callable[[], AnkiMinerConfig],
        acquire: Callable[[], bool],
        release: Callable[[], None],
        on_imported: Callable[[BundleInstallResult], None],
        wordlists_root: Path,
    ) -> None:
        self._parent = parent
        self._get_config = get_config
        self._acquire = acquire
        self._release = release
        self._on_imported = on_imported
        self._wordlists_root = wordlists_root
        self._held = False
        self._active_import_worker: ImportWorker | None = None
        self._retained_import_workers: list[ImportWorker] = []

    def iter_close_workers(self) -> tuple:
        return self._iter_import_workers()

    # --- Lock -------------------------------------------------------------

    def _lock(self) -> bool:
        if self._held or not self._acquire():
            return False
        self._held = True
        return True

    def _set_import_buttons_enabled(self, enabled: bool) -> None:
        # Mixin hook: False when its modal dialog opens (already held since the
        # click, so nothing to do), True when it closes or refuses -- release.
        if enabled and self._held:
            self._held = False
            self._release()

    def _unlock(self) -> None:
        self._set_import_buttons_enabled(True)

    # --- Export -----------------------------------------------------------
    # Every step from the click to the worker start carries
    # @_releases_lock_on_error; after that the mixin owns the release.

    @_releases_lock_on_error
    def export_resources(self) -> None:
        if not self._lock():
            return
        config = self._get_config()
        known_words_db = resolve_known_words_db_path(config)
        run_off_thread(
            self._parent,
            lambda: collect_export_candidates(config, known_words_db=known_words_db),
            lambda found: self._choose_export(config, known_words_db, cast(list[ExportCandidate], found)),
            self._on_export_scan_failed,
        )

    def _on_export_scan_failed(self, message: str) -> None:
        self._unlock()
        self._report_import_issue(
            QCoreApplication.translate("ResourceBundleFlow", "Your installed resources could not be checked."),
            message,
        )

    @_releases_lock_on_error
    def _choose_export(self, config: AnkiMinerConfig, known_words_db: Path, candidates: list[ExportCandidate]) -> None:
        language = config_language(config)
        if not any(not c.unavailable for c in candidates):
            self._unlock()
            QMessageBox.information(
                self._parent,
                QCoreApplication.translate("ResourceBundleFlow", "Nothing to Export"),
                tr_format(
                    QCoreApplication.translate(
                        "ResourceBundleFlow",
                        "Your %1 setup has no dictionary, frequency or pitch list, ignore list or word list "
                        "that can be exported.",
                    ),
                    language_display_name(language),
                ),
            )
            return
        dialog = ResourceBundleDialog(
            [self._export_choice(c) for c in candidates],
            title=QCoreApplication.translate("ResourceBundleFlow", "Export Resources"),
            intro=tr_format(
                QCoreApplication.translate(
                    "ResourceBundleFlow",
                    "Choose what goes into the %1 resource bundle. Dictionaries and lists travel as their "
                    "original files and are rebuilt when the bundle is imported.",
                ),
                language_display_name(language),
            ),
            accept_text=QCoreApplication.translate("ResourceBundleFlow", "Export…"),
            parent=self._parent,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            self._unlock()
            return
        chosen = set(dialog.selected_items())
        selected = [c for c in candidates if c.item in chosen]
        file_dialogs.pick_save_file(
            self._parent,
            QCoreApplication.translate("ResourceBundleFlow", "Export Resources"),
            str(Path(resolve_start_dir(None, file_mode=True)) / f"anki_miner_resources_{language}.zip"),
            QCoreApplication.translate("ResourceBundleFlow", "Resource bundles (*.zip)"),
            on_done=lambda target: self._write(target, selected, language, known_words_db),
        )

    def _export_choice(self, candidate: ExportCandidate) -> BundleChoice:
        if candidate.unavailable == "no_source":
            reason = QCoreApplication.translate(
                "ResourceBundleFlow", "no original file was kept; add it again from its file to include it"
            )
        elif candidate.unavailable == "other_language":
            reason = QCoreApplication.translate("ResourceBundleFlow", "imported for another mining language")
        else:
            reason = ""
        if candidate.word_count:
            detail = tr_format(QCoreApplication.translate("ResourceBundleFlow", "%1 words"), candidate.word_count)
        elif candidate.size_bytes:
            detail = format_data_size(QLocale(), candidate.size_bytes)
        else:
            detail = ""
        return BundleChoice(candidate.item, detail=detail, disabled_reason=reason)

    @_releases_lock_on_error
    def _write(self, target: str, selected: list[ExportCandidate], language: str, known_words_db: Path) -> None:
        if not target:
            self._unlock()
            return
        path = Path(target)

        def runner(progress: ProgressFn, cancel: CancelFn) -> tuple[str, dict[str, Any]]:
            result = write_resource_bundle(
                path,
                selected,
                language=language,
                app_version=__version__,
                known_words_db=known_words_db,
                progress=progress,
                cancel_check=cancel,
            )
            return str(result.path), {"result": result}

        self._run_modal_import(
            worker=ImportWorker(runner),
            progress_label=QCoreApplication.translate("ResourceBundleFlow", "Exporting resources…"),
            cancel_label=QCoreApplication.translate("ResourceBundleFlow", "Cancel"),
            determinate=True,
            join_noun="resource bundle worker",
            failure_summary=QCoreApplication.translate("ResourceBundleFlow", "The resources could not be exported."),
            refusal_message=QCoreApplication.translate(
                "ResourceBundleFlow", "A resource export or import is still finishing. Try again in a moment."
            ),
            cancelling_label=QCoreApplication.translate("ResourceBundleFlow", "Cancelling…"),
            missing_result_message=QCoreApplication.translate(
                "ResourceBundleFlow", "The export stopped without reporting a result."
            ),
            trace_id=_begin_import_trace("resource export"),
            on_success=self._on_exported,
        )

    def _on_exported(self, _path: str, meta: dict) -> None:
        result = cast(BundleWriteResult, meta["result"])
        clear_reported_issue(self._parent)
        QMessageBox.information(
            self._parent,
            QCoreApplication.translate("ResourceBundleFlow", "Resources Exported"),
            tr_format(
                QCoreApplication.translate("ResourceBundleFlow", "Resources written: %1 (%2)\n%3"),
                result.item_count,
                format_data_size(QLocale(), result.size_bytes),
                str(result.path),
            ),
        )

    # --- Import -----------------------------------------------------------

    @_releases_lock_on_error
    def import_resources(self) -> None:
        if not self._lock():
            return
        file_dialogs.pick_open_file(
            self._parent,
            QCoreApplication.translate("ResourceBundleFlow", "Import Resources"),
            resolve_start_dir(None, file_mode=True),
            QCoreApplication.translate("ResourceBundleFlow", "Resource bundles (*.zip);;All Files (*)"),
            on_done=self._read_bundle,
        )

    @_releases_lock_on_error
    def _read_bundle(self, source: str) -> None:
        if not source:
            self._unlock()
            return
        path = Path(source)
        config = self._get_config()
        run_off_thread(
            self._parent,
            lambda: _read_and_plan(path, config),
            lambda planned: self._choose_import(path, *cast(tuple[BundleManifest, list[ImportCandidate]], planned)),
            self._on_unreadable_bundle,
        )

    def _on_unreadable_bundle(self, message: str) -> None:
        self._unlock()
        self._report_import_issue(
            QCoreApplication.translate("ResourceBundleFlow", "That file is not a resource bundle Anki Miner can read."),
            message,
        )

    @_releases_lock_on_error
    def _choose_import(self, path: Path, manifest: BundleManifest, candidates: list[ImportCandidate]) -> None:
        config = self._get_config()
        if manifest.language != config_language(config):
            self._unlock()
            self._report_import_issue(
                tr_format(
                    QCoreApplication.translate(
                        "ResourceBundleFlow",
                        "This bundle holds %1 resources. Switch the mining language to %1, then import it again.",
                    ),
                    language_display_name(manifest.language),
                )
            )
            return
        if not any(not c.blocked for c in candidates):
            self._unlock()
            QMessageBox.information(
                self._parent,
                QCoreApplication.translate("ResourceBundleFlow", "Nothing to Import"),
                QCoreApplication.translate(
                    "ResourceBundleFlow", "You already have everything this bundle holds, so nothing was changed."
                ),
            )
            return
        dialog = ResourceBundleDialog(
            [self._import_choice(c) for c in candidates],
            title=QCoreApplication.translate("ResourceBundleFlow", "Import Resources"),
            intro=QCoreApplication.translate(
                "ResourceBundleFlow",
                "Choose what to install. Each dictionary and list is rebuilt from its original file, which "
                "can take several minutes for a large dictionary. New dictionaries go to the top of your list; "
                "the bundle's ignore list is added to yours; nothing you already have is replaced.",
            ),
            accept_text=QCoreApplication.translate("ResourceBundleFlow", "Import"),
            parent=self._parent,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            self._unlock()
            return
        selected = dialog.selected_items()
        known_words_db = resolve_known_words_db_path(config)
        wordlists_root = self._wordlists_root

        def runner(progress: ProgressFn, cancel: CancelFn) -> tuple[str, dict[str, Any]]:
            result = install_resource_bundle(
                path,
                manifest,
                selected,
                config,
                known_words_db=known_words_db,
                wordlists_root=wordlists_root,
                progress=progress,
                cancel_check=cancel,
            )
            return str(path), {"result": result}

        self._run_modal_import(
            worker=ImportWorker(runner, source_path=path),
            progress_label=QCoreApplication.translate("ResourceBundleFlow", "Importing resources…"),
            cancel_label=QCoreApplication.translate("ResourceBundleFlow", "Cancel"),
            determinate=True,
            join_noun="resource bundle worker",
            failure_summary=QCoreApplication.translate("ResourceBundleFlow", "The resources could not be imported."),
            refusal_message=QCoreApplication.translate(
                "ResourceBundleFlow", "A resource export or import is still finishing. Try again in a moment."
            ),
            cancelling_label=QCoreApplication.translate("ResourceBundleFlow", "Cancelling…"),
            missing_result_message=QCoreApplication.translate(
                "ResourceBundleFlow", "The import stopped without reporting a result."
            ),
            trace_id=_begin_import_trace("resource import"),
            on_success=self._on_installed,
            on_success_error=lambda exc: self._report_import_issue(
                QCoreApplication.translate(
                    "ResourceBundleFlow",
                    "The resources were installed, but your settings could not be updated to use them.",
                ),
                str(exc),
            ),
        )

    def _import_choice(self, candidate: ImportCandidate) -> BundleChoice:
        if candidate.blocked == "installed":
            reason = QCoreApplication.translate("ResourceBundleFlow", "already installed")
        elif candidate.blocked == "configured":
            reason = QCoreApplication.translate("ResourceBundleFlow", "you already use one")
        else:
            reason = ""
        word_count = dict(candidate.item.options).get("word_count", "")
        detail = (
            tr_format(QCoreApplication.translate("ResourceBundleFlow", "%1 words"), word_count)
            if candidate.item.kind == "known_words" and word_count
            else ""
        )
        return BundleChoice(candidate.item, detail=detail, disabled_reason=reason)

    def _on_installed(self, _path: str, meta: dict) -> None:
        result = cast(BundleInstallResult, meta["result"])
        if result.installed:
            self._on_imported(result)  # raises into on_success_error on a commit failure
        if not result.installed and result.failures and not result.cancelled:
            self._report_import_issue(
                QCoreApplication.translate("ResourceBundleFlow", "None of the chosen resources could be installed."),
                "\n".join(f"{name}: {message}" for name, message in result.failures),
            )
            return
        clear_reported_issue(self._parent)
        body = format_batch_summary(
            [
                (
                    QCoreApplication.translate("ResourceBundleFlow", "Installed:"),
                    [self._installed_label(item, result) for item in result.installed],
                ),
                (
                    QCoreApplication.translate("ResourceBundleFlow", "Not installed:"),
                    [f"{name}: {message}" for name, message in result.failures],
                ),
            ],
            cancelled_note=(
                QCoreApplication.translate(
                    "ResourceBundleFlow", "The import was cancelled. What finished installing is kept."
                )
                if result.cancelled
                else None
            ),
            empty=QCoreApplication.translate("ResourceBundleFlow", "Nothing was installed."),
        )
        QMessageBox.information(
            self._parent,
            QCoreApplication.translate("ResourceBundleFlow", "Resources Imported"),
            body,
        )

    @staticmethod
    def _installed_label(item: BundleItem, result: BundleInstallResult) -> str:
        if item.kind == "known_words":
            return tr_format(
                QCoreApplication.translate("ResourceBundleFlow", "Your ignore list (%1 new words)"),
                result.known_words_added,
            )
        if item.kind == "blacklist":
            return QCoreApplication.translate("ResourceBundleFlow", "Blacklist")
        if item.kind == "whitelist":
            return QCoreApplication.translate("ResourceBundleFlow", "Whitelist")
        return item.name
