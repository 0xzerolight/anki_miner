"""Wizard pages for the guided first-run Setup Wizard (Task 3).

Five ``QWizardPage`` subclasses. Each takes the parent :class:`SetupWizard` so
it can read/write the working config and use the wizard's shared
:class:`AnkiService` / :class:`ValidationService` and worker registry.

Detect & guide ONLY — no ``createDeck`` / ``createModel`` / ``ensure_deck``
calls anywhere. Deck/note type creation is the user's job; the wizard inspects,
explains, links, and re-checks.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from PyQt6.QtCore import QT_TRANSLATE_NOOP, Qt, QUrl
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
from anki_miner.gui.workers.fetch_decks_worker import FetchDecksWorker
from anki_miner.gui.workers.fetch_fields_worker import FetchFieldsWorker
from anki_miner.gui.workers.fetch_notetypes_worker import FetchNotetypesWorker
from anki_miner.utils.i18n import tr_format

if TYPE_CHECKING:
    from .setup_wizard import SetupWizard

# AnkiConnect is an Anki add-on; this is its add-on code on AnkiWeb.
ANKICONNECT_ADDON_CODE = "2055492159"
ANKICONNECT_URL = "https://ankiweb.net/shared/info/2055492159"
# Recommended Japanese-mining note type guidance (Lapis is the default note type).
NOTE_TYPE_HELP_URL = "https://github.com/0xzerolight/anki_miner#recommended-note-type"

# Moved from welcome_dialog.WELCOME_BLURB (that dialog is retired).
RESOURCES_BLURB = QT_TRANSLATE_NOOP(
    "SetupWizard",
    "Anki Miner works best with a frequency list, pitch accent data, and a "
    "dictionary. Download the recommended set now?",
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
                self.tr(
                    "AnkiConnect is a free Anki add-on. In Anki: Tools → Add-ons → "
                    "Get Add-ons…, paste the code <b>%1</b>, then restart Anki."
                ),
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

    def _recheck_work(self) -> tuple[bool, str]:
        """Blocking AnkiConnect check (runs off the GUI thread)."""
        from anki_miner.gui.widgets.dialogs.setup_wizard import setup_wizard as sw_mod

        cfg = replace(self._wizard.working_config(), ankiconnect_url=self.url_input.text().strip())
        return sw_mod.ValidationService(cfg).check_ankiconnect()

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
    """Step 2: choose the target deck (auto-created at mine time if missing)."""

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
        # Never hard-blocks: any non-empty name is acceptable (auto-created at mine time).
        return bool(self.deck_combo.currentText().strip())

    def _on_text_changed(self, _text: str) -> None:
        self._update_deck_hint()
        self.completeChanged.emit()

    def _write_deck_to_config(self) -> None:
        name = self.deck_combo.currentText().strip()
        if name and name != self._wizard.working_config().anki_deck_name:
            self._wizard.update_working_config(replace(self._wizard.working_config(), anki_deck_name=name))

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
        worker.error.connect(lambda _m: self.refresh_button.setEnabled(True))
        worker.start()

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

    def _update_deck_hint(self) -> None:
        name = self.deck_combo.currentText().strip()
        if not name:
            self.deck_hint.setText(self.tr("Enter a deck name."))
        elif self._fetched_decks and name not in self._fetched_decks:
            # Reuse the phrasing from validation_service's deck-not-found message.
            self.deck_hint.setText(self.tr("Deck not found — it will be created automatically when mining starts."))
        else:
            self.deck_hint.setText("")


class NoteTypePage(QWizardPage):
    """Step 3 (richest): choose a note type, auto-map its fields, warn on gaps."""

    def __init__(self, wizard: SetupWizard) -> None:
        super().__init__(wizard)
        self._wizard = wizard
        self._notetypes_worker: SingleCallWorker | None = None
        self._fields_worker: SingleCallWorker | None = None
        self._field_names: list[str] = []

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

    def initializePage(self) -> None:
        self.notetype_combo.setCurrentText(self._wizard.working_config().anki_note_type)
        self._on_refresh_clicked()

    def isComplete(self) -> bool:
        # Never hard-block: a non-empty note type is enough (warnings are advisory).
        return bool(self.notetype_combo.currentText().strip())

    def _on_notetype_changed(self, _text: str) -> None:
        self.completeChanged.emit()

    def validatePage(self) -> bool:
        self._write_notetype_to_config()
        return True

    def _write_notetype_to_config(self) -> None:
        name = self.notetype_combo.currentText().strip()
        if name and name != self._wizard.working_config().anki_note_type:
            self._wizard.update_working_config(replace(self._wizard.working_config(), anki_note_type=name))

    # --- note-type list fetch ---

    def _on_refresh_clicked(self) -> None:
        if self._notetypes_worker is not None and self._notetypes_worker.isRunning():
            return
        self.refresh_button.setEnabled(False)
        worker = FetchNotetypesWorker(self._wizard.anki_service(), self)
        self._notetypes_worker = worker
        self._wizard.register_worker(worker)
        worker.result_ready.connect(self._on_notetypes_fetched)
        worker.error.connect(lambda _m: self.refresh_button.setEnabled(True))
        worker.start()

    def _on_notetypes_fetched(self, model_names: object) -> None:
        self.refresh_button.setEnabled(True)
        names = list(model_names) if isinstance(model_names, list) else []
        current = self.notetype_combo.currentText()
        self.notetype_combo.blockSignals(True)
        self.notetype_combo.clear()
        self.notetype_combo.addItems(names)
        self.notetype_combo.setCurrentText(current or self._wizard.working_config().anki_note_type)
        self.notetype_combo.blockSignals(False)
        # Auto-fetch the fields for the selected note type so Auto-Map lights up.
        self._fetch_fields()

    # --- field list fetch ---

    def _fetch_fields(self) -> None:
        note_type = self.notetype_combo.currentText().strip()
        if not note_type:
            return
        if self._fields_worker is not None and self._fields_worker.isRunning():
            return
        self._write_notetype_to_config()
        worker = FetchFieldsWorker(self._wizard.anki_service(), note_type, self)
        self._fields_worker = worker
        self._wizard.register_worker(worker)
        worker.result_ready.connect(self._on_fields_fetched)
        worker.error.connect(lambda _m: None)
        worker.start()

    def _on_fields_fetched(self, field_names: object) -> None:
        names = list(field_names) if isinstance(field_names, list) else []
        self._field_names = names
        if not names:
            self.auto_map_button.setEnabled(False)
            self._show_guidance(
                self.tr(
                    "No fields found. Make sure Anki is running and the note type name is spelled "
                    "exactly as in Anki."
                )
            )
            return
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

    # --- auto-map ---

    def _on_auto_map_clicked(self) -> None:
        if not self._field_names:
            return
        mapped = auto_map_fields(self._field_names)
        note_type = self.notetype_combo.currentText().strip()
        # Stage anki_fields as a PLAIN dict; config re-wraps it in MappingProxyType.
        # Keep anki_word_field synced to anki_fields["word"] (same as panel.contribute).
        self._wizard.update_working_config(
            replace(
                self._wizard.working_config(),
                anki_note_type=note_type or self._wizard.working_config().anki_note_type,
                anki_fields=dict(mapped),
                anki_word_field=mapped.get("word", "") or "Expression",
            )
        )
        self._show_mapping_summary(mapped)
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
        """Non-blocking warning listing any required fields missing on the note type."""
        ok, message = self._wizard.validation_service().check_field_names()
        self.warning_label.setText("" if ok else message)


class ResourcesPage(QWizardPage):
    """Step 4: optionally download the recommended resource set (skippable)."""

    def __init__(self, wizard: SetupWizard) -> None:
        super().__init__(wizard)
        self._wizard = wizard

        self.setTitle(self.tr("Recommended Resources"))
        self.setSubTitle(self.tr("Frequency, pitch accent, and a dictionary (optional)."))

        layout = QVBoxLayout(self)

        blurb = QLabel(self.tr(RESOURCES_BLURB))
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

    def isComplete(self) -> bool:
        return True  # Always skippable.

    def _on_download_clicked(self) -> None:
        from anki_miner.gui.widgets.dialogs.resource_download_dialog import run_resource_download

        new_config = run_resource_download(self, self._wizard.working_config())
        if new_config is not None:
            self._wizard.update_working_config(new_config)
            self.status_label.setText(self.tr("Resources updated."))


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
        resources = cfg.use_frequency_data or cfg.use_pitch_accent
        yes = self.tr("Yes")
        no = self.tr("No")
        lines = [
            tr_format(self.tr("AnkiConnect reachable: <b>%1</b>"), yes if reachable else no),
            tr_format(self.tr("Deck: <b>%1</b>"), cfg.anki_deck_name),
            tr_format(self.tr("Note type: <b>%1</b>"), cfg.anki_note_type),
            tr_format(self.tr("Mapped fields: <b>%1</b>"), str(mapped_count)),
            tr_format(self.tr("Resources downloaded: <b>%1</b>"), yes if resources else no),
        ]
        self.summary_label.setText("<br>".join(lines))
