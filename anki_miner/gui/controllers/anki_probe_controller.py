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

from PyQt6.QtCore import QCoreApplication
from PyQt6.QtWidgets import QWidget

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.utils.qt_helpers import widget_alive
from anki_miner.gui.utils.run_off_thread import still_running
from anki_miner.gui.widgets.base import ScreenIssue, report_screen_issue
from anki_miner.gui.widgets.panels import AnkiSettingsPanel, FilteringSettingsPanel
from anki_miner.gui.workers.base_worker import SingleCallWorker
from anki_miner.gui.workers.fetch_workers import FetchDecksWorker, FetchFieldsWorker, FetchNotetypesWorker
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
        # Same again for the two name-list workers that fill the deck /
        # note-type dropdowns in the Anki panel.
        self._name_decks_worker: SingleCallWorker | None = None
        self._name_notetypes_worker: SingleCallWorker | None = None

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
        return (
            self._fetch_fields_worker,
            self._fetch_decks_worker,
            self._name_decks_worker,
            self._name_notetypes_worker,
        )

    def shutdown(self) -> None:
        """Cancel every running AnkiConnect worker (cancel only, no wait).

        Explicit-teardown entry point mirroring the YouTube tab. closeEvent
        does the bounded join via ``BackgroundTaskController._join_worker_for_close``; this
        is the standalone cancel for any non-close caller. ``cancel()`` is
        idempotent, so the helper re-cancelling is harmless.
        """
        for worker in self.iter_close_workers():
            if still_running(worker):
                worker.cancel()

    def _release_worker(self, attr: str, worker: SingleCallWorker) -> None:
        """Free a finished probe worker (mirrors ``background_tasks._release_worker``).

        Workers are parented to the settings tab (window lifetime), so without
        this they accumulate as live QObjects across repeated probes. Clear the
        handle only when it still points at *worker* — a fresh probe may have
        already replaced it — and schedule the QThread for deletion.
        """
        if getattr(self, attr, None) is worker:
            setattr(self, attr, None)
        worker.deleteLater()

    @staticmethod
    def _alive(widget: QWidget) -> bool:
        """True unless ``widget``'s underlying C++ object has been destroyed.

        A worker's completion signal is queued cross-thread, so it can be
        delivered *after* the target panel is torn down (a tab closed mid-probe,
        or test teardown freeing the widget tree before the worker emits).
        Every worker-completion slot guards its target widget with this so a
        late signal no-ops instead of crashing the Qt event loop.

        Thin alias for :func:`widget_alive`, kept because every probe slot in
        this class reads better with it.
        """
        return widget_alive(widget)

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
        if still_running(self._fetch_fields_worker):
            return

        note_type = self._anki_panel.get_note_type().strip()
        if not note_type:
            # "Select", not "Enter": the note type is a strict dropdown now.
            self._anki_panel.set_notetype_status(
                False,
                QCoreApplication.translate("AnkiProbeController", "Select a note type before fetching fields"),
            )
            return

        ankiconnect_url = self._anki_panel.get_ankiconnect_url().strip()
        if not ankiconnect_url:
            return
        # Patch the live config with the user's in-flight input values so the
        # service hits the URL/note type currently shown in the form, not
        # whatever was last saved to disk.
        config = self._get_config()
        probe_config = replace(
            config,
            anki_note_type=note_type,
            ankiconnect_url=ankiconnect_url,
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
        worker.finished.connect(lambda w=worker: self._release_worker("_fetch_fields_worker", w))
        worker.result_ready.connect(
            lambda names, stamp=(note_type, ankiconnect_url): self._on_fetch_fields_finished(stamp[0], names, stamp[1])
        )
        worker.error.connect(
            lambda message, stamp=(note_type, ankiconnect_url): self._on_fetch_fields_error(message, stamp[0], stamp[1])
        )
        worker.start()

    def _on_fetch_fields_finished(
        self,
        note_type: str,
        field_names: list[str],
        ankiconnect_url: str | None = None,
    ) -> None:
        """Populate the panel with the fetched field list (main-thread slot)."""
        if not self._alive(self._anki_panel):
            return
        self._anki_panel.set_fetch_fields_button_enabled(True)
        if note_type != self._anki_panel.get_note_type().strip():
            return
        if ankiconnect_url is not None and ankiconnect_url != self._anki_panel.get_ankiconnect_url().strip():
            return
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

    def _on_fetch_fields_error(
        self,
        message: str,
        note_type: str | None = None,
        ankiconnect_url: str | None = None,
    ) -> None:
        """Surface an unexpected worker exception via the note-type status line."""
        if not self._alive(self._anki_panel):
            return
        self._anki_panel.set_fetch_fields_button_enabled(True)
        if note_type is not None and note_type != self._anki_panel.get_note_type().strip():
            return
        if ankiconnect_url is not None and ankiconnect_url != self._anki_panel.get_ankiconnect_url().strip():
            return
        self._anki_panel.set_notetype_status(False, message)

    def _report(self, summary: str, details: str = "") -> None:
        """Report a probe failure on the Settings page that asked for it (D24).

        A modal here interrupted a user who was in the middle of editing the
        very field that would fix it. ``report_screen_issue`` walks up from the
        panel to Settings' own banner, so the failure sits beside the
        AnkiConnect address rather than on top of it.

        A worker's error signal is queued cross-thread, so it can arrive after
        the settings tab (or the panel itself) is torn down — guard both
        before handing either to ``report_screen_issue``, which would
        otherwise walk into a dead C++ widget and raise.
        """
        origin = self._anki_panel if widget_alive(self._anki_panel) else self._parent
        if not widget_alive(origin):
            return
        report_screen_issue(origin, ScreenIssue(summary=summary, details=details))

    # === Excluded decks (Issue #38) ===

    def fetch_decks(self) -> None:
        """Fetch the deck list from AnkiConnect to populate the exclude picker.

        Uses the AnkiConnect URL currently shown in the Anki panel (not the
        last-saved config) so the user can pick decks without hitting Save
        first. The picker opens when results arrive via
        :meth:`_on_fetch_decks_finished`.
        """
        if still_running(self._fetch_decks_worker):
            return

        ankiconnect_url = self._anki_panel.get_ankiconnect_url().strip()
        if not ankiconnect_url:
            return
        config = self._get_config()
        probe_config = replace(config, ankiconnect_url=ankiconnect_url)

        try:
            service = AnkiService(probe_config)
        except ValueError as e:
            self._report(
                QCoreApplication.translate(
                    "AnkiProbeController",
                    "The deck list could not be requested. Check the AnkiConnect address in Settings.",
                ),
                str(e),
            )
            return

        self._filtering_panel.set_add_deck_button_enabled(False)
        worker = FetchDecksWorker(service, self._parent)
        self._fetch_decks_worker = worker
        worker.finished.connect(lambda w=worker: self._release_worker("_fetch_decks_worker", w))
        worker.result_ready.connect(
            lambda names, endpoint=ankiconnect_url: self._on_fetch_decks_finished(names, endpoint)
        )
        worker.error.connect(lambda message, endpoint=ankiconnect_url: self._on_fetch_decks_error(message, endpoint))
        worker.start()

    def _on_fetch_decks_finished(self, deck_names: list[str], ankiconnect_url: str | None = None) -> None:
        """Hand the fetched deck list to the panel, which opens the picker."""
        if not self._alive(self._filtering_panel):
            return
        self._filtering_panel.set_add_deck_button_enabled(True)
        if ankiconnect_url is not None and ankiconnect_url != self._anki_panel.get_ankiconnect_url().strip():
            return
        if not deck_names:
            self._report(
                QCoreApplication.translate(
                    "AnkiProbeController",
                    "No decks came back. Check that Anki is running with the AnkiConnect add-on.",
                )
            )
            return
        self._filtering_panel.set_available_decks(deck_names)

    def _on_fetch_decks_error(self, message: str, ankiconnect_url: str | None = None) -> None:
        """Surface an unexpected deck-fetch worker exception."""
        if not self._alive(self._filtering_panel):
            return
        self._filtering_panel.set_add_deck_button_enabled(True)
        if ankiconnect_url is not None and ankiconnect_url != self._anki_panel.get_ankiconnect_url().strip():
            return
        self._report(
            QCoreApplication.translate(
                "AnkiProbeController",
                "The deck list could not be read from Anki.",
            ),
            message,
        )

    # === Deck / note-type dropdown lists ===

    def refresh_name_lists(self) -> None:
        """Fill the Anki panel's deck and note-type dropdowns from AnkiConnect.

        Separate from :meth:`fetch_decks`, which serves the excluded-decks
        picker and opens a dialog with its result. Both lists are fetched
        concurrently — independent AnkiConnect calls, and the user waits on
        both. Reads the URL currently shown in the panel so a probe works
        before Save.

        Every user-visible string here goes through ``QCoreApplication.translate``
        with a LITERAL context: pylupdate6 cannot resolve a non-literal context
        (e.g. a ``self._TR`` attribute) and drops the string silently, exactly
        as it does for an f-string.
        """
        ankiconnect_url = self._anki_panel.get_ankiconnect_url().strip()
        if not ankiconnect_url:
            return
        config = self._get_config()
        probe_config = replace(config, ankiconnect_url=ankiconnect_url)

        try:
            service = AnkiService(probe_config)
        except ValueError as e:
            message = tr_format(QCoreApplication.translate("AnkiProbeController", "Cannot build AnkiService: %1"), e)
            self._anki_panel.set_deck_status(False, message)
            self._set_notetype_status(False, message)
            return

        if not still_running(self._name_decks_worker):
            self._anki_panel.set_deck_status(
                None, QCoreApplication.translate("AnkiProbeController", "Loading decks from Anki…")
            )
            decks_worker = FetchDecksWorker(service, self._parent)
            self._name_decks_worker = decks_worker
            decks_worker.finished.connect(lambda w=decks_worker: self._release_worker("_name_decks_worker", w))
            decks_worker.result_ready.connect(
                lambda names, endpoint=ankiconnect_url: self._on_name_decks_fetched(names, endpoint)
            )
            decks_worker.error.connect(
                lambda message, endpoint=ankiconnect_url: self._on_name_decks_error(message, endpoint)
            )
            decks_worker.start()

        if not still_running(self._name_notetypes_worker):
            self._set_notetype_status(
                None, QCoreApplication.translate("AnkiProbeController", "Loading note types from Anki…")
            )
            types_worker = FetchNotetypesWorker(service, self._parent)
            self._name_notetypes_worker = types_worker
            types_worker.finished.connect(lambda w=types_worker: self._release_worker("_name_notetypes_worker", w))
            types_worker.result_ready.connect(
                lambda names, endpoint=ankiconnect_url: self._on_name_notetypes_fetched(names, endpoint)
            )
            types_worker.error.connect(
                lambda message, endpoint=ankiconnect_url: self._on_name_notetypes_error(message, endpoint)
            )
            types_worker.start()

    def _set_notetype_status(self, exists: bool | None, message: str) -> None:
        """Write the note-type status unless the Auto-Map flow is mid-flight.

        Both flows share one label, and refresh_name_lists() fires
        automatically on first show. This yield alone is NOT sufficient — it
        only covers the window while Auto-Map is still running. The other
        ordering (Auto-Map finishes first, then the list lands) is handled by
        the list flow staying silent on success; see
        :meth:`_on_name_notetypes_fetched`. Together they mean an actionable
        message always wins and the low-value count never overwrites a
        terminal Auto-Map result.
        """
        if still_running(self._fetch_fields_worker):
            return
        self._anki_panel.set_notetype_status(exists, message)

    def _on_name_decks_fetched(self, deck_names: object, ankiconnect_url: str | None = None) -> None:
        if not self._alive(self._anki_panel):
            return
        if ankiconnect_url is not None and ankiconnect_url != self._anki_panel.get_ankiconnect_url().strip():
            return
        names = [str(n) for n in deck_names] if isinstance(deck_names, list) else []
        if not names:
            self._anki_panel.set_deck_status(
                False,
                QCoreApplication.translate(
                    "AnkiProbeController", "Could not load decks. Is Anki running with AnkiConnect?"
                ),
            )
            return
        self._anki_panel.set_available_decks(names)
        selected = self._anki_panel.get_deck_name()
        if selected in names:
            # %n numerus, not %1: "1 decks loaded" is ungrammatical in every
            # plural-rule language the app ships.
            self._anki_panel.set_deck_status(
                True,
                QCoreApplication.translate("AnkiProbeController", "%n deck(s) loaded", "", len(names)),
            )
        else:
            # NOT set_deck_status(True, ...): that renders a green success
            # badge for a config guaranteed to fail the next mine.
            self._anki_panel.set_deck_status(
                False,
                tr_format(
                    QCoreApplication.translate("AnkiProbeController", "Deck '%1' is not in Anki — pick one below."),
                    selected,
                ),
            )

    def _on_name_decks_error(self, message: str, ankiconnect_url: str | None = None) -> None:
        if not self._alive(self._anki_panel):
            return
        if ankiconnect_url is not None and ankiconnect_url != self._anki_panel.get_ankiconnect_url().strip():
            return
        self._anki_panel.set_deck_status(False, message)

    def _on_name_notetypes_fetched(self, model_names: object, ankiconnect_url: str | None = None) -> None:
        if not self._alive(self._anki_panel):
            return
        if ankiconnect_url is not None and ankiconnect_url != self._anki_panel.get_ankiconnect_url().strip():
            return
        names = [str(n) for n in model_names] if isinstance(model_names, list) else []
        if not names:
            self._set_notetype_status(
                False,
                QCoreApplication.translate(
                    "AnkiProbeController", "Could not load note types. Is Anki running with AnkiConnect?"
                ),
            )
            return
        self._anki_panel.set_available_note_types(names)
        selected = self._anki_panel.get_note_type()
        if selected not in names:
            self._set_notetype_status(
                False,
                tr_format(
                    QCoreApplication.translate(
                        "AnkiProbeController", "Note type '%1' is not in Anki — pick one below."
                    ),
                    selected,
                ),
            )
        # Deliberately silent on success: this label is shared with Auto-Map
        # Fields, whose terminal message is worth more than a count.

    def _on_name_notetypes_error(self, message: str, ankiconnect_url: str | None = None) -> None:
        if not self._alive(self._anki_panel):
            return
        if ankiconnect_url is not None and ankiconnect_url != self._anki_panel.get_ankiconnect_url().strip():
            return
        self._set_notetype_status(False, message)
