"""Wizard pages for the guided first-run Setup Wizard (Task 3).

Six ``QWizardPage`` subclasses. Each takes the parent :class:`SetupWizard` so
it can read/write the working config and use the wizard's shared
:class:`AnkiService` / :class:`ValidationService` and worker registry.

Detect & guide ONLY — no ``createDeck`` / ``createModel`` / ``ensure_deck``
calls anywhere. Deck/note type creation is the user's job; the wizard inspects,
explains, links, and re-checks.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from functools import partial
from typing import TYPE_CHECKING

from PyQt6.QtCore import QT_TRANSLATE_NOOP, QCoreApplication, Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
    QWizardPage,
)

from anki_miner.gui.resources.styles.theme import Theme
from anki_miner.gui.utils.run_off_thread import still_running
from anki_miner.gui.widgets.base import StatusBadge
from anki_miner.gui.widgets.enhanced import ModernButton, ThemeGalleryWidget
from anki_miner.gui.widgets.panels.anki_settings_panel import _FIELD_KEYWORDS, auto_map_fields
from anki_miner.gui.workers.base_worker import SingleCallWorker
from anki_miner.gui.workers.fetch_workers import (
    FetchDecksWorker,
    FetchFieldsWorker,
    FetchNotetypesWorker,
)
from anki_miner.services.anki_note_builder import configured_target_field_names
from anki_miner.services.note_presets import NotePreset, preset_for_field_names
from anki_miner.utils.i18n import tr_format

if TYPE_CHECKING:
    from anki_miner.config import AnkiMinerConfig
    from anki_miner.gui.widgets.dialogs.resource_download_dialog import ResourceDownloadSession
    from anki_miner.services.resource_catalog import ResourceSpec
    from anki_miner.services.validation_service import ValidationService

    from .setup_wizard import SetupWizard

# AnkiConnect is an Anki add-on; this is its add-on code on AnkiWeb.
ANKICONNECT_ADDON_CODE = "2055492159"
ANKICONNECT_URL = "https://ankiweb.net/shared/info/2055492159"
# Recommended Japanese-mining note type guidance (Lapis is the default note type).
NOTE_TYPE_HELP_URL = "https://github.com/0xzerolight/anki_miner#recommended-note-type"

# Moved from welcome_dialog.WELCOME_BLURB (that dialog is retired).
RESOURCES_BLURB = QT_TRANSLATE_NOOP(
    "SetupWizard",
    "Download the recommended frequency list, pitch accent data, and dictionary now?",
)
RESOURCES_HELP_URL = "https://github.com/0xzerolight/anki_miner#recommended-resources"

#: Family noun per catalog ``kind``, so a checkbox says what a resource *is*
#: rather than only what it is called. Keyed by ``ResourceSpec.kind``; a kind
#: with no entry here falls back to the display name alone.
_RESOURCE_KIND_NOUNS = {
    "dict": QT_TRANSLATE_NOOP("SetupWizard", "Dictionary"),
    "freq": QT_TRANSLATE_NOOP("SetupWizard", "Frequency"),
    "pitch": QT_TRANSLATE_NOOP("SetupWizard", "Pitch accent"),
}

#: Eight themes spanning light/dark and warm/cool, shown before the full set.
#: A shortlist keeps the first page of onboarding a glance rather than a wall;
#: "See all" is one click away and states the real count.
WIZARD_SHORTLIST_THEMES = (
    "dark",
    "light",
    "catppuccin-mocha",
    "nord",
    "tokyo-night",
    "everforest-light",
    "rose-pine-dawn",
    "gruvbox-dark-medium",
)


def _open_url(url: str) -> None:
    QDesktopServices.openUrl(QUrl(url))


class _LiveCheckPage(QWizardPage):
    """A page that re-checks one fact about the world, off the GUI thread.

    Every step re-checks live rather than trusting a result cached when the page
    was built (D26), and each such page runs at most one check at a time. Two
    rules make that safe; both exist because the obvious spellings are wrong.

    * **Connect bound methods, never closures.** PyQt drops a connection whose
      receiver ``QObject`` has been destroyed, but a lambda or nested function
      has no receiver to watch — so its queued result is still delivered, into a
      page whose C++ object is mid-destruction. That is a segfault, not a
      ``RuntimeError``, and no amount of guarding inside the slot prevents it.
    * **The retained worker is the generation counter.** Starting a new check
      replaces ``_live_check``, so a late answer from any earlier worker no
      longer matches and is dropped. A separate integer counter would be a
      second thing to keep in step with the first.
    """

    def __init__(self, wizard: SetupWizard) -> None:
        super().__init__(wizard)
        self._wizard = wizard
        self._live_check: SingleCallWorker | None = None

    def _start_live_check(
        self,
        work: Callable[[], object],
        *,
        error_prefix: str,
        on_result: Callable[[object], None],
        on_error: Callable[[str], None],
    ) -> SingleCallWorker:
        """Run ``work`` off-thread; hand ownership to the wizard's close barrier."""
        worker = SingleCallWorker(work, error_prefix=error_prefix, parent=self)
        self._live_check = worker
        self._wizard.register_worker(worker)
        worker.result_ready.connect(on_result)
        worker.error.connect(on_error)
        worker.start()
        return worker

    def _is_live_check(self) -> bool:
        """True when the emitting worker is still the check being waited on."""
        return self.sender() is self._live_check


class ThemePage(QWizardPage):
    """Step 1: pick a look.

    Deliberately first and deliberately non-blocking. It is the one step with no
    external dependency -- nothing to detect, nothing that can fail -- so it
    costs the user nothing, and every page after it wears their own pick.

    Stars are off here: favorites are a curation tool for someone who already
    has opinions, and asking a new user to manage a top-right selector they have
    not seen yet is noise. They are one click away in Settings afterwards.
    """

    def __init__(self, wizard: SetupWizard) -> None:
        super().__init__(wizard)
        self._wizard = wizard
        # ThemeGalleryWidget.refresh() seeds selected_key() from the app-wide
        # Theme.get_current_mode() so the matching card reads "Active" on
        # first paint -- that is a display default, not a user decision, and
        # it need not agree with the working config's own theme (e.g. before
        # anything has synced the two). Only an actual click may write the
        # config, so a page nobody touched stays inert.
        self._touched = False

        self.setTitle(self.tr("Pick a Look"))
        self.setSubTitle(self.tr("Click a theme to try it. You can change it any time in Settings."))

        layout = QVBoxLayout(self)

        self.gallery = ThemeGalleryWidget(self, show_stars=False)
        self.gallery.set_shortlist(WIZARD_SHORTLIST_THEMES)
        self.gallery.theme_activated.connect(self._on_theme_activated)
        layout.addWidget(self.gallery, 1)

        row = QHBoxLayout()
        self.see_all_btn = ModernButton(
            tr_format(self.tr("See all %1 themes…"), len(Theme.get_available_themes())),
            variant="secondary",
        )
        self.see_all_btn.clicked.connect(self._on_see_all_clicked)
        row.addWidget(self.see_all_btn)
        row.addStretch(1)
        layout.addLayout(row)

    def isComplete(self) -> bool:
        # Always true. There is no wrong answer and no state to gather, so this
        # step must never be able to hold the wizard up.
        return True

    def _on_see_all_clicked(self) -> None:
        """Expand to the full grouped set, in place.

        In place, not a dialog: a modal opened on top of a wizard page is a
        second navigation stack over the first, and Escape then means two
        different things depending on what has focus.
        """
        self.gallery.show_all_themes()
        self.see_all_btn.setVisible(False)

    def _on_theme_activated(self, key: str) -> None:
        """Apply live so the wizard itself reskins -- that IS the preview."""
        self._touched = True
        Theme.set_mode(key)
        app = QApplication.instance()
        if isinstance(app, QApplication):
            Theme.apply_to_app(app, key)

    def _write_theme_to_config(self) -> None:
        if not self._touched:
            return
        key = self.gallery.selected_key()
        if key and key != self._wizard.working_config().theme:
            self._wizard.update_working_config(replace(self._wizard.working_config(), theme=key))

    def stage_current_edits(self) -> None:
        """Stage the current pick without navigating."""
        self._write_theme_to_config()

    def validatePage(self) -> bool:
        self._write_theme_to_config()
        return True


class AnkiConnectPage(QWizardPage):
    """Step 1: verify AnkiConnect is reachable; guide install if not."""

    def __init__(self, wizard: SetupWizard) -> None:
        super().__init__(wizard)
        self._wizard = wizard
        self._reachable = False
        self._worker: SingleCallWorker | None = None
        self._active_recheck_url: str | None = None

        self.setTitle(self.tr("Connect to Anki"))
        self.setSubTitle(self.tr("Anki Miner talks to Anki through the AnkiConnect add-on."))

        layout = QVBoxLayout(self)

        self.badge = StatusBadge("AnkiConnect", status="checking", clickable=False)
        layout.addWidget(self.badge)

        guidance = QLabel(
            tr_format(
                self.tr("In Anki: Tools → Add-ons → Get Add-ons…, paste the code <b>%1</b>, then restart Anki."),
                ANKICONNECT_ADDON_CODE,
            )
        )
        guidance.setWordWrap(True)
        layout.addWidget(guidance)

        link = QLabel(f'<a href="{ANKICONNECT_URL}">{self.tr("Open the AnkiConnect add-on page")}</a>')
        link.setOpenExternalLinks(False)
        link.linkActivated.connect(lambda: _open_url(ANKICONNECT_URL))
        layout.addWidget(link)

        url_row = QHBoxLayout()
        url_row.addWidget(QLabel(self.tr("AnkiConnect URL:")))
        self.url_input = QLineEdit(wizard.working_config().ankiconnect_url)
        self.url_input.setPlaceholderText("http://127.0.0.1:8765")
        url_row.addWidget(self.url_input, 1)
        layout.addLayout(url_row)

        self.recheck_button = ModernButton(self.tr("Recheck"), variant="secondary")
        self.recheck_button.clicked.connect(self._on_recheck_clicked)
        layout.addWidget(self.recheck_button)

        self.result_label = QLabel("")
        self.result_label.setWordWrap(True)
        layout.addWidget(self.result_label)
        self.url_input.textChanged.connect(self._on_url_changed)

    def initializePage(self) -> None:
        """Fire one auto recheck so the happy path is zero clicks."""
        self.url_input.setText(self._wizard.working_config().ankiconnect_url)
        self._on_recheck_clicked()

    def isComplete(self) -> bool:
        return self._reachable

    def _on_url_changed(self, _text: str) -> None:
        self._reachable = False
        self.badge.set_status("pending")
        self.badge.setToolTip("")
        self.result_label.clear()
        self.completeChanged.emit()

    def _normalized_url(self) -> str:
        return self.url_input.text().strip()

    def _write_url_to_config(self) -> None:
        """Stage the URL field into the working config."""
        url = self._normalized_url()
        if url != self._wizard.working_config().ankiconnect_url:
            self._wizard.update_working_config(replace(self._wizard.working_config(), ankiconnect_url=url))

    def stage_current_edits(self) -> None:
        """Stage editor state without starting an AnkiConnect check."""
        self._write_url_to_config()

    def _recheck_work(self) -> tuple[bool, str]:
        """Blocking AnkiConnect check (runs off the GUI thread)."""
        # The URL is staged into the working config by _write_url_to_config() on the
        # main thread before this worker starts, so the wizard's validation_service()
        # (bound to that working config) reads the staged URL rather than touching the
        # QLineEdit off-thread.
        return self._wizard.validation_service().check_ankiconnect()

    def _on_recheck_clicked(self) -> None:
        if still_running(self._worker):
            return
        self._write_url_to_config()
        url = self._normalized_url()
        if not url:
            self._reachable = False
            self.badge.set_status("error", self.tr("Enter an AnkiConnect URL."))
            self.result_label.setText(self.tr("Enter an AnkiConnect URL."))
            self.completeChanged.emit()
            return
        self._active_recheck_url = url
        self.badge.set_status("checking", self.tr("Checking connection..."))
        self.result_label.setText(self.tr("Checking connection..."))
        self.recheck_button.setEnabled(False)

        worker = SingleCallWorker(self._recheck_work, error_prefix="", parent=self)
        self._worker = worker
        self._wizard.register_worker(worker)
        worker.result_ready.connect(self._on_recheck_result)
        worker.error.connect(self._on_recheck_error)
        worker.start()

    def _on_recheck_result(self, result: object) -> None:
        """Main-thread slot: update the badge + reachability from the check result."""
        ok, message = result if isinstance(result, tuple) else (False, str(result))
        self.recheck_button.setEnabled(True)
        if self._active_recheck_url is not None and self._active_recheck_url != self._normalized_url():
            return
        self._reachable = bool(ok)
        self.badge.set_status("success" if ok else "error", message)
        self.result_label.setText(message)
        self.completeChanged.emit()

    def _on_recheck_error(self, message: str) -> None:
        self.recheck_button.setEnabled(True)
        if self._active_recheck_url is not None and self._active_recheck_url != self._normalized_url():
            return
        self._reachable = False
        self.badge.set_status("error", message)
        self.result_label.setText(message)
        self.completeChanged.emit()


class DeckPage(QWizardPage):
    """Step 2: choose the target deck (must already exist in Anki)."""

    def __init__(self, wizard: SetupWizard) -> None:
        super().__init__(wizard)
        self._wizard = wizard
        self._worker: SingleCallWorker | None = None
        self._fetched_decks: list[str] = []

        self.setTitle(self.tr("Choose a Deck"))
        self.setSubTitle(self.tr("Mined cards go into this deck."))

        layout = QVBoxLayout(self)

        row = QHBoxLayout()
        self.deck_combo = QComboBox()
        self.deck_combo.setEditable(True)
        self.deck_combo.currentTextChanged.connect(self._on_text_changed)
        row.addWidget(self.deck_combo, 1)
        self.refresh_button = ModernButton(self.tr("Refresh"), variant="secondary")
        self.refresh_button.clicked.connect(self._on_refresh_clicked)
        row.addWidget(self.refresh_button)
        layout.addLayout(row)

        self.deck_hint = QLabel("")
        self.deck_hint.setObjectName("helper-text")
        self.deck_hint.setWordWrap(True)
        layout.addWidget(self.deck_hint)

    def initializePage(self) -> None:
        self.deck_combo.setCurrentText(self._wizard.working_config().anki_deck_name)
        self._on_refresh_clicked()

    def isComplete(self) -> bool:
        # Decks are no longer auto-created at mine time, so only a deck Anki
        # actually reports can be accepted here. Every path that mutates
        # _fetched_decks must emit completeChanged or Next stays disabled.
        name = self.deck_combo.currentText().strip()
        return bool(name) and name in self._fetched_decks

    def _on_text_changed(self, _text: str) -> None:
        self._update_deck_hint()
        self.completeChanged.emit()

    def _write_deck_to_config(self) -> None:
        name = self.deck_combo.currentText().strip()
        if name and name != self._wizard.working_config().anki_deck_name:
            self._wizard.update_working_config(replace(self._wizard.working_config(), anki_deck_name=name))

    def stage_current_edits(self) -> None:
        """Stage editor state without fetching decks."""
        self._write_deck_to_config()

    def validatePage(self) -> bool:
        self._write_deck_to_config()
        return True

    def _on_refresh_clicked(self) -> None:
        if still_running(self._worker):
            return
        self.refresh_button.setEnabled(False)
        worker = FetchDecksWorker(self._wizard.anki_service(), self)
        self._worker = worker
        self._wizard.register_worker(worker)
        worker.result_ready.connect(self._on_decks_fetched)
        worker.error.connect(self._on_decks_error)
        # isComplete() now depends on _fetched_decks, and QWizard only
        # re-queries it on completeChanged — every path that touches that list
        # must emit or Next freezes. Mirrors NoteTypePage.
        self.completeChanged.emit()
        worker.start()

    def _on_decks_error(self, _message: str) -> None:
        self.refresh_button.setEnabled(True)
        self.completeChanged.emit()

    def _on_decks_fetched(self, deck_names: object) -> None:
        self.refresh_button.setEnabled(True)
        names = list(deck_names) if isinstance(deck_names, list) else []
        self._fetched_decks = names
        current = self.deck_combo.currentText()
        self.deck_combo.blockSignals(True)
        self.deck_combo.clear()
        self.deck_combo.addItems(names)
        self.deck_combo.setCurrentText(current or self._wizard.working_config().anki_deck_name)
        self.deck_combo.blockSignals(False)
        self._update_deck_hint()
        # The repopulate above runs with signals blocked, so _on_text_changed —
        # the only other emitter — never fires. Without this the Next button is
        # never re-evaluated after the list lands and stays disabled forever.
        self.completeChanged.emit()

    def _update_deck_hint(self) -> None:
        name = self.deck_combo.currentText().strip()
        if not self._fetched_decks:
            self.deck_hint.setText(self.tr("Could not load decks. Is Anki running with AnkiConnect?"))
        elif not name:
            self.deck_hint.setText(self.tr("Pick a deck."))
        elif name not in self._fetched_decks:
            self.deck_hint.setText(self.tr("No such deck. Create it in Anki, then press Refresh."))
        else:
            self.deck_hint.setText("")


class NoteTypePage(_LiveCheckPage):
    """Step 3 (richest): choose a note type, auto-map its fields, warn on gaps."""

    def __init__(self, wizard: SetupWizard) -> None:
        super().__init__(wizard)
        self._notetypes_worker: SingleCallWorker | None = None
        self._fields_worker: SingleCallWorker | None = None
        # The field-name warning check; ``_LiveCheckPage`` owns its staleness.
        self._warn_worker: SingleCallWorker | None = None
        self._fields_generation = 0
        self._desired_note_type = ""
        self._active_fields_request: tuple[int, str] | None = None
        self._accept_field_fetches = True
        self._fetched_note_types: list[str] = []
        self._field_names: list[str] = []
        self._field_names_note_type: str | None = None

        self.setTitle(self.tr("Choose a Note Type"))
        self.setSubTitle(self.tr("Pick the Anki note type whose fields will hold mined data."))

        layout = QVBoxLayout(self)

        row = QHBoxLayout()
        self.notetype_combo = QComboBox()
        self.notetype_combo.setEditable(True)
        self.notetype_combo.currentTextChanged.connect(self._on_notetype_changed)
        row.addWidget(self.notetype_combo, 1)
        self.refresh_button = ModernButton(self.tr("Refresh"), variant="secondary")
        self.refresh_button.clicked.connect(self._on_refresh_clicked)
        row.addWidget(self.refresh_button)
        layout.addLayout(row)

        self.guidance_label = QLabel("")
        self.guidance_label.setWordWrap(True)
        self.guidance_label.setOpenExternalLinks(False)
        self.guidance_label.linkActivated.connect(self._on_guidance_link_activated)
        self.guidance_label.setVisible(False)
        layout.addWidget(self.guidance_label)

        self.auto_map_button = ModernButton(self.tr("Auto-Map Fields from Note Type"), variant="primary")
        self.auto_map_button.clicked.connect(self._on_auto_map_clicked)
        self.auto_map_button.setEnabled(False)
        layout.addWidget(self.auto_map_button)

        self.mapping_summary = QLabel("")
        self.mapping_summary.setObjectName("helper-text")
        self.mapping_summary.setWordWrap(True)
        self.mapping_summary.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.mapping_summary)

        self.warning_label = QLabel("")
        self.warning_label.setObjectName("validation-status")
        self.warning_label.setWordWrap(True)
        layout.addWidget(self.warning_label)
        wizard.finished.connect(self._on_wizard_finished)

    def initializePage(self) -> None:
        self.notetype_combo.setCurrentText(self._wizard.working_config().anki_note_type)
        self._on_refresh_clicked()

    def isComplete(self) -> bool:
        note_type = self.notetype_combo.currentText().strip()
        config = self._wizard.working_config()
        if (
            not note_type
            or note_type not in self._fetched_note_types
            or self._field_names_note_type != note_type
            or config.anki_note_type != note_type
        ):
            return False
        actual_fields = set(self._field_names)
        word_field = config.anki_fields.get("word", "")
        return (
            bool(word_field)
            and word_field in actual_fields
            and not (configured_target_field_names(config) - actual_fields)
        )

    def _on_notetype_changed(self, text: str) -> None:
        self._record_desired_note_type(text.strip())
        self._fetch_fields()

    def _record_desired_note_type(self, note_type: str) -> None:
        self._fields_generation += 1
        self._desired_note_type = note_type
        self._field_names = []
        self._field_names_note_type = None
        self.auto_map_button.setEnabled(False)
        self._write_notetype_to_config()
        self.completeChanged.emit()

    def validatePage(self) -> bool:
        self._write_notetype_to_config()
        return True

    def _write_notetype_to_config(self) -> None:
        name = self.notetype_combo.currentText().strip()
        if name and name != self._wizard.working_config().anki_note_type:
            self._wizard.update_working_config(replace(self._wizard.working_config(), anki_note_type=name))

    def stage_current_edits(self) -> None:
        """Stage editor state without fetching note-type fields."""
        self._write_notetype_to_config()

    # --- note-type list fetch ---

    def _on_refresh_clicked(self) -> None:
        if still_running(self._notetypes_worker):
            return
        self.refresh_button.setEnabled(False)
        worker = FetchNotetypesWorker(self._wizard.anki_service(), self)
        self._notetypes_worker = worker
        self._wizard.register_worker(worker)
        worker.result_ready.connect(self._on_notetypes_fetched)
        worker.error.connect(self._on_notetypes_error)
        self.completeChanged.emit()
        worker.start()

    def _on_notetypes_fetched(self, model_names: object) -> None:
        self.refresh_button.setEnabled(True)
        names = list(model_names) if isinstance(model_names, list) else []
        self._fetched_note_types = names
        current = self.notetype_combo.currentText()
        self.notetype_combo.blockSignals(True)
        self.notetype_combo.clear()
        self.notetype_combo.addItems(names)
        self.notetype_combo.setCurrentText(current or self._wizard.working_config().anki_note_type)
        self.notetype_combo.blockSignals(False)
        self.completeChanged.emit()
        # Auto-fetch the fields for the selected note type so Auto-Map lights up.
        self._fetch_fields()

    def _on_notetypes_error(self, _message: str) -> None:
        self.refresh_button.setEnabled(True)
        self.completeChanged.emit()

    # --- field list fetch ---

    def _fetch_fields(self) -> None:
        if not self._accept_field_fetches:
            return
        note_type = self.notetype_combo.currentText().strip()
        if note_type != self._desired_note_type:
            self._record_desired_note_type(note_type)
        if not note_type or note_type not in self._fetched_note_types:
            return
        if self._active_fields_request is not None:
            return
        self._write_notetype_to_config()
        generation = self._fields_generation
        worker = FetchFieldsWorker(self._wizard.anki_service(), note_type, self)
        self._fields_worker = worker
        self._active_fields_request = (generation, note_type)
        self._wizard.register_worker(worker)
        worker.result_ready.connect(partial(self._on_fields_fetch_result, generation, note_type))
        worker.error.connect(partial(self._on_fields_fetch_error, generation, note_type))
        worker.finished.connect(partial(self._on_fields_fetch_finished, worker, generation, note_type))
        self.completeChanged.emit()
        worker.start()

    def _on_fields_fetch_result(self, generation: int, note_type: str, field_names: object) -> None:
        if self._active_fields_request != (generation, note_type):
            return
        if generation != self._fields_generation or note_type != self._desired_note_type:
            self.completeChanged.emit()
            return
        self._on_fields_fetched(note_type, field_names)

    def _on_fields_fetch_error(self, generation: int, note_type: str, _message: str) -> None:
        if self._active_fields_request != (generation, note_type):
            return
        if generation == self._fields_generation and note_type == self._desired_note_type:
            self._field_names = []
            self._field_names_note_type = None
            self.auto_map_button.setEnabled(False)
        self.completeChanged.emit()

    def _on_fields_fetch_finished(
        self,
        worker: SingleCallWorker,
        generation: int,
        note_type: str,
    ) -> None:
        if self._fields_worker is not worker or self._active_fields_request != (generation, note_type):
            return
        self._fields_worker = None
        self._active_fields_request = None
        if generation != self._fields_generation or note_type != self._desired_note_type:
            self._fetch_fields()

    def prepare_for_close(self) -> None:
        if not self._accept_field_fetches:
            return
        self._accept_field_fetches = False
        self._fields_generation += 1
        self._desired_note_type = ""

    def _on_wizard_finished(self, _result: int) -> None:
        self.prepare_for_close()

    def _on_fields_fetched(self, note_type: str, field_names: object) -> None:
        try:
            current_note_type = self.notetype_combo.currentText().strip()
        except RuntimeError:
            return
        if note_type != current_note_type:
            return
        names = list(field_names) if isinstance(field_names, list) else []
        self._field_names = names
        self._field_names_note_type = note_type
        if not names:
            self.auto_map_button.setEnabled(False)
            self._show_guidance(
                self.tr(
                    "No fields found. Make sure Anki is running and the note type name is spelled exactly as in Anki."
                )
            )
            self.completeChanged.emit()
            return
        self._sanitize_field_mappings(note_type, names)
        self.auto_map_button.setEnabled(True)
        # A note type we can name maps itself: the preset carries the exact
        # field names plus the pitch/marker settings the keyword pass below
        # cannot know about, so there is nothing left for the user to press.
        preset = preset_for_field_names(names)
        if preset is not None:
            self.guidance_label.setVisible(False)
            self.guidance_label.setText("")
            self._apply_preset(preset)
            return
        if not self._has_mining_shape(names):
            guidance = tr_format(
                self.tr(
                    "This note type does not look set up for Japanese mining (no obvious word/"
                    "sentence fields). Import a recommended mining note type in Anki, then "
                    '<a href="%1">recheck</a>. See: <a href="%1">recommended note type</a>.'
                ),
                NOTE_TYPE_HELP_URL,
            )
            self._show_guidance(
                guidance.replace(
                    f'href="{NOTE_TYPE_HELP_URL}"',
                    'href="recheck"',
                    1,
                )
            )
        else:
            self.guidance_label.setVisible(False)
            self.guidance_label.setText("")
        self.completeChanged.emit()

    @staticmethod
    def _has_mining_shape(field_names: list[str]) -> bool:
        """True if the field list has both a word-ish and a sentence-ish field.

        Normalizes each name the same way :func:`auto_map_fields` does, then
        checks for ANY match against the word and sentence keyword sets.
        """
        word_kw = {kw.lower() for kw in _FIELD_KEYWORDS["word"]}
        sentence_kw = {kw.lower() for kw in _FIELD_KEYWORDS["sentence"]}
        normalized = {name.lower().replace(" ", "").replace("_", "") for name in field_names}
        return bool(normalized & word_kw) and bool(normalized & sentence_kw)

    def _show_guidance(self, html: str) -> None:
        self.guidance_label.setText(html)
        self.guidance_label.setVisible(True)

    def _on_guidance_link_activated(self, url: str) -> None:
        if url == "recheck":
            self._on_refresh_clicked()
        elif url == NOTE_TYPE_HELP_URL:
            _open_url(NOTE_TYPE_HELP_URL)

    def _sanitize_field_mappings(self, note_type: str, field_names: list[str]) -> None:
        config = self._wizard.working_config()
        actual_fields = set(field_names)
        sanitized_fields = dict.fromkeys(_FIELD_KEYWORDS, "")
        sanitized_fields.update(
            {key: value if not value or value in actual_fields else "" for key, value in config.anki_fields.items()}
        )
        sanitized_markers = dict(config.card_type_marker_fields)
        if config.card_type:
            marker_field = sanitized_markers.get(config.card_type, "")
            if marker_field and marker_field not in actual_fields:
                sanitized_markers[config.card_type] = ""
        sanitized_config = replace(
            config,
            anki_note_type=note_type,
            anki_fields=sanitized_fields,
            card_type_marker_fields=sanitized_markers,
        )
        if sanitized_config != config:
            self._wizard.update_working_config(sanitized_config)
            self.completeChanged.emit()

    # --- auto-map ---

    def _apply_preset(self, preset: NotePreset) -> None:
        """Stage ``preset``'s whole answer onto the wizard's working config.

        Deliberately does NOT call ``_warn_missing_fields``: the preset matched
        because every field it ships is on this note type, and every name it
        maps is inside that set, so the check has nothing to find and would
        spend an AnkiConnect round trip saying so.
        """
        config = self._wizard.working_config()
        merged = dict(config.anki_fields)
        merged.update(preset.fields)
        updated = replace(
            config,
            anki_fields=merged,
            pitch_category_format=preset.pitch_category_format,
            card_type_marker_fields=dict(preset.card_type_marker_fields),
            card_type=config.card_type if config.card_type in preset.supported_card_types else "",
        )
        if updated != config:
            self._wizard.update_working_config(updated)
        mapped = sum(1 for value in preset.fields.values() if value)
        self.mapping_summary.setText(
            tr_format(
                self.tr("Recognized %1 — mapped %2 fields. You can fine-tune these later in Settings → Anki."),
                preset.name,
                str(mapped),
            )
        )
        self.warning_label.setText("")
        self.completeChanged.emit()

    def _on_auto_map_clicked(self) -> None:
        note_type = self.notetype_combo.currentText().strip()
        if not self._field_names or self._field_names_note_type != note_type:
            return
        self._sanitize_field_mappings(note_type, self._field_names)
        preset = preset_for_field_names(self._field_names)
        if preset is not None:
            self._apply_preset(preset)
            return
        mapped = auto_map_fields(self._field_names)
        config = self._wizard.working_config()
        merged = dict(config.anki_fields)
        for key, value in mapped.items():
            if value and not merged.get(key):
                merged[key] = value
        # Stage anki_fields as a PLAIN dict; config re-wraps it in MappingProxyType.
        if merged != dict(config.anki_fields):
            self._wizard.update_working_config(replace(config, anki_fields=merged))
            self.completeChanged.emit()
        effective_mappings = {key: merged.get(key, "") for key in mapped}
        self._show_mapping_summary(effective_mappings)
        self._warn_missing_fields()

    def _show_mapping_summary(self, mapped: dict[str, str]) -> None:
        pairs = [f"{key} → {value}" for key, value in mapped.items() if value]
        if pairs:
            summary = ", ".join(pairs)
            self.mapping_summary.setText(
                tr_format(
                    self.tr("Mapped: %1\nYou can fine-tune these later in Settings → Anki."),
                    summary,
                )
            )
        else:
            self.mapping_summary.setText(self.tr("No fields could be auto-mapped."))

    def _warn_missing_fields(self) -> None:
        """Warn about required fields missing on the note type — checked off-thread.

        ``check_field_names()`` makes a synchronous AnkiConnect HTTP call (10s
        timeout), so it runs on a worker thread; the result updates
        ``warning_label`` on the GUI thread. A failure (Anki down) never raises
        into the GUI.
        """
        self.warning_label.setText(self.tr("Checking note type fields..."))
        self._warn_worker = self._start_live_check(
            self._wizard.validation_service().check_field_names,
            error_prefix=self.tr("Could not check note type fields: "),
            on_result=self._on_field_warning_result,
            on_error=self._on_field_warning_error,
        )

    def _on_field_warning_result(self, result: object) -> None:
        if not self._is_live_check():
            return  # Superseded by a newer check.
        ok, message = result if isinstance(result, tuple) else (False, str(result))
        self.warning_label.setText("" if ok else message)

    def _on_field_warning_error(self, message: str) -> None:
        if not self._is_live_check():
            return
        # Anki unreachable/slow: surface the failure but never raise.
        self.warning_label.setText(message)


class ResourcesPage(_LiveCheckPage):
    """Step 4: install the recommended resources. A dictionary is required.

    The dictionary used to be labelled optional, so setup could be completed in
    a state guaranteed to fail the first mine: without one, every mined card
    comes out with no definition (D26). The page therefore gates Next on a live
    probe of whether an enabled offline dictionary can actually answer a lookup
    — not on whether the download button was pressed, and not on a result
    cached from when the page was built. **Skip Setup** remains available on
    every page for anyone who genuinely cannot download right now.
    """

    def __init__(self, wizard: SetupWizard) -> None:
        super().__init__(wizard)
        self._dictionary_ready = False

        self.setTitle(self.tr("Recommended Resources"))
        self.setSubTitle(self.tr("Frequency and pitch accent are optional. A dictionary is required."))

        layout = QVBoxLayout(self)

        # RESOURCES_BLURB is registered under the "SetupWizard" context (module-level
        # QT_TRANSLATE_NOOP), so look it up there rather than via self.tr(), which would
        # query the "ResourcesPage" context and miss the translation.
        blurb = QLabel(QCoreApplication.translate("SetupWizard", RESOURCES_BLURB))
        blurb.setWordWrap(True)
        layout.addWidget(blurb)

        link = QLabel(f'<a href="{RESOURCES_HELP_URL}">{self.tr("What are these resources?")}</a>')
        link.setOpenExternalLinks(False)
        link.linkActivated.connect(lambda: _open_url(RESOURCES_HELP_URL))
        layout.addWidget(link)

        # Built from the active language's catalog, never hand-listed: a spec
        # added to a profile's catalog has to appear here without touching this
        # page. ja's catalog IS RECOMMENDED_DEFAULT_SET, so the ja wizard is
        # byte-identical to the pre-multilanguage one.
        from anki_miner.languages.registry import config_language, get_profile  # noqa: PLC0415

        # _sync_download_button reads _download_running, and the toggled
        # connection below deliberately comes AFTER setChecked: a fresh
        # unchecked box emits toggled the first time it is checked, and that
        # slot touches download_button, which this loop runs before.
        self._download_running = False
        # config_language, never the raw field: a stored code whose profile this
        # build cannot supply — a language whitelisted in config but with its
        # engine extra absent — is legal on disk, and raising here would make the
        # whole wizard unconstructible on first run.
        self._specs = list(get_profile(config_language(wizard.working_config())).catalog)
        self.resource_checks: dict[str, QCheckBox] = {}
        for spec in self._specs:
            noun = _RESOURCE_KIND_NOUNS.get(spec.kind)
            label = (
                tr_format(self.tr("%1 — %2"), QCoreApplication.translate("SetupWizard", noun), spec.display_name)
                if noun
                else spec.display_name
            )
            box = QCheckBox(label)
            box.setToolTip(spec.license_note)
            box.setChecked(True)
            box.toggled.connect(self._sync_download_button)
            layout.addWidget(box)
            self.resource_checks[spec.id] = box

        self.download_button = ModernButton(self.tr("Download recommended resources"), variant="primary")
        self.download_button.clicked.connect(self._on_download_clicked)
        layout.addWidget(self.download_button)

        self.status_label = QLabel("")
        self.status_label.setObjectName("helper-text")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        # Derived, never assumed: the button was born enabled, and the only
        # thing that ever re-derived it was a checkbox toggling. A language
        # whose catalog is empty has no checkbox to toggle, so it kept an
        # enabled button over a handler that returns silently at :1070.
        self._sync_download_button()
        if not self._specs:
            # ko's catalog is empty on purpose (languages/ko/catalog.py): no
            # Korean resource is both redistributable by link and shaped like
            # an importer here. Saying so beats a dead button, and the sentence
            # has to name where the resources DO come from.
            self.status_label.setText(
                self.tr(
                    "No downloadable resources are recommended for this language — import a Yomitan "
                    "dictionary in Settings → Dictionaries and a frequency list in Settings → Frequency."
                )
            )

        # Kept apart from status_label: one reports how the *download* ended,
        # the other what the app can *do now*. A single label would let a
        # finished download overwrite the readiness verdict that gates Next.
        self.dictionary_label = QLabel("")
        self.dictionary_label.setObjectName("validation-status")
        self.dictionary_label.setWordWrap(True)
        layout.addWidget(self.dictionary_label)

        # Frequency and pitch get their own lines rather than sharing the
        # dictionary's. They never gate Next, and a required verdict and an
        # optional one that read as a single sentence is how a user concludes
        # an optional resource is what blocked them.
        self.frequency_label = QLabel("")
        self.frequency_label.setObjectName("helper-text")
        self.frequency_label.setWordWrap(True)
        layout.addWidget(self.frequency_label)

        self.pitch_label = QLabel("")
        self.pitch_label.setObjectName("helper-text")
        self.pitch_label.setWordWrap(True)
        layout.addWidget(self.pitch_label)

        # Pitch accent is a Japanese resource family; a language without the
        # capability has no pitch row in its catalog and no verdict to report.
        self._language_gate_pairs: list[tuple[QWidget, str]] = []
        self._language_gate_pairs.append((self.pitch_label, "pitch"))
        self._apply_language_gate()

        # Retained past the run's end: the terminal window offers Retry setup,
        # which calls back into the session. Dropping the reference on finish
        # would collect the session and leave that button inert.
        self._session: ResourceDownloadSession | None = None

    def selected_specs(self) -> list[ResourceSpec]:
        """Catalog order, filtered to what is ticked."""
        return [spec for spec in self._specs if self.resource_checks[spec.id].isChecked()]

    def _sync_download_button(self) -> None:
        """Nothing ticked is not a run: an empty spec list reports success for no work."""
        self.download_button.setEnabled(bool(self.selected_specs()) and not self._download_running)

    def _apply_language_gate(self) -> None:
        """Re-derive the paired rows' visibility from the active language.

        Re-applied on every page entry because the wizard can be re-entered
        after a language switch. The gate is two-way and owns the whole
        visibility of a paired widget, so a switch back re-shows the row.
        """
        from anki_miner.gui.utils.language_gate import apply_language_gate  # noqa: PLC0415
        from anki_miner.languages.registry import config_language, get_profile  # noqa: PLC0415

        capabilities = get_profile(config_language(self._wizard.working_config())).capabilities
        apply_language_gate(self._language_gate_pairs, capabilities)

    def initializePage(self) -> None:
        """Ask the disk, every time the page is entered."""
        self._apply_language_gate()
        self._recheck_resources()

    def isComplete(self) -> bool:
        # Nothing to download is nothing to block on. The dictionary gate exists
        # so nobody finishes setup into a guaranteed-empty first mine (D26), and
        # it holds wherever a dictionary is one button away. Where the catalog is
        # empty that button does not exist, so the same gate is a wizard with no
        # exit but Skip Setup — the page still reports what the disk has through
        # dictionary_label, it just stops standing in the way.
        return self._dictionary_ready or not self._specs

    # --- live dictionary readiness ---

    def _recheck_resources(self) -> None:
        """Probe off-thread what all three resource families can do.

        Off-thread because the probe scans three resource folders, any of which
        can be a slow network path. One worker, not three: the base class keeps
        a single ``_live_check`` as its generation counter, so a second
        concurrent probe would have no way to be recognised as stale.
        """
        self._dictionary_ready = False
        self.dictionary_label.setText(self.tr("Checking for an offline dictionary..."))
        self.frequency_label.clear()
        self.pitch_label.clear()
        self.completeChanged.emit()
        self._start_live_check(
            self._wizard.validation_service().check_resource_readiness,
            error_prefix=self.tr("Could not check the installed resources: "),
            on_result=self._on_readiness_result,
            on_error=self._on_readiness_error,
        )

    def _on_readiness_result(self, result: object) -> None:
        from anki_miner.services.validation_service import ResourceReadiness  # noqa: PLC0415

        if not self._is_live_check():
            return
        if not isinstance(result, ResourceReadiness):
            self._on_readiness_error(str(result))
            return

        ok, message = result.dictionary
        self._dictionary_ready = bool(ok)
        self.dictionary_label.setText(tr_format(self.tr("Dictionary ready: %1"), message) if ok else message)

        # Nouns come from the same "SetupWizard"-context table the checkbox
        # labels use (:892) -- a second, ResourcesPage-context "Frequency" /
        # "Pitch accent" copy here let the two drift and doubled translator
        # work. One shared "X ready: Y" template stands in for the two
        # per-noun copies this used to carry.
        freq_noun = QCoreApplication.translate("SetupWizard", _RESOURCE_KIND_NOUNS["freq"])
        pitch_noun = QCoreApplication.translate("SetupWizard", _RESOURCE_KIND_NOUNS["pitch"])
        ready_template = self.tr("%1 ready: %2")
        self.frequency_label.setText(
            self._optional_line(result.frequency, freq_noun, tr_format(ready_template, freq_noun, "%1"))
        )
        self.pitch_label.setText(
            self._optional_line(result.pitch, pitch_noun, tr_format(ready_template, pitch_noun, "%1"))
        )
        self.completeChanged.emit()

    def _optional_line(self, answer: tuple[bool | None, str], noun: str, ready_template: str) -> str:
        """Render one optional family. ``None`` is a resting state, not a fault."""
        ok, message = answer
        if ok is None:
            return tr_format(self.tr("%1: not set up (optional)"), noun)
        if ok:
            return tr_format(ready_template, message)
        return message

    def _on_readiness_error(self, message: str) -> None:
        """One failed probe answered all three questions -- clear all three."""
        if not self._is_live_check():
            return
        self._dictionary_ready = False
        self.dictionary_label.setText(message)
        self.frequency_label.clear()
        self.pitch_label.clear()
        self.completeChanged.emit()

    def _on_download_clicked(self) -> None:
        """Start the download and hand the page back immediately.

        The flow is asynchronous now, so the page reports through the session's
        completion signal instead of a return value. Worker ownership goes to
        the wizard, whose close path already cancels every registered worker and
        defers ``done()`` until each one's native thread has exited — which is
        what keeps a run started here from outliving the wizard.
        """
        from anki_miner.gui.widgets.dialogs.resource_download_dialog import start_resource_download

        if self._download_running:
            return
        self.status_label.clear()
        specs = self.selected_specs()
        if not specs:
            return
        session = start_resource_download(
            self,
            self._wizard.working_config(),
            activate=self._activate_resources,
            release_resources=self._wizard._release_resources,
            task_registry=getattr(self._wizard.parent(), "task_registry", None),
            adopt_worker=self._wizard.register_worker,
            specs=specs,
        )
        if session is None:
            return
        self._session = session
        self._download_running = True
        self.download_button.setEnabled(False)
        session.finished.connect(self._on_download_finished)

    def _activate_resources(self, summary: object) -> AnkiMinerConfig | None:
        """Fold a completed summary into the wizard's working config.

        Read from ``working_config()`` at activation time, never from a config
        captured when the download started: the user can have changed the deck
        or note type on an earlier page while the transfer ran.
        """
        from anki_miner.gui.utils.resource_setup import apply_download_summary
        from anki_miner.gui.workers.resource_download_worker import ResourceDownloadSummary

        if not isinstance(summary, ResourceDownloadSummary) or not summary.succeeded:
            return None
        new_config = apply_download_summary(self._wizard.working_config(), summary)
        self._wizard.update_working_config(new_config)
        return new_config

    def _on_download_finished(self, outcome: object) -> None:
        """Report the run's real ending, including imported-but-not-active.

        Fires again after a successful **Retry setup**, which is the point: the
        status line has to stop saying the resources are inactive once they are
        not.
        """
        from anki_miner.gui.widgets.dialogs.resource_download_dialog import ResourceDownloadOutcome

        self._download_running = False
        # Not setEnabled(True): a finished run must not resurrect the button
        # for a selection the user has since emptied.
        self._sync_download_button()
        if not isinstance(outcome, ResourceDownloadOutcome):
            return

        summary = outcome.summary
        if summary.cancelled:
            status = (
                self.tr("Download cancelled. Some resources were installed before cancellation.")
                if summary.succeeded
                else self.tr("Download cancelled. No resources were installed.")
            )
        elif summary.succeeded and not outcome.activated:
            status = self.tr("Imported, but not active — Retry setup")
        elif summary.failed:
            status = (
                self.tr("Some resources were installed; some failed.")
                if summary.succeeded
                else self.tr("No resources were installed.")
            )
        else:
            status = self.tr("Resources installed.")
        self.status_label.setText(status)
        # Re-ask rather than infer: a summary saying the dictionary imported is
        # not the same claim as the chain being able to answer with it.
        self._recheck_resources()


#: The final page's required checks, in the order they are reported. Optional
#: tools (yt-dlp, alass, ffprobe) and optional packs are deliberately absent:
#: none of them is needed to mine a card, so none of them may block Finish.
_FINAL_CHECKS = ("ankiconnect", "deck", "note_type", "fields", "dictionary")


def _final_sweep(validation: ValidationService) -> dict[str, bool]:
    """Re-ask every required question, off the GUI thread.

    A module-level function, not a page method: it runs on a worker thread, and
    a bound method there is one careless attribute access away from touching a
    widget off-thread.

    Short-circuited the way ``validate_setup`` short-circuits — asking a closed
    Anki for its deck list produces a timeout, not an answer.
    """
    results = dict.fromkeys(_FINAL_CHECKS, False)
    results["dictionary"] = validation.check_offline_dictionary()[0]
    results["ankiconnect"] = validation.check_ankiconnect()[0]
    if not results["ankiconnect"]:
        return results
    results["deck"] = validation.check_deck_exists()[0]
    results["note_type"] = validation.check_note_type_exists()[0]
    if results["note_type"]:
        results["fields"] = validation.check_field_names()[0]
    return results


class DonePage(_LiveCheckPage):
    """Step 5: re-verify the whole setup, then offer the first real action.

    The old summary read the AnkiConnect page's cached ``_reachable`` flag and
    counted the mapped fields in config — both of which were true several
    minutes and one Anki restart ago. It could therefore say "AnkiConnect
    reachable: Yes" over a closed Anki. This page now runs its own sweep on
    entry and Finish stays disabled until every required check passes.
    """

    def __init__(self, wizard: SetupWizard) -> None:
        super().__init__(wizard)
        self._results: dict[str, bool] = {}
        self.setTitle(self.tr("Ready to Mine"))
        self.setSubTitle(self.tr("A last check of everything mining needs. You can change it later in Settings."))
        self.setFinalPage(True)

        layout = QVBoxLayout(self)
        self.summary_label = QLabel("")
        self.summary_label.setWordWrap(True)
        self.summary_label.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.summary_label)

        self.recheck_button = ModernButton(self.tr("Recheck"), variant="secondary")
        self.recheck_button.clicked.connect(self._start_sweep)
        layout.addWidget(self.recheck_button)

    def isComplete(self) -> bool:
        return all(self._results.get(name, False) for name in _FINAL_CHECKS)

    def initializePage(self) -> None:
        """Run one fresh readiness sweep; render it when it lands."""
        previous_check = self._live_check
        if still_running(previous_check):
            assert previous_check is not None
            previous_check.cancel()
        self._live_check = None
        self._start_sweep()

    def _start_sweep(self) -> None:
        if still_running(self._live_check):
            return
        self._results = {}
        self.summary_label.setText(self.tr("Checking your setup..."))
        self.recheck_button.setEnabled(False)
        self.completeChanged.emit()

        self._start_live_check(
            partial(_final_sweep, self._wizard.validation_service()),
            error_prefix=self.tr("Could not check your setup: "),
            on_result=self._on_sweep_result,
            on_error=self._on_sweep_error,
        )

    def _on_sweep_result(self, result: object) -> None:
        if not self._is_live_check():
            return
        self._results = dict(result) if isinstance(result, dict) else {}
        self.summary_label.setText(self._summary_html())
        self.recheck_button.setEnabled(True)
        self.completeChanged.emit()

    def _on_sweep_error(self, message: str) -> None:
        if not self._is_live_check():
            return
        self._results = {}
        self.summary_label.setText(message)
        self.recheck_button.setEnabled(True)
        self.completeChanged.emit()

    def _summary_html(self) -> str:
        cfg = self._wizard.working_config()
        yes = self.tr("Yes")
        no = self.tr("No")

        def mark(name: str) -> str:
            return yes if self._results.get(name, False) else no

        return "<br>".join(
            [
                tr_format(self.tr("AnkiConnect reachable: <b>%1</b>"), mark("ankiconnect")),
                tr_format(self.tr("Deck '%1' exists: <b>%2</b>"), cfg.anki_deck_name, mark("deck")),
                tr_format(self.tr("Note type '%1' exists: <b>%2</b>"), cfg.anki_note_type, mark("note_type")),
                tr_format(self.tr("Every mapped field exists: <b>%1</b>"), mark("fields")),
                tr_format(self.tr("Offline dictionary ready: <b>%1</b>"), mark("dictionary")),
            ]
        )
