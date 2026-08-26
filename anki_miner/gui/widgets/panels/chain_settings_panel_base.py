"""Shared base for the reorderable chain settings panels.

Hoists the verbatim-identical state machine shared by
:class:`~anki_miner.gui.widgets.panels.dictionary_settings_panel.DictionarySettingsPanel`,
:class:`~anki_miner.gui.widgets.panels.frequency_settings_panel.FrequencySettingsPanel`,
and
:class:`~anki_miner.gui.widgets.panels.audio_pack_settings_panel.AudioPackSettingsPanel`:
the lazy first-show registry scan, the off-thread rescan/redispatch dance, the
reorder and destructive-remove flows, the loading placeholder, and the row-list
rebuild.

Since D13 it also owns what the four panels *look* like: ``_build_chain_container``
builds the explanation, the drag-reorderable
:class:`~anki_miner.gui.widgets.panels.chain_priority_list.ChainPriorityList` and
the one toolbar all four share, so there is a single answer to "where does Add
go, and what colour is Remove".

Per-panel deltas stay subclass responsibilities via explicit hooks (see the
"Subclass hooks" section): the field layout (``_setup_fields``), the entry
type's ``set_chain`` marshalling and its ``_entry_with_enabled`` clone, the
off-thread registry factory (``_build_view``), what a row says (``_row_spec``),
the context menu, and the remove-flow specifics (protected kinds, disk-less
removal, confirm/release dialogs). All user-facing strings stay bound to each
subclass's own ``self.tr`` context — either textually inside a subclass method
or, for the literals the hoisted slots need, via the :class:`_ChainPanelStrings`
and :class:`ChainListLabels` objects each subclass builds with ``self.tr(...)``
(the ``_ToolTabStrings`` precedent). The base itself makes no ``tr()`` call, so
extraction contexts never churn.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QShowEvent
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from anki_miner.gui.resources.styles import SPACING
from anki_miner.gui.utils.config_commit import ConfigCommitResult
from anki_miner.gui.utils.focus_ring import KEYBOARD_FOCUS_PROPERTY
from anki_miner.gui.utils.run_off_thread import run_off_thread
from anki_miner.gui.widgets.base import FormPanel, ScreenIssue, ScreenIssueHost
from anki_miner.gui.widgets.enhanced.modern_button import ButtonVariant, ModernButton
from anki_miner.gui.widgets.panels.chain_priority_list import (
    ChainPriorityList,
    ChainRowActions,
    ChainRowSpec,
    ChainSourceRow,
)
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

    Every failure string here is a *summary*: the sentence and the guidance,
    with no raw path and no exception text (D24). Those go in the issue's
    Details, which the base fills from the values it already has.
    """

    loading: str
    retry_label: str
    scan_failed_summary: str
    files_left_summary: str
    # ``tr_format`` templates whose one substitution is the entry's display name.
    intact_failure_summary: str
    partial_failure_summary: str
    config_pending_failure_summary: str
    post_save_summary: str
    cleanup_pending_summary: str


@dataclass(frozen=True)
class ChainListLabels:
    """Per-panel translated strings for the shared list and its toolbar.

    Built in each subclass with ``self.tr(...)``, like :class:`_ChainPanelStrings`
    -- the base reads already-translated text and never calls ``tr()`` itself.

    ``explanation`` is the one line above the list, and it is *not* the same
    sentence on every panel: dictionaries, word audio and pitch accent return the
    first source that has an entry, while frequency layers its sources additively
    and only uses chain order to break rank ties. Writing one sentence for all
    four would document a lie on one of them.
    """

    explanation: str
    add: str
    remove: str
    move_up: str
    move_down: str
    remove_tooltip: str = ""
    move_up_tooltip: str = ""
    move_down_tooltip: str = ""


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


class ChainSettingsPanelBase(ScreenIssueHost, FormPanel):
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
    # WARNING/ERROR log labels (English, not user-facing → not translated).
    _SCAN_ERROR_LABEL: ClassVar[str] = "Registry scan failed"
    _REMOVE_ERROR_NOUN: ClassVar[str] = "folder"

    #: Glyph on the remove control. The move arrows live on the rows, so their
    #: glyphs live with them in ``chain_priority_list``.
    #:
    #: Remove was U+1F5D1 WASTEBASKET followed by U+FE0E, which asks for text
    #: presentation. Linux font matching ignores that request -- fontconfig
    #: hands the astral code point to the colour emoji font anyway -- so the
    #: control shipped as a 3D teal bin in an otherwise flat monochrome UI. No
    #: monochrome trash can exists to swap in; every trash code point pulls the
    #: emoji font on some platform. U+2715 is the same multiplication X the
    #: update banner already dismisses with, and the same kind of glyph as the
    #: two arrows beside it. Do not reach for a bin again.
    _REMOVE_GLYPH: ClassVar[str] = "✕"

    # --- Instance attributes the base builds in _build_chain_container ---
    _list: ChainPriorityList
    _explanation_label: QLabel
    _add_btn: ModernButton
    _remove_btn: ModernButton
    _row_actions: ChainRowActions
    _strings: _ChainPanelStrings

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(title, parent=parent)
        # Directly under the panel heading, above the chain list: a scan that
        # failed is a statement about the list the user is looking at (D24).
        self.install_issue_banner(self._main_layout, 1)
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
        self._external_mutation_preflight: Callable[[], bool] | None = None
        self._mutation_preflight: Callable[[], bool] | None = None
        self._remove_mutation_token: MutationToken | None = None
        self._remove_chain_commit: Callable[[tuple[Any, ...]], ConfigCommitResult] | None = None
        self._after_scan_callbacks: list[Callable[[], None]] = []
        # Set while the base is repopulating the list. Rebuilding removes and
        # re-adds every row, so anything that reads the *visual* order has to
        # stand down until the render finishes -- otherwise a rebuild would look
        # like a reorder and persist a half-built chain.
        self._rebuilding: bool = False

    # ------------------------------------------------------------------
    # Shared list + toolbar
    # ------------------------------------------------------------------

    def _build_chain_container(
        self,
        labels: ChainListLabels,
        *,
        extra_actions: tuple[ModernButton, ...] = (),
    ) -> QWidget:
        """Build the explanation, the drag-reorderable list, and the toolbar.

        This is the whole of D13's "one real list": drag to reorder, small square
        arrows as the keyboard path onto the same move, one clear primary Add,
        quiet panel-specific maintenance actions beside it, and exactly one red
        control -- the trash, an outline rather than a fill because removing a
        source is reversible by re-importing it (D41).

        Args:
            labels: This panel's translated strings.
            extra_actions: Quiet per-panel maintenance buttons, placed after Add.

        Returns:
            The container to hand to ``add_field``. The caller owns the anchor.
        """
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACING.xs)

        self._explanation_label = QLabel(labels.explanation)
        self._explanation_label.setObjectName("helper-text")
        self._explanation_label.setWordWrap(True)
        layout.addWidget(self._explanation_label)

        # The move controls live on the rows, so their copy is panel-wide state
        # the rows are handed rather than something each row spec repeats.
        self._row_actions = ChainRowActions(
            move_up=labels.move_up,
            move_down=labels.move_down,
            move_up_tooltip=labels.move_up_tooltip,
            move_down_tooltip=labels.move_down_tooltip,
        )

        self._list = ChainPriorityList()
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.order_changed.connect(self._sync_chain_from_visual_order)
        layout.addWidget(self._list)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(SPACING.xs)

        self._add_btn = ModernButton(labels.add, variant="primary")
        toolbar.addWidget(self._add_btn)
        for action in extra_actions:
            toolbar.addWidget(action)
        toolbar.addStretch()

        self._remove_btn = self._make_square_button(
            self._REMOVE_GLYPH,
            "danger",
            labels.remove,
            labels.remove_tooltip,
        )
        self._remove_btn.clicked.connect(lambda: self.remove(self._list.currentRow()))
        toolbar.addWidget(self._remove_btn)

        layout.addLayout(toolbar)
        return container

    @staticmethod
    def _make_square_button(glyph: str, variant: ButtonVariant, name: str, tooltip: str) -> ModernButton:
        """One glyph-only control, named for anyone who cannot see the glyph."""
        button = ModernButton(glyph, variant=variant, square=True)
        button.setAccessibleName(name)
        button.setToolTip(tooltip or name)
        return button

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
        # The list is now trustworthy again — that is the only thing that
        # clears a scan issue.
        self.clear_screen_issue()
        self._rebuild_list()
        self._finish_scan_mutation()
        if not self._redispatch_pending_scan():
            self._run_after_scan_callbacks()

    def _on_scan_error(self, msg: str) -> None:
        self._scan_in_flight = False
        logger.warning("%s: %s", self._SCAN_ERROR_LABEL, msg)
        # A failed scan used to log and nothing else, so the panel rendered a
        # list of rows with no metadata behind them and looked fine (D24).
        self.show_screen_issue(
            ScreenIssue(
                summary=self._strings.scan_failed_summary,
                details=msg,
                action_id="chain.rescan",
                action_text=self._strings.retry_label,
            ),
            action=self.refresh_registry,
        )
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

    def _owns_focus(self, widget: QWidget | None) -> bool:
        """Is ``widget`` this panel, or something inside it?"""
        return widget is not None and (widget is self or self.isAncestorOf(widget))

    @contextmanager
    def _keep_focus_in_list(self) -> Generator[None]:
        """Hold keyboard focus inside this panel across a clear-and-repopulate.

        Rebuilding destroys every row widget, and the placeholder path disables
        the reorder controls on top of that. Qt answers either by calling
        ``focusNextChild()``, which WRAPS the window's tab order — and the first
        focusable widget in the window is the header's theme selector, so a
        click on a row's Enabled checkbox ended with focus (and, before the
        stylesheet fix, an accent border) in the top-right corner of the app.

        Restores whenever the widget that held focus no longer does — it was
        destroyed or disabled — and leaves an untouched control alone. Where Qt
        dropped focus in the meantime is not the test: it sometimes parks it on
        the list itself with ``TabFocusReason``, which is inside the panel but
        wears the keyboard ring, so "still in the panel" is not good enough.

        The target is the same row index when the rebuild produced one, and the
        list itself otherwise (after the loading placeholder there are no rows to
        go back to).

        ``OtherFocusReason`` is load-bearing: it is not in
        ``focus_ring.KEYBOARD_FOCUS_REASONS``, so putting focus back marks
        nothing and paints no keyboard ring on a list the user reached with the
        mouse.
        """
        focused = QApplication.focusWidget()
        had_focus = self._owns_focus(focused)
        row_index = -1
        if had_focus and focused is not None:
            for index in range(self._list.count()):
                row = self._row_widget(index)
                if row is not None and (row is focused or row.isAncestorOf(focused)):
                    row_index = index
                    break
        try:
            yield
        finally:
            # Identity, not attribute access: `focused` may be a destroyed C++
            # object by now, and `is` never touches it.
            if had_focus and QApplication.focusWidget() is not focused:
                target: QWidget = self._list
                restored_row = self._row_widget(row_index) if row_index >= 0 else None
                if restored_row is not None:
                    target = restored_row.checkbox
                # Qt may already have parked focus on the target — with
                # TabFocusReason, which marks it for the keyboard ring. setFocus
                # on a widget that already holds focus sends NO QFocusEvent, so
                # the mark would survive and ring a list the user clicked. Drop
                # focus first and the filter clears the mark on the way out.
                if target.hasFocus():
                    target.clearFocus()
                target.setFocus(Qt.FocusReason.OtherFocusReason)

    def _show_loading_placeholder(self) -> None:
        """Render a single disabled 'Loading…' row while a scan is in flight."""
        # The guard wraps _set_reorder_controls_enabled too: disabling a focused
        # button is its own way to throw focus out of the panel.
        with self._keep_focus_in_list():
            # No real rows exist during the scan, so disable the reorder/remove
            # controls explicitly (they act on currentRow(), which would otherwise
            # operate on a transient placeholder); _rebuild_list re-enables them.
            self._set_reorder_controls_enabled(False)
            self._rebuilding = True
            self._list.setUpdatesEnabled(False)
            try:
                self._list.clear()
                placeholder = QListWidgetItem(self._strings.loading)
                # NoItemFlags also strips ItemIsDragEnabled, so the placeholder
                # cannot be dragged into a reorder of a chain it is not part of.
                placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
                self._list.addItem(placeholder)
            finally:
                self._list.setUpdatesEnabled(True)
                self._rebuilding = False

    def _set_reorder_controls_enabled(self, enabled: bool) -> None:
        """Toggle every way to reorder or remove a row, drag included.

        The move controls live on the rows now, so this walks them. When they
        are on, they are still boundary-aware: the first row cannot move up and
        the last cannot move down, so a button is only offered when pressing it
        would do something.
        """
        rows = self._rows()
        last = len(rows) - 1
        for index, row in enumerate(rows):
            row.set_move_enabled(up=enabled and index > 0, down=enabled and index < last)
        self._remove_btn.setEnabled(enabled)
        # Dragging is a reorder like any other, so it answers to the same gate.
        self._list.setDragEnabled(enabled)

    def _resync_move_boundaries(self) -> None:
        """Re-derive which arrows are offered, after the rows have moved.

        The ends of the list are a property of the current order, and a move
        changes it: without this the row that left index 0 keeps a greyed-out
        ``↑`` and the row that arrived keeps a live one that :meth:`_move_row`
        then refuses. Nothing else re-derives it —
        :meth:`_set_reorder_controls_enabled` otherwise runs only on a rebuild
        or a mutation-token cycle, so the stale state survived until the panel
        happened to re-render.

        Deliberately not :meth:`_sync_mutation_controls`: that also drives
        ``_set_mutation_controls_enabled``, a subclass hook owning Add and root
        controls for reasons a reorder has no opinion about.

        Disabling the arrow the user just pressed is its own way to throw focus
        out of the panel, so the press is handed to that row's other arrow: same
        row, still enabled, and it undoes the move that disabled the first one.
        It arrives with the focus reason the pressed arrow had earned, so a
        keyboard user keeps the ring and a mouse user still does not get one.
        ``_keep_focus_in_list`` is the net for every other way focus can leave.
        """
        pressed = QApplication.focusWidget()
        pressed_by_keyboard = bool(pressed is not None and pressed.property(KEYBOARD_FOCUS_PROPERTY))
        with self._keep_focus_in_list():
            self._set_reorder_controls_enabled(not self.has_active_mutation())
        if pressed is None or pressed.isEnabled():
            return
        for row in self._rows():
            sibling = row.other_arrow(pressed)
            if sibling is None:
                continue
            if sibling.isEnabled():
                reason = Qt.FocusReason.TabFocusReason if pressed_by_keyboard else Qt.FocusReason.OtherFocusReason
                sibling.setFocus(reason)
            return

    def _rows(self) -> list[ChainSourceRow]:
        """Every rendered row, in visual order. Empty during a placeholder."""
        found = []
        for index in range(self._list.count()):
            row = self._row_widget(index)
            if row is not None:
                found.append(row)
        return found

    def _on_row_move_up(self, row: ChainSourceRow) -> None:
        """Move the row whose button was pressed, not the selected one."""
        index = self._index_of_row(row)
        if index is not None:
            self.move_up(index)

    def _on_row_move_down(self, row: ChainSourceRow) -> None:
        index = self._index_of_row(row)
        if index is not None:
            self.move_down(index)

    def _index_of_row(self, row: ChainSourceRow) -> int | None:
        """Where ``row`` currently sits, by widget identity.

        Deliberately not ``currentRow()``: pressing row 3's arrow must move row
        3 whether or not row 3 is the selection. Identity also survives a drag,
        which is why the rows carry their own entry.
        """
        for index in range(self._list.count()):
            if self._row_widget(index) is row:
                return index
        return None

    # ------------------------------------------------------------------
    # Mutation ownership
    # ------------------------------------------------------------------

    def set_mutation_preflight(self, callback: Callable[[], bool] | None) -> None:
        """Set the synchronous settings commit required before a mutation."""
        self._mutation_preflight = callback

    def set_external_mutation_preflight(self, callback: Callable[[], bool] | None) -> None:
        """Set a preflight that must finish before mutation ownership is checked."""
        self._external_mutation_preflight = callback

    def set_remove_chain_commit(
        self,
        callback: Callable[[tuple[Any, ...]], ConfigCommitResult] | None,
    ) -> None:
        """Set the synchronous, outcome-aware chain commit used by remove."""
        self._remove_chain_commit = callback

    def prepare_for_mutation(self) -> bool:
        """Commit pending settings, refusing overlap with an active mutation."""
        if self._external_mutation_preflight is not None and not self._external_mutation_preflight():
            return False
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
        self._set_row_repair_enabled(enabled)
        self._set_mutation_controls_enabled(enabled)

    def _set_row_repair_enabled(self, enabled: bool) -> None:
        """Toggle every row's optional repair button (e.g. Re-import).

        A second import launched while one is in flight would clobber the
        panel's single active-import worker and orphan the first one.
        """
        for index in range(self._list.count()):
            row = self._row_widget(index)
            if row is not None and row.repair_button is not None:
                row.repair_button.setEnabled(enabled)

    # ------------------------------------------------------------------
    # Reorder / toggle
    # ------------------------------------------------------------------

    def get_chain(self) -> tuple[Any, ...]:
        """Return the chain with live checkbox states folded back in.

        Rows that are not currently rendered (before first show, or while the
        Loading placeholder is up) fall back to the entry's own flag, so reading
        the chain mid-scan cannot silently switch every source off.
        """
        out: list[Any] = []
        for index, entry in enumerate(self._chain):
            row = self._row_widget(index)
            enabled = row.get_enabled() if row is not None else entry.enabled
            out.append(self._entry_with_enabled(entry, enabled))
        return tuple(out)

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
        """Move the row at *index* one place towards the front of the chain."""
        self._move_row(index, index - 1)

    def move_down(self, index: int) -> None:
        """Move the row at *index* one place towards the back of the chain."""
        self._move_row(index, index + 1)

    def _move_row(self, source: int, target: int) -> None:
        """Reorder through the model, exactly as a drag does.

        The arrows are the keyboard path onto drag-and-drop, not a second
        implementation of it: they move the same model row, which raises the
        same ``rowsMoved``, which lands in the same
        :meth:`_sync_chain_from_visual_order`. One code path means the two can
        never disagree about what order was persisted.
        """
        if self._rebuilding or self.has_active_mutation():
            return
        count = self._list.count()
        if count != len(self._chain):
            # A placeholder or a half-rendered list: its visual order is not the
            # chain, so moving a row in it would rebase onto nothing real.
            return
        if not (0 <= source < count) or not (0 <= target < count) or source == target:
            return
        if self._list.move_row(source, target):
            self._list.setCurrentRow(target)

    def _sync_chain_from_visual_order(self) -> None:
        """Rebase ``_chain`` onto the order the row widgets are actually in.

        Called once per completed move, whether the user dragged the row or
        pressed an arrow. Reading the *widgets* rather than recomputing indices
        is what keeps a row's enabled flag attached to its own entry: each row
        still holds the exact entry object it was built from, so a move can
        reorder them but never re-pair them.
        """
        if self._rebuilding or self.has_active_mutation():
            return
        rows = [self._row_widget(index) for index in range(self._list.count())]
        if len(rows) != len(self._chain) or any(row is None for row in rows):
            # The list is not currently showing this chain (placeholder, or a
            # rejected drop that left a stray item). Re-render from the model
            # rather than persisting whatever happens to be on screen.
            self._rebuild_list()
            return
        # Before the no-op check below: the arrows answer to the *visual* order,
        # which a drop can shuffle without changing the chain it persists.
        self._resync_move_boundaries()
        reordered = [self._entry_with_enabled(row.entry, row.get_enabled()) for row in rows if row is not None]
        if reordered == self._chain:
            return  # a no-op drop is not an edit
        self._chain = reordered
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
            owns_target = (
                target_dir is not None and os.path.lexists(target_dir) and self._owns_entry_disk_dir(entry, target_dir)
            )

            confirm_remove = self._confirm_remove if owns_target else self._confirm_chain_only_remove
            if not confirm_remove(display):
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

            if not owns_target or not self._owns_entry_disk_dir(entry, target_dir):
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

        def report() -> None:
            try:
                self.show_screen_issue(
                    ScreenIssue(
                        summary=tr_format(
                            self._strings.cleanup_pending_summary,
                            self._entry_display_name(removed_entry),
                        ),
                        details=f"{tombstone}: {msg}",
                    )
                )
            finally:
                self._finish_remove_mutation()

        self._rescan_then(report)

    def _warn_files_left(self, target: object) -> None:
        self.show_screen_issue(
            ScreenIssue(summary=self._strings.files_left_summary, details=str(target)),
        )

    def _warn_post_save_failure(self, display: str, msg: str) -> None:
        self.show_screen_issue(
            ScreenIssue(summary=tr_format(self._strings.post_save_summary, display), details=msg),
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

        def report() -> None:
            try:
                if target_dir is not None and os.path.lexists(target_dir):
                    intact = files_untouched or self._owns_entry_disk_dir(removed_entry, target_dir)
                    template = self._strings.intact_failure_summary if intact else self._strings.partial_failure_summary
                else:
                    template = self._strings.config_pending_failure_summary
                self.show_screen_issue(
                    ScreenIssue(
                        summary=tr_format(template, self._entry_display_name(removed_entry)),
                        details=f"{target}: {msg}",
                    )
                )
            finally:
                self._finish_remove_mutation()

        self._rescan_then(report)

    # ------------------------------------------------------------------
    # Row list
    # ------------------------------------------------------------------

    def _row_widget(self, index: int) -> ChainSourceRow | None:
        item = self._list.item(index)
        if item is None:
            return None
        widget = self._list.itemWidget(item)
        return widget if isinstance(widget, ChainSourceRow) else None

    def _rebuild_list(self) -> None:
        # Suspend repaints across clear+populate so the reorder ↑↓ buttons don't
        # flash on each rebuild. clear() destroys the previous row widgets (and
        # their signal connections), so there is no duplicate-handler risk — but
        # destroying the focused row is what used to hand focus to the header,
        # hence the guard.
        with self._keep_focus_in_list():
            self._rebuild_list_rows()

    def _rebuild_list_rows(self) -> None:
        """Clear and repopulate the row list. Call through :meth:`_rebuild_list`."""
        self._rebuilding = True
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
                row = ChainSourceRow(self._row_spec(entry, view), self._row_actions)
                row.toggled.connect(self._on_row_toggled)
                row.move_up_requested.connect(self._on_row_move_up)
                row.move_down_requested.connect(self._on_row_move_down)
                self._connect_row_repair(row)
                item = QListWidgetItem()
                item.setSizeHint(row.sizeHint())
                self._list.addItem(item)
                self._list.setItemWidget(item, row)
        finally:
            self._list.setUpdatesEnabled(True)
            self._rebuilding = False
            self._sync_mutation_controls()

    # ------------------------------------------------------------------
    # Subclass hooks
    # ------------------------------------------------------------------

    def _setup_fields(self) -> None:
        """Build the panel's fields, calling ``_build_chain_container`` for the
        list, its explanation and its toolbar."""
        raise NotImplementedError

    def _set_mutation_controls_enabled(self, enabled: bool) -> None:
        """Toggle subclass-specific mutation triggers and root selectors."""

    def _entry_with_enabled(self, entry: Any, enabled: bool) -> Any:
        """Return a copy of *entry* carrying *enabled*.

        The entries are frozen dataclasses, so this is how a toggle is folded
        back into the chain. It is also what makes drag-reordering safe: the
        enabled flag is read off the row that owns the entry, never off an
        index into a list that has just moved.
        """
        raise NotImplementedError

    def _build_view(self) -> Any:
        """Construct + load the registry view OFF the GUI thread (no widgets)."""
        raise NotImplementedError

    def _row_spec(self, entry: Any, view: Any) -> ChainRowSpec:
        """Describe one row: title, its own metadata, toggle label, repair."""
        raise NotImplementedError

    def _connect_row_repair(self, row: ChainSourceRow) -> None:
        """Wire a row's optional repair button. Default: nothing to repair."""

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

    def _confirm_chain_only_remove(self, display: str) -> bool:
        """Show a chain-only confirmation when disk ownership is unproved."""
        return self._confirm_remove(display)

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
