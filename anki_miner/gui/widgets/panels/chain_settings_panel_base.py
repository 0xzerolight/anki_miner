"""Shared base for the reorderable chain settings panels.

Hoists the verbatim-identical state machine shared by
:class:`~anki_miner.gui.widgets.panels.dictionary_settings_panel.DictionarySettingsPanel`,
:class:`~anki_miner.gui.widgets.panels.frequency_settings_panel.FrequencySettingsPanel`,
and
:class:`~anki_miner.gui.widgets.panels.audio_pack_settings_panel.AudioPackSettingsPanel`:
the lazy first-show registry scan, the off-thread rescan/redispatch dance, the
reorder (move up/down) and destructive-remove flows, the loading placeholder, and
the row-list rebuild.

Per-panel deltas stay subclass responsibilities via explicit hooks (see the
"Subclass hooks" section): the field layout (``_setup_fields``), the entry type
and its ``get_chain``/``set_chain`` marshalling, the off-thread registry factory
(``_build_view``), row construction (``_make_row``), the context menu, and the
remove-flow specifics (protected kinds, disk-less removal, confirm/release
dialogs). All user-facing strings stay bound to each subclass's own ``self.tr``
context — either textually inside a subclass method or, for the few literals the
hoisted slots need, via the :class:`_ChainPanelStrings` object each subclass
builds with ``self.tr(...)`` (the ``_ToolTabStrings`` precedent). The base itself
makes no ``tr()`` call, so extraction contexts never churn.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QShowEvent
from PyQt6.QtWidgets import (
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QWidget,
)

from anki_miner.gui.utils.config_commit import ConfigCommitResult
from anki_miner.gui.utils.run_off_thread import run_off_thread
from anki_miner.gui.widgets.base import FormPanel
from anki_miner.services.store_recovery import make_tombstone_path
from anki_miner.utils.i18n import tr_format
from anki_miner.utils.robust_fs import RmtreeOutcome

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _ChainPanelStrings:
    """Per-panel translated labels consumed by the hoisted slots.

    Built in each subclass via ``self.tr(...)`` so every literal stays in that
    panel's tr-context (mirroring ``_ToolTabStrings`` in ``_tool_tab_base``).
    The base reads the already-translated strings — it never calls ``tr()``.
    """

    loading: str
    remove_failed_title: str
    # Already-translated ``tr_format`` template: "Could not delete %1:\n%2\n\n…".
    could_not_delete_template: str
    files_left_title: str
    files_left_template: str
    intact_failure_template: str
    partial_failure_template: str
    config_pending_failure_template: str
    post_save_warning_template: str
    cleanup_pending_template: str


@dataclass(frozen=True, eq=False)
class MutationToken:
    """Opaque ownership token for one panel mutation."""

    kind: str


class _RegistryView:
    """Uniform meta-lookup shim: ``get(id) -> meta | None``.

    Wraps a single getter callable so the frequency and audio panels can feed
    either a live registry (``registry.get`` / ``registry.packs.get``) or a
    pre-built ``dict`` injected by ``set_chain(registry_meta=...)`` (tests)
    through the same interface. The dictionary panel stores its
    ``DictionaryRegistry`` directly (it already exposes ``.get``) and does not
    need this shim.
    """

    def __init__(self, getter: Callable[[str], Any | None]) -> None:
        self._getter = getter

    def get(self, key: str) -> Any | None:
        return self._getter(key)


class ChainSettingsPanelBase(FormPanel):
    """State machine shared by the reorderable chain settings panels.

    See the module docstring. Subclasses provide the field layout, the entry
    type marshalling, and the remove/row/menu hooks; the base owns the scan and
    reorder/remove lifecycle.
    """

    # Persist-on-every-edit signal common to all three panels. The settings tab
    # wires this for reorder/toggle; remove uses an outcome-aware synchronous
    # commit callback so it can distinguish pre-save from post-save failure.
    chain_changed = pyqtSignal()

    # --- Class-level knobs the subclass sets (declared for the type checker) ---
    _ROW_CLASS: ClassVar[type]
    # WARNING/ERROR log labels (English, not user-facing → not translated).
    _SCAN_ERROR_LABEL: ClassVar[str] = "Registry scan failed"
    _REMOVE_ERROR_NOUN: ClassVar[str] = "folder"

    # --- Instance attributes the subclass builds in _setup_fields ---
    _list: QListWidget
    _up_btn: QPushButton
    _down_btn: QPushButton
    _remove_btn: QPushButton
    _strings: _ChainPanelStrings

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(title, parent=parent)
        self._chain: list[Any] = []
        # Cached registry view (subclass-typed); refreshed on demand instead of
        # per UI tick. The dictionary panel stores a DictionaryRegistry; the
        # frequency/audio panels store a _RegistryView.
        self._view: Any | None = None
        # Guard: registry scan deferred to first showEvent so it does not run on
        # the GUI thread before the window paints (OVH-053).
        self._scanned: bool = False
        # Set while an off-thread registry scan is running so overlapping scans /
        # removes don't stack (OVH disk-scan-off-thread).
        self._scan_in_flight: bool = False
        self._scan_mutation_token: MutationToken | None = None
        # Set when a rescan is requested while one is already in flight. The
        # in-flight worker captured the pre-request disk state, so dropping the
        # request would leave the panel showing stale data after an import. On
        # scan completion we re-dispatch a single fresh scan instead. A boolean
        # (not a counter) — one trailing scan reads the latest disk state, so
        # collapsing N pending requests into one re-dispatch cannot loop.
        self._rescan_pending: bool = False
        self._mutation_counts: dict[str, int] = {}
        self._mutation_tokens: set[MutationToken] = set()
        self._mutation_preflight: Callable[[], bool] | None = None
        self._remove_mutation_token: MutationToken | None = None
        self._remove_chain_commit: Callable[[tuple[Any, ...]], ConfigCommitResult] | None = None
        self._after_scan_callbacks: list[Callable[[], None]] = []

    # ------------------------------------------------------------------
    # First-show / refresh lifecycle
    # ------------------------------------------------------------------

    def showEvent(self, event: QShowEvent) -> None:  # type: ignore[override]
        """Trigger the first registry scan when the panel becomes visible.

        Defers the registry load off the app startup / first-paint path
        (OVH-053). Subsequent showEvent calls are no-ops; explicit refreshes
        (refresh_registry, set_chain) call _rebuild_list directly and bypass
        this guard.
        """
        super().showEvent(event)
        if not self._scanned:
            self._scanned = True
            self._scan_and_render_async()

    def refresh_registry(self) -> None:
        """Force a registry rescan. Call after an import finishes.

        The disk scan runs off the GUI thread; the row list re-renders once it
        completes (OVH disk-scan-off-thread).
        """
        self._view = None
        self._scanned = True
        self._scan_and_render_async()

    def _scan_and_render_async(self) -> None:
        """Scan the registry off-thread (if not cached) then render the rows.

        When the view is already cached this is a synchronous render — no worker
        is spawned — so callers that supplied meta directly (tests, set_chain)
        keep their immediate behavior. Otherwise a ``Loading…`` placeholder shows
        while the subclass ``_build_view`` runs on a worker thread.
        """
        if self._view is not None or not self._scanned:
            # Either cached, or not yet allowed to scan (pre-first-show).
            self._rebuild_list()
            return
        if self._scan_in_flight:
            # A scan is already running against the pre-request disk state. Mark
            # a rescan so the done/error callback re-dispatches once the current
            # scan finishes (otherwise an import's refresh is lost).
            self._rescan_pending = True
            return
        self._scan_in_flight = True
        self._scan_mutation_token = self.hold_mutation("scan")
        self._show_loading_placeholder()
        try:
            run_off_thread(self, self._build_view, self._on_scan_done, self._on_scan_error)
        except Exception:
            self._scan_in_flight = False
            self._finish_scan_mutation()
            raise

    def _on_scan_done(self, view: object) -> None:
        self._scan_in_flight = False
        self._view = view
        self._rebuild_list()
        self._finish_scan_mutation()
        if not self._redispatch_pending_scan():
            self._run_after_scan_callbacks()

    def _on_scan_error(self, msg: str) -> None:
        self._scan_in_flight = False
        logger.warning("%s: %s", self._SCAN_ERROR_LABEL, msg)
        # Render whatever we have (rows without metadata) so the panel isn't
        # stuck on the Loading placeholder.
        self._rebuild_list()
        self._finish_scan_mutation()
        if not self._redispatch_pending_scan():
            self._run_after_scan_callbacks()

    def _finish_scan_mutation(self) -> None:
        token = self._scan_mutation_token
        self._scan_mutation_token = None
        if token is not None:
            self.release(token)

    def _redispatch_pending_scan(self) -> bool:
        """Re-run one scan if a rescan was requested while one was in flight.

        Drops the now-stale cached view so the trailing scan reads the latest
        disk state. Single-shot: the flag is cleared before dispatch, so only
        the rescans requested *during* this dispatch can queue another.
        """
        if not self._rescan_pending:
            return False
        self._rescan_pending = False
        self._view = None
        self._scan_and_render_async()
        return True

    def _run_after_scan_callbacks(self) -> None:
        callbacks = self._after_scan_callbacks
        self._after_scan_callbacks = []
        for callback in callbacks:
            callback()

    def _rescan_then(self, callback: Callable[[], None]) -> None:
        """Refresh the registry off-thread before running a GUI continuation."""
        self._after_scan_callbacks.append(callback)
        self._view = None
        self._scanned = True
        try:
            self._scan_and_render_async()
        except Exception:
            logger.exception("Could not start registry rescan after remove")
            self._run_after_scan_callbacks()

    def _show_loading_placeholder(self) -> None:
        """Render a single disabled 'Loading…' row while a scan is in flight."""
        # No real rows exist during the scan, so disable the reorder/remove
        # controls explicitly (they act on currentRow(), which would otherwise
        # operate on a transient placeholder); _rebuild_list re-enables them.
        self._set_reorder_controls_enabled(False)
        self._list.setUpdatesEnabled(False)
        try:
            self._list.clear()
            placeholder = QListWidgetItem(self._strings.loading)
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            self._list.addItem(placeholder)
        finally:
            self._list.setUpdatesEnabled(True)

    def _set_reorder_controls_enabled(self, enabled: bool) -> None:
        """Toggle the move-up/down + remove buttons together."""
        self._up_btn.setEnabled(enabled)
        self._down_btn.setEnabled(enabled)
        self._remove_btn.setEnabled(enabled)

    # ------------------------------------------------------------------
    # Mutation ownership
    # ------------------------------------------------------------------

    def set_mutation_preflight(self, callback: Callable[[], bool] | None) -> None:
        """Set the synchronous settings commit required before a mutation."""
        self._mutation_preflight = callback

    def set_remove_chain_commit(
        self,
        callback: Callable[[tuple[Any, ...]], ConfigCommitResult] | None,
    ) -> None:
        """Set the synchronous, outcome-aware chain commit used by remove."""
        self._remove_chain_commit = callback

    def prepare_for_mutation(self) -> bool:
        """Commit pending settings, refusing overlap with an active mutation."""
        if self.has_active_mutation():
            return False
        return self._mutation_preflight is None or self._mutation_preflight()

    def hold_mutation(self, kind: str) -> MutationToken:
        """Hold one named mutation until its opaque token is released."""
        token = MutationToken(kind)
        self._mutation_tokens.add(token)
        self._mutation_counts[kind] = self._mutation_counts.get(kind, 0) + 1
        self._sync_mutation_controls()
        return token

    def release(self, token: MutationToken) -> None:
        """Release a mutation token once; repeated releases are no-ops."""
        if token not in self._mutation_tokens:
            return
        self._mutation_tokens.remove(token)
        remaining = self._mutation_counts[token.kind] - 1
        if remaining:
            self._mutation_counts[token.kind] = remaining
        else:
            del self._mutation_counts[token.kind]
        self._sync_mutation_controls()

    def has_active_mutation(self, kind: str | None = None) -> bool:
        """Return whether any token, or any token of ``kind``, is held."""
        if kind is None:
            return bool(self._mutation_tokens)
        return self._mutation_counts.get(kind, 0) > 0

    def _sync_mutation_controls(self) -> None:
        enabled = not self.has_active_mutation()
        self._list.setEnabled(enabled)
        self._set_reorder_controls_enabled(enabled)
        self._set_mutation_controls_enabled(enabled)

    # ------------------------------------------------------------------
    # Reorder / toggle
    # ------------------------------------------------------------------

    def _on_row_toggled(self) -> None:
        """Fold the live checkbox states back into ``self._chain`` before emitting.

        ``_rebuild_list`` renders checkboxes from ``self._chain``, so an unguarded
        rescan would re-render a just-disabled row from the stale chain and the
        next commit would re-persist ``enabled=True``. Syncing here keeps
        ``_chain`` authoritative.
        """
        if self.has_active_mutation():
            return
        self._chain = list(self.get_chain())
        self.chain_changed.emit()

    def move_up(self, index: int) -> None:
        if self.has_active_mutation():
            return
        if index <= 0 or index >= len(self._chain):
            return
        # Capture current enabled state before rebuild.
        self._chain = list(self.get_chain())
        self._chain[index - 1], self._chain[index] = self._chain[index], self._chain[index - 1]
        self._rebuild_list()
        self._list.setCurrentRow(index - 1)
        self.chain_changed.emit()

    def move_down(self, index: int) -> None:
        if self.has_active_mutation():
            return
        if index < 0 or index >= len(self._chain) - 1:
            return
        self._chain = list(self.get_chain())
        self._chain[index + 1], self._chain[index] = self._chain[index], self._chain[index + 1]
        self._rebuild_list()
        self._list.setCurrentRow(index + 1)
        self.chain_changed.emit()

    # ------------------------------------------------------------------
    # Destructive remove
    # ------------------------------------------------------------------

    def remove(self, index: int) -> None:
        if index < 0 or index >= len(self._chain):
            return
        entry = self._chain[index]
        if self._is_protected_entry(entry):
            return  # built-in / online entry: can be disabled but not removed
        if not self.prepare_for_mutation():
            return
        self._remove_mutation_token = self.hold_mutation("remove")
        async_started = False
        try:
            if self._handle_diskless_remove(entry, index):
                return  # subclass fully handled a source with nothing on disk

            # Resolve the display name + managed folder for the confirm prompt
            # and tombstone rename after pending settings have committed.
            display = self._entry_display_name(entry)
            target_dir = self._entry_disk_dir(entry)

            if not self._confirm_remove(display):
                return  # user declined the destructive-remove confirmation

            if target_dir is None:
                result = self._commit_removed_entry(entry)
                if not result.persisted:
                    self._report_remove_failure(entry, None, self._error_text(result))
                    async_started = True
                    return
                self._refresh_after_chain_only_remove()
                if not result.refreshed:
                    self._warn_post_save_failure(display, self._error_text(result))
                self._warn_files_left(display)
                return

            if not os.path.lexists(target_dir):
                result = self._commit_removed_entry(entry)
                if not result.persisted:
                    self._report_remove_failure(entry, target_dir, self._error_text(result))
                    async_started = True
                    return
                self._refresh_after_chain_only_remove()
                if not result.refreshed:
                    self._warn_post_save_failure(display, self._error_text(result))
                return

            if not self._owns_entry_disk_dir(entry, target_dir):
                result = self._commit_removed_entry(entry)
                if not result.persisted:
                    self._report_remove_failure(
                        entry,
                        target_dir,
                        self._error_text(result),
                        files_untouched=True,
                    )
                    async_started = True
                    return
                self._refresh_after_chain_only_remove()
                if not result.refreshed:
                    self._warn_post_save_failure(display, self._error_text(result))
                self._warn_files_left(target_dir)
                return

            # Give the subclass a chance to drop cached sqlite handles before
            # rename. Returns False to abort (e.g. mining in flight).
            if not self._acquire_release_for_remove():
                return

            tombstone = make_tombstone_path(target_dir)
            try:
                os.replace(target_dir, tombstone)
            except OSError as error:
                self._report_remove_failure(entry, target_dir, str(error))
                async_started = True
                return

            result = self._commit_removed_entry(entry)
            if not result.persisted:
                error_text = self._error_text(result)
                try:
                    os.replace(tombstone, target_dir)
                except OSError as rollback_error:
                    error_text = f"{error_text}; rollback failed: {rollback_error}"
                self._report_remove_failure(entry, target_dir, error_text)
                async_started = True
                return

            if not result.refreshed:
                self._warn_post_save_failure(display, self._error_text(result))

            try:
                run_off_thread(
                    self,
                    lambda: self._rmtree_dir(tombstone),
                    lambda outcome: self._on_tombstone_cleanup_done(entry, target_dir, tombstone, outcome),
                    lambda msg: self._on_tombstone_cleanup_error(entry, target_dir, tombstone, msg),
                )
            except Exception as error:
                self._report_cleanup_pending(entry, target_dir, tombstone, str(error))
            async_started = True
        finally:
            if not async_started:
                self._finish_remove_mutation()

    def _finish_remove_mutation(self) -> None:
        token = self._remove_mutation_token
        self._remove_mutation_token = None
        if token is not None:
            self.release(token)

    def _on_tombstone_cleanup_done(
        self,
        removed_entry: Any,
        target_dir: Path,
        tombstone: Path,
        outcome: object,
    ) -> None:
        if isinstance(outcome, tuple) and len(outcome) == 2 and outcome[0] is True:
            self._chain = list(self._chain_after_remove(removed_entry))
            self._view = None
            self._scan_and_render_async()
            self._finish_remove_mutation()
            return
        error = outcome[1] if isinstance(outcome, tuple) and len(outcome) == 2 else None
        self._report_cleanup_pending(
            removed_entry,
            target_dir,
            tombstone,
            str(error or "Unknown cleanup failure"),
        )

    def _on_tombstone_cleanup_error(
        self,
        removed_entry: Any,
        target_dir: Path,
        tombstone: Path,
        msg: str,
    ) -> None:
        self._report_cleanup_pending(removed_entry, target_dir, tombstone, msg)

    def _report_cleanup_pending(
        self,
        removed_entry: Any,
        target_dir: Path,
        tombstone: Path,
        msg: str,
    ) -> None:
        self._chain = list(self._chain_after_remove(removed_entry))
        logger.error("Failed to delete %s %s: %s", self._REMOVE_ERROR_NOUN, tombstone, msg)

        def show_warning() -> None:
            try:
                QMessageBox.warning(
                    self,
                    self._strings.remove_failed_title,
                    tr_format(
                        self._strings.cleanup_pending_template,
                        self._entry_display_name(removed_entry),
                        tombstone,
                        msg,
                    ),
                )
            finally:
                self._finish_remove_mutation()

        self._rescan_then(show_warning)

    def _warn_files_left(self, target: object) -> None:
        QMessageBox.warning(
            self,
            self._strings.files_left_title,
            tr_format(self._strings.files_left_template, target),
        )

    def _warn_post_save_failure(self, display: str, msg: str) -> None:
        QMessageBox.warning(
            self,
            self._strings.remove_failed_title,
            tr_format(self._strings.post_save_warning_template, display, msg),
        )

    @staticmethod
    def _error_text(result: ConfigCommitResult) -> str:
        return str(result.error or "Configuration commit failed")

    def _chain_after_remove(self, removed_entry: Any) -> tuple[Any, ...]:
        """Rebase one removal onto the current live chain."""
        removed_dir = self._entry_disk_dir(removed_entry)
        if removed_dir is None:
            return tuple(entry for entry in self.get_chain() if entry != removed_entry)
        return tuple(entry for entry in self.get_chain() if self._entry_disk_dir(entry) != removed_dir)

    def _commit_removed_entry(self, removed_entry: Any) -> ConfigCommitResult:
        new_chain = self._chain_after_remove(removed_entry)
        if self._remove_chain_commit is None:
            self._chain = list(new_chain)
            self.chain_changed.emit()
            return ConfigCommitResult.committed()
        try:
            result = self._remove_chain_commit(new_chain)
        except Exception as error:
            result = ConfigCommitResult.pre_save_failure(error)
        if result.persisted:
            self._chain = list(new_chain)
        return result

    def _refresh_after_chain_only_remove(self) -> None:
        self._view = None
        self._scan_and_render_async()

    def _report_remove_failure(
        self,
        removed_entry: Any,
        target_dir: Path | None,
        msg: str,
        *,
        files_untouched: bool = False,
    ) -> None:
        target = target_dir or self._entry_display_name(removed_entry)
        logger.error("Failed to remove %s %s: %s", self._REMOVE_ERROR_NOUN, target, msg)

        def show_warning() -> None:
            try:
                if target_dir is not None and os.path.lexists(target_dir):
                    intact = files_untouched or self._owns_entry_disk_dir(removed_entry, target_dir)
                    template = (
                        self._strings.intact_failure_template if intact else self._strings.partial_failure_template
                    )
                else:
                    template = self._strings.config_pending_failure_template
                QMessageBox.warning(
                    self,
                    self._strings.remove_failed_title,
                    tr_format(template, target, msg),
                )
            finally:
                self._finish_remove_mutation()

        self._rescan_then(show_warning)

    # ------------------------------------------------------------------
    # Row list
    # ------------------------------------------------------------------

    def _row_widget(self, index: int) -> Any:
        item = self._list.item(index)
        if item is None:
            return None
        widget = self._list.itemWidget(item)
        return widget if isinstance(widget, self._ROW_CLASS) else None

    def _rebuild_list(self) -> None:
        # Suspend repaints across clear+populate so the reorder ↑↓ buttons don't
        # flash on each rebuild. clear() destroys the previous row widgets (and
        # their signal connections), so there is no duplicate-handler risk.
        self._list.setUpdatesEnabled(False)
        try:
            self._list.clear()
            # Render-only: the disk scan is owned by _scan_and_render_async, which
            # runs the subclass registry load off the GUI thread and only then
            # calls back here with self._view populated. Before first show
            # (OVH-053) self._view is None and rows render without metadata — a
            # safe no-content state since the list is never visible until the
            # Settings tab is opened.
            view = self._view  # may be None before first show / scan
            for entry in self._chain:
                row = self._make_row(entry, view)
                item = QListWidgetItem()
                item.setSizeHint(row.sizeHint())
                self._list.addItem(item)
                self._list.setItemWidget(item, row)
        finally:
            self._list.setUpdatesEnabled(True)
            self._sync_mutation_controls()

    # ------------------------------------------------------------------
    # Subclass hooks
    # ------------------------------------------------------------------

    def _setup_fields(self) -> None:
        """Build the panel's fields, including ``self._list`` and the reorder
        buttons (``_up_btn`` / ``_down_btn`` / ``_remove_btn``)."""
        raise NotImplementedError

    def _set_mutation_controls_enabled(self, enabled: bool) -> None:
        """Toggle subclass-specific mutation triggers and root selectors."""

    def get_chain(self) -> tuple[Any, ...]:
        """Return the chain with live checkbox states folded back in."""
        raise NotImplementedError

    def _build_view(self) -> Any:
        """Construct + load the registry view OFF the GUI thread (no widgets)."""
        raise NotImplementedError

    def _make_row(self, entry: Any, view: Any) -> QWidget:
        """Build a fully-wired row widget for *entry* (``toggled`` connected)."""
        raise NotImplementedError

    def _entry_display_name(self, entry: Any) -> str:
        """Human-readable name for the remove-confirmation prompt."""
        raise NotImplementedError

    def _entry_disk_dir(self, entry: Any) -> Path | None:
        """On-disk managed folder, or None when nothing is on disk."""
        raise NotImplementedError

    def _owns_entry_disk_dir(self, entry: Any, target: Path) -> bool:
        """Return whether *target* is proven safe for recursive deletion."""
        raise NotImplementedError

    def _confirm_remove(self, display: str) -> bool:
        """Show the destructive-remove confirmation; return True to proceed."""
        raise NotImplementedError

    def _rmtree_dir(self, target: Path) -> RmtreeOutcome:
        """Delete *target* off-thread with non-raising cleanup semantics."""
        raise NotImplementedError

    def _is_protected_entry(self, entry: Any) -> bool:
        """True for entries that can be disabled but never removed (default: none)."""
        return False

    def _handle_diskless_remove(self, entry: Any, index: int) -> bool:
        """Fully handle removal of an entry with nothing on disk.

        Return True when handled (skips the confirm/release/tombstone flow). Default:
        not handled.
        """
        return False

    def _acquire_release_for_remove(self) -> bool:
        """Drop cached sqlite handles before rename; return False to abort.

        Default: no-op success (the audio panel keeps nothing open).
        """
        return True
