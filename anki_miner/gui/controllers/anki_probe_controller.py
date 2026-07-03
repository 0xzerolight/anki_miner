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

from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication
from PyQt6.QtWidgets import QMessageBox, QWidget

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.widgets.panels import AnkiSettingsPanel, FilteringSettingsPanel
from anki_miner.gui.workers.base_worker import SingleCallWorker
from anki_miner.gui.workers.fetch_decks_worker import FetchDecksWorker
from anki_miner.gui.workers.fetch_fields_worker import FetchFieldsWorker
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

    def iter_close_workers(self) -> tuple:
        """Live worker handles MainWindow must join on close (T-12).

        The short-lived AnkiConnect workers — fetch fields and fetch decks — are
        each a tab-parented QThread that can sit in a 15-60 s blocking request.
        They have no ``worker_thread`` attribute, so closeEvent discovers them
        (via ``SettingsTab.iter_close_workers``, which delegates here) and routes
        each through the single ``BackgroundTaskController._join_worker_for_close``
        policy (cancel + bounded grace join + laggard deferral). Returning them —
        rather than waiting here — keeps every shutdown join in one place;
        abandoning them to Qt teardown aborts with "QThread: Destroyed while
        thread is still running".
        """
        return (self._fetch_fields_worker, self._fetch_decks_worker)

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

    @staticmethod
    def _alive(widget: QWidget) -> bool:
        """True unless ``widget``'s underlying C++ object has been destroyed.

        A worker's completion signal is queued cross-thread, so it can be
        delivered *after* the target panel is torn down (a tab closed mid-probe,
        or test teardown freeing the widget tree before the worker emits).
        Touching the dead wrapper then raises ``RuntimeError: wrapped C/C++
        object of type ... has been deleted``. Every worker-completion slot
        guards its target widget with this so a late signal no-ops instead of
        crashing the Qt event loop.

        Non-wrapped objects (e.g. a test ``MagicMock`` panel) aren't sip-tracked,
        so ``isdeleted`` would reject them — treat those as always alive.
        """
        if not isinstance(widget, sip.simplewrapper):
            return True
        return not sip.isdeleted(widget)

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
        if not self._alive(self._anki_panel):
            return
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
        if not self._alive(self._anki_panel):
            return
        self._anki_panel.set_fetch_fields_button_enabled(True)
        self._anki_panel.set_notetype_status(False, message)

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
        if not self._alive(self._filtering_panel):
            return
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
        if not self._alive(self._filtering_panel):
            return
        self._filtering_panel.set_add_deck_button_enabled(True)
        QMessageBox.warning(
            self._parent,
            QCoreApplication.translate("AnkiProbeController", "Add Deck"),
            message,
        )
