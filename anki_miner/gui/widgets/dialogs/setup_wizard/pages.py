"""Wizard pages for the guided first-run Setup Wizard (Task 3).

Five ``QWizardPage`` subclasses. Each takes the parent :class:`SetupWizard` so
it can read/write the working config and use the wizard's shared
:class:`AnkiService` / :class:`ValidationService` and worker registry.

Detect & guide ONLY — no ``createDeck`` / ``createModel`` / ``ensure_deck``
calls anywhere. Deck/note type creation is the user's job; the wizard inspects,
explains, links, and re-checks.
"""

from __future__ import annotations

import contextlib
from dataclasses import replace
from functools import partial
from typing import TYPE_CHECKING

from PyQt6.QtCore import QT_TRANSLATE_NOOP, QCoreApplication, Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWizardPage,
)

from anki_miner.gui.widgets.base import StatusBadge
from anki_miner.gui.widgets.enhanced import ModernButton
from anki_miner.gui.widgets.panels.anki_settings_panel import _FIELD_KEYWORDS, auto_map_fields
from anki_miner.gui.workers.base_worker import SingleCallWorker
from anki_miner.gui.workers.fetch_workers import (
    FetchDecksWorker,
    FetchFieldsWorker,
    FetchNotetypesWorker,
)
from anki_miner.services.anki_note_builder import configured_target_field_names
from anki_miner.utils.i18n import tr_format

if TYPE_CHECKING:
    from anki_miner.config import AnkiMinerConfig
    from anki_miner.gui.widgets.dialogs.resource_download_dialog import ResourceDownloadSession

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


def _open_url(url: str) -> None:
    QDesktopServices.openUrl(QUrl(url))


class AnkiConnectPage(QWizardPage):
    """Step 1: verify AnkiConnect is reachable; guide install if not."""

    def __init__(self, wizard: SetupWizard) -> None:
        super().__init__(wizard)
        self._wizard = wizard
        self._reachable = False
        self._worker: SingleCallWorker | None = None

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

    def initializePage(self) -> None:
        """Fire one auto recheck so the happy path is zero clicks."""
        self.url_input.setText(self._wizard.working_config().ankiconnect_url)
        self._on_recheck_clicked()

    def isComplete(self) -> bool:
        return self._reachable

    def _write_url_to_config(self) -> None:
        """Stage the URL field into the working config."""
        url = self.url_input.text().strip()
        if url and url != self._wizard.working_config().ankiconnect_url:
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
        if self._worker is not None and self._worker.isRunning():
            return
        self._write_url_to_config()
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
        self._reachable = bool(ok)
        self.badge.set_status("success" if ok else "error", message)
        self.result_label.setText(message)
        self.completeChanged.emit()

    def _on_recheck_error(self, message: str) -> None:
        self.recheck_button.setEnabled(True)
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
        if self._worker is not None and self._worker.isRunning():
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


class NoteTypePage(QWizardPage):
    """Step 3 (richest): choose a note type, auto-map its fields, warn on gaps."""

    def __init__(self, wizard: SetupWizard) -> None:
        super().__init__(wizard)
        self._wizard = wizard
        self._notetypes_worker: SingleCallWorker | None = None
        self._fields_worker: SingleCallWorker | None = None
        self._warn_worker: SingleCallWorker | None = None
        # Monotonic id of the latest field-name check; on_done/on_error ignore any
        # result whose generation no longer matches (superseded by a newer check).
        self._warn_generation = 0
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
        self.guidance_label.linkActivated.connect(lambda _u: _open_url(NOTE_TYPE_HELP_URL))
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
        if self._notetypes_worker is not None and self._notetypes_worker.isRunning():
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
        if not self._has_mining_shape(names):
            self._show_guidance(
                tr_format(
                    self.tr(
                        "This note type does not look set up for Japanese mining (no obvious word/"
                        "sentence fields). Import a recommended mining note type in Anki, then "
                        '<a href="%1">recheck</a>. See: <a href="%1">recommended note type</a>.'
                    ),
                    NOTE_TYPE_HELP_URL,
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

    def _on_auto_map_clicked(self) -> None:
        note_type = self.notetype_combo.currentText().strip()
        if not self._field_names or self._field_names_note_type != note_type:
            return
        self._sanitize_field_mappings(note_type, self._field_names)
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
        ``warning_label`` on the GUI thread. Overlapping checks are superseded by
        a generation counter so only the latest result wins, and a failure
        (Anki down) never raises into the GUI.
        """
        self._warn_generation += 1
        generation = self._warn_generation
        self.warning_label.setText(self.tr("Checking note type fields..."))

        validation = self._wizard.validation_service()

        def _set_warning(text: str) -> None:
            # The page may have been torn down while the check was in flight, so
            # the QLabel's C++ object can already be gone — suppress that, never
            # raise into the GUI.
            with contextlib.suppress(RuntimeError):
                self.warning_label.setText(text)

        def _on_done(result: object) -> None:
            if generation != self._warn_generation:
                return  # Superseded by a newer check.
            ok, message = result if isinstance(result, tuple) else (False, str(result))
            _set_warning("" if ok else message)

        def _on_error(message: str) -> None:
            if generation != self._warn_generation:
                return
            # Anki unreachable/slow: surface the failure but never raise.
            _set_warning(message)

        worker = SingleCallWorker(
            validation.check_field_names,
            error_prefix=self.tr("Could not check note type fields: "),
            parent=self,
        )
        self._warn_worker = worker
        self._wizard.register_worker(worker)
        worker.result_ready.connect(_on_done)
        worker.error.connect(_on_error)
        worker.start()


class ResourcesPage(QWizardPage):
    """Step 4: optionally download the recommended resource set (skippable)."""

    def __init__(self, wizard: SetupWizard) -> None:
        super().__init__(wizard)
        self._wizard = wizard

        self.setTitle(self.tr("Recommended Resources"))
        self.setSubTitle(self.tr("Frequency, pitch accent, and a dictionary (optional)."))

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

        self.download_button = ModernButton(self.tr("Download recommended resources"), variant="primary")
        self.download_button.clicked.connect(self._on_download_clicked)
        layout.addWidget(self.download_button)

        self.status_label = QLabel("")
        self.status_label.setObjectName("helper-text")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        # Retained past the run's end: the terminal window offers Retry setup,
        # which calls back into the session. Dropping the reference on finish
        # would collect the session and leave that button inert.
        self._session: ResourceDownloadSession | None = None
        self._download_running = False

    def isComplete(self) -> bool:
        return True  # Always skippable.

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
        session = start_resource_download(
            self,
            self._wizard.working_config(),
            activate=self._activate_resources,
            release_resources=self._wizard._release_resources,
            task_registry=getattr(self._wizard.parent(), "task_registry", None),
            adopt_worker=self._wizard.register_worker,
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
        self.download_button.setEnabled(True)
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


class DonePage(QWizardPage):
    """Step 5: summary of what was set up."""

    def __init__(self, wizard: SetupWizard) -> None:
        super().__init__(wizard)
        self._wizard = wizard
        self.setTitle(self.tr("All Set"))
        self.setSubTitle(self.tr("Review your setup. You can change anything later in Settings."))
        self.setFinalPage(True)

        layout = QVBoxLayout(self)
        self.summary_label = QLabel("")
        self.summary_label.setWordWrap(True)
        self.summary_label.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.summary_label)

    def initializePage(self) -> None:
        cfg = self._wizard.working_config()
        reachable = getattr(self._wizard.ankiconnect_page, "_reachable", False)
        mapped_count = sum(1 for v in cfg.anki_fields.values() if v)
        resources = cfg.frequency_active or cfg.pitch_active
        yes = self.tr("Yes")
        no = self.tr("No")
        lines = [
            tr_format(self.tr("AnkiConnect reachable: <b>%1</b>"), yes if reachable else no),
            tr_format(self.tr("Deck: <b>%1</b>"), cfg.anki_deck_name),
            tr_format(self.tr("Note type: <b>%1</b>"), cfg.anki_note_type),
            tr_format(self.tr("Mapped fields: <b>%1</b>"), str(mapped_count)),
            tr_format(self.tr("Resources configured: <b>%1</b>"), yes if resources else no),
        ]
        self.summary_label.setText("<br>".join(lines))
