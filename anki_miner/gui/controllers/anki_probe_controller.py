"""AnkiConnect probe workers for the Settings tab (fields / decks / styling).

Extracted from ``SettingsTab`` (T-66). Owns the short-lived AnkiConnect worker
threads — fetch note-type fields, fetch deck list, and the card-styling write +
read-only probe — and surfaces their live handles through
:meth:`iter_close_workers` so ``MainWindow.closeEvent`` can route each through
its single join policy (the tab's ``iter_close_workers`` delegates here).

Each probe reads the note type / AnkiConnect URL straight from the panel
inputs (not the saved config) so the user can probe without first hitting
Save.
"""

from collections.abc import Callable
from dataclasses import replace
from typing import Literal

from PyQt6.QtCore import QCoreApplication
from PyQt6.QtWidgets import QMessageBox, QWidget

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.widgets.panels import AnkiSettingsPanel, FilteringSettingsPanel
from anki_miner.gui.workers.base_worker import SingleCallWorker
from anki_miner.gui.workers.fetch_decks_worker import FetchDecksWorker
from anki_miner.gui.workers.fetch_fields_worker import FetchFieldsWorker
from anki_miner.gui.workers.styling_worker import StylingWorker
from anki_miner.services.anki_service import AnkiService
from anki_miner.utils.i18n import tr_format


class AnkiProbeController:
    """Runs the Settings tab's AnkiConnect probes in worker threads.

    Args:
        parent: Widget used as the Qt parent for dialogs and the spawned
            worker threads (the settings tab), preserving lifetime and
            modality semantics.
        anki_panel: Source of the note-type / URL inputs and target of the
            field-list + styling status feedback.
        filtering_panel: Target of the fetched deck list (excluded-decks
            picker, Issue #38).
        get_config: Returns the tab's *current* config (it is reassigned on
            every save, so a snapshot would go stale).
    """

    def __init__(
        self,
        parent: QWidget,
        anki_panel: AnkiSettingsPanel,
        filtering_panel: FilteringSettingsPanel,
        get_config: Callable[[], AnkiMinerConfig],
    ) -> None:
        self._parent = parent
        self._anki_panel = anki_panel
        self._filtering_panel = filtering_panel
        self._get_config = get_config
        # Hold a reference to the fetch-fields worker across its lifetime.
        # Without this attribute, a freshly-spawned QThread can be garbage
        # collected before run() completes — Qt logs "QThread: Destroyed
        # while thread is still running" and the result signal never fires.
        self._fetch_fields_worker: SingleCallWorker | None = None
        # Same GC-safety rationale for the deck-list fetch worker.
        self._fetch_decks_worker: SingleCallWorker | None = None
        # GC-safety for the card-styling write worker (Issue #44).
        self._styling_worker: StylingWorker | None = None
        # A sync requested while a write is in flight (e.g. a dict import landing
        # during a Save-write) is remembered here and re-fired when the write
        # settles, so the latest dict set / toggle still reaches Anki.
        self._resync_pending = False

    def iter_close_workers(self) -> tuple:
        """Live worker handles MainWindow must join on close (T-12).

        The short-lived AnkiConnect workers — fetch fields, fetch decks, and the
        styling write — are each a tab-parented QThread that can sit in a 15-60 s
        blocking request. They have no ``worker_thread`` attribute,
        so closeEvent discovers them (via ``SettingsTab.iter_close_workers``,
        which delegates here) and routes each through the single
        ``BackgroundTaskController._join_worker_for_close`` policy (cancel + bounded grace
        join + laggard deferral). Returning them — rather than waiting here —
        keeps every shutdown join in one place; abandoning them to Qt teardown
        aborts with "QThread: Destroyed while thread is still running".
        """
        return (self._fetch_fields_worker, self._fetch_decks_worker, self._styling_worker)

    def shutdown(self) -> None:
        """Cancel every running AnkiConnect worker (cancel only, no wait).

        Explicit-teardown entry point mirroring the YouTube tab. closeEvent
        does the bounded join via ``BackgroundTaskController._join_worker_for_close``; this
        is the standalone cancel for any non-close caller. ``cancel()`` is
        idempotent, so the helper re-cancelling is harmless.
        """
        for worker in self.iter_close_workers():
            if worker is not None and worker.isRunning():
                worker.cancel()

    # === Fetch fields ===

    def fetch_fields(self) -> None:
        """Fetch the note type's field list from AnkiConnect in a worker thread.

        Reads the current note type and AnkiConnect URL straight from the panel
        inputs (not the saved config) so the user can fetch without first
        hitting Save. The button is disabled for the duration to prevent piling
        up concurrent requests. Results land on the main thread via
        :meth:`_on_fetch_fields_finished`.
        """
        # Don't stack worker threads — first request wins until it completes.
        if self._fetch_fields_worker is not None and self._fetch_fields_worker.isRunning():
            return

        note_type = self._anki_panel.get_note_type().strip()
        if not note_type:
            self._anki_panel.set_notetype_status(False, "Enter a note type name before fetching fields")
            return

        ankiconnect_url = self._anki_panel.get_ankiconnect_url().strip()
        # Patch the live config with the user's in-flight input values so the
        # service hits the URL/note type currently shown in the form, not
        # whatever was last saved to disk.
        config = self._get_config()
        probe_config = replace(
            config,
            anki_note_type=note_type,
            ankiconnect_url=ankiconnect_url or config.ankiconnect_url,
        )

        try:
            service = AnkiService(probe_config)
        except ValueError as e:
            # Misconfigured anki_fields keys — surface, don't crash.
            self._anki_panel.set_notetype_status(False, f"Cannot build AnkiService: {e}")
            return

        self._anki_panel.set_notetype_status(None, "Fetching fields from note type...")
        self._anki_panel.set_fetch_fields_button_enabled(False)

        worker = FetchFieldsWorker(service, note_type, self._parent)
        self._fetch_fields_worker = worker
        worker.result_ready.connect(self._on_fetch_fields_finished)
        worker.error.connect(self._on_fetch_fields_error)
        worker.start()

    def _on_fetch_fields_finished(self, field_names: list[str]) -> None:
        """Populate the panel with the fetched field list (main-thread slot)."""
        self._anki_panel.set_fetch_fields_button_enabled(True)
        if not field_names:
            # Empty list means AnkiConnect rejected the request or returned
            # nothing — most commonly the note type doesn't exist, or Anki
            # isn't running. The status indicator is the existing affordance
            # for note-type problems, so reuse it.
            self._anki_panel.set_notetype_status(
                False, "Could not fetch fields. Is Anki running and the note type spelled right?"
            )
            return
        self._anki_panel.populate_from_field_list(field_names)
        self._anki_panel.set_notetype_status(True, f"Fetched {len(field_names)} fields and auto-mapped them")

    def _on_fetch_fields_error(self, message: str) -> None:
        """Surface an unexpected worker exception via the note-type status line."""
        self._anki_panel.set_fetch_fields_button_enabled(True)
        self._anki_panel.set_notetype_status(False, message)

    # === Card styling (Issue #44 / auto-sync) ===
    #
    # `manage_card_styling` is the desired state; the note type's managed CSS
    # block is the applied state. `sync_styling` reconciles them — apply the
    # universal sheet + scoped dict CSS + custom CSS when managing, strip the
    # block when not. It is called on Save, on dictionary import, and when
    # AnkiConnect becomes reachable; apply/strip are idempotent, so redundant
    # re-syncs are harmless. The note type / URL come straight from the panel
    # inputs (like fetch_fields) so a sync works against whatever is shown.

    def sync_styling(self) -> None:
        """Apply or remove the managed glossary CSS to match `manage_card_styling`."""
        mode: Literal["apply", "remove"] = "apply" if self._get_config().manage_card_styling else "remove"
        self._start_styling_write(mode)

    def _build_styling_service(self, note_type: str) -> AnkiService | None:
        """Build an AnkiService against the panel's note type + URL (None on error)."""
        ankiconnect_url = self._anki_panel.get_ankiconnect_url().strip()
        config = self._get_config()
        probe_config = replace(
            config,
            anki_note_type=note_type,
            ankiconnect_url=ankiconnect_url or config.ankiconnect_url,
        )
        try:
            return AnkiService(probe_config)
        except ValueError as e:
            self._anki_panel.set_styling_status(
                False,
                tr_format(QCoreApplication.translate("AnkiProbeController", "Cannot build AnkiService: %1"), e),
            )
            return None

    def _start_styling_write(self, mode: Literal["apply", "remove"]) -> None:
        """Spawn the write worker: apply the managed block, or strip it for ``remove``.

        If a write is already in flight, remember the request (``_resync_pending``)
        and re-fire it when the current write settles, so a trigger that lands
        mid-write (e.g. a dict import during a Save-write) still takes effect.
        """
        if self._styling_worker is not None and self._styling_worker.isRunning():
            self._resync_pending = True
            return
        note_type = self._anki_panel.get_note_type().strip()
        if not note_type:
            self._anki_panel.set_styling_status(
                False,
                QCoreApplication.translate("AnkiProbeController", "Enter a note type name before styling can sync."),
            )
            return
        service = self._build_styling_service(note_type)
        if service is None:
            return
        self._anki_panel.set_styling_status(
            None, QCoreApplication.translate("AnkiProbeController", "Syncing card styling…")
        )
        worker = StylingWorker(
            service,
            mode=mode,
            config=self._get_config(),
            note_type=note_type,
            parent=self._parent,
        )
        self._styling_worker = worker
        worker.finished_ok.connect(self._on_styling_finished)
        worker.error.connect(self._on_styling_error)
        worker.start()

    def _on_styling_finished(self, _message: str) -> None:
        """A write succeeded: report live, then flush any sync that landed mid-write."""
        self._anki_panel.set_styling_status(True, self._live_status_text())
        self._flush_pending_resync()

    def _on_styling_error(self, message: str) -> None:
        """A write failed (Anki down / note type missing): keep desired, retry later."""
        self._anki_panel.set_styling_status(
            None,
            tr_format(
                QCoreApplication.translate(
                    "AnkiProbeController", "Couldn't reach Anki — card styling will sync when it's back. (%1)"
                ),
                message,
            ),
        )
        self._flush_pending_resync()

    def _flush_pending_resync(self) -> None:
        """Re-fire a sync requested while the just-finished write ran.

        Clears the flag *before* re-firing so a still-unreachable Anki can't
        loop: the re-fired write succeeds or fails once with the flag already
        down. The AnkiConnect-reachable retry covers anything still deferred.
        """
        if self._resync_pending:
            self._resync_pending = False
            self.sync_styling()

    def _live_status_text(self) -> str:
        """Status text describing what is currently live in Anki."""
        if self._get_config().manage_card_styling:
            return QCoreApplication.translate("AnkiProbeController", "Glossary styling is live in Anki.")
        return QCoreApplication.translate("AnkiProbeController", "Off — Anki Miner isn't styling this note type.")

    # === Excluded decks (Issue #38) ===

    def fetch_decks(self) -> None:
        """Fetch the deck list from AnkiConnect to populate the exclude picker.

        Uses the AnkiConnect URL currently shown in the Anki panel (not the
        last-saved config) so the user can pick decks without hitting Save
        first. The picker opens when results arrive via
        :meth:`_on_fetch_decks_finished`.
        """
        if self._fetch_decks_worker is not None and self._fetch_decks_worker.isRunning():
            return

        ankiconnect_url = self._anki_panel.get_ankiconnect_url().strip()
        config = self._get_config()
        probe_config = replace(config, ankiconnect_url=ankiconnect_url or config.ankiconnect_url)

        try:
            service = AnkiService(probe_config)
        except ValueError as e:
            QMessageBox.warning(
                self._parent,
                QCoreApplication.translate("AnkiProbeController", "Add Deck"),
                tr_format(QCoreApplication.translate("AnkiProbeController", "Cannot build AnkiService: %1"), e),
            )
            return

        self._filtering_panel.set_add_deck_button_enabled(False)
        worker = FetchDecksWorker(service, self._parent)
        self._fetch_decks_worker = worker
        worker.result_ready.connect(self._on_fetch_decks_finished)
        worker.error.connect(self._on_fetch_decks_error)
        worker.start()

    def _on_fetch_decks_finished(self, deck_names: list[str]) -> None:
        """Hand the fetched deck list to the panel, which opens the picker."""
        self._filtering_panel.set_add_deck_button_enabled(True)
        if not deck_names:
            QMessageBox.warning(
                self._parent,
                QCoreApplication.translate("AnkiProbeController", "Add Deck"),
                QCoreApplication.translate(
                    "AnkiProbeController", "Could not fetch decks. Is Anki running with AnkiConnect?"
                ),
            )
            return
        self._filtering_panel.set_available_decks(deck_names)

    def _on_fetch_decks_error(self, message: str) -> None:
        """Surface an unexpected deck-fetch worker exception."""
        self._filtering_panel.set_add_deck_button_enabled(True)
        QMessageBox.warning(
            self._parent,
            QCoreApplication.translate("AnkiProbeController", "Add Deck"),
            message,
        )
