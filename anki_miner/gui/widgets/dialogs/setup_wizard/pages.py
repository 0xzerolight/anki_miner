"""Wizard pages for the guided first-run Setup Wizard (Task 3).

Four ``QWizardPage`` subclasses (D8): the mining language (first run only), the
dictionary download, the Anki page, and the Ready check. The Anki page hosts
three sections that used to be pages of their own -- the AnkiConnect check,
the deck and the note type -- and keeps their class names, because pylupdate6
files every ``self.tr`` string under its class name. Each page or section takes
the parent :class:`SetupWizard` so it can read/write the working config and use
the wizard's shared :class:`AnkiService` / :class:`ValidationService` and
worker registry.

Detect & guide ONLY — no ``createDeck`` / ``createModel`` / ``ensure_deck``
calls anywhere. Deck/note type creation is the user's job; the wizard inspects,
explains, links, and re-checks.
"""

from __future__ import annotations

import html
from collections.abc import Callable
from dataclasses import replace
from functools import partial
from typing import TYPE_CHECKING, Any

from PyQt6.QtCore import QT_TRANSLATE_NOOP, QCoreApplication, QEvent, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QGuiApplication
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
    QWizardPage,
)

from anki_miner.gui.utils.ankiconnect_help import (
    ANKICONNECT_ADDON_CODE,
    anki_launch_command,
    ankiconnect_install_steps,
    launch_anki,
)
from anki_miner.gui.utils.language_choices import available_mining_languages
from anki_miner.gui.utils.run_off_thread import still_running
from anki_miner.gui.widgets.enhanced import ModernButton
from anki_miner.gui.widgets.panels.anki_settings_panel import (
    _FIELD_KEYWORDS,
    auto_map_fields,
    auto_map_profile_fields,
)
from anki_miner.gui.workers.base_worker import SingleCallWorker
from anki_miner.gui.workers.fetch_workers import (
    FetchDecksWorker,
    FetchFieldsWorker,
    FetchNotetypesWorker,
)
from anki_miner.languages.registry import config_language
from anki_miner.languages.switching import LANGUAGE_SCOPED_FIELDS, switch_language
from anki_miner.services.anki_note_builder import configured_target_field_names
from anki_miner.services.note_presets import NotePreset, preset_for_field_names
from anki_miner.utils.i18n import tr_format

if TYPE_CHECKING:
    from anki_miner.config import AnkiMinerConfig
    from anki_miner.gui.widgets.dialogs.resource_download_dialog import ResourceDownloadSession
    from anki_miner.services.resource_catalog import ResourceSpec
    from anki_miner.services.validation_service import ValidationService

    from .setup_wizard import SetupWizard

ANKICONNECT_URL = "https://ankiweb.net/shared/info/2055492159"
RESOURCES_HELP_URL = "https://github.com/0xzerolight/anki_miner/blob/main/RESOURCES.md"
# Note types that map cleanly (the Lapis/Kiku/Senren presets, or any word + sentence field list).
NOTE_TYPE_HELP_URL = f"{RESOURCES_HELP_URL}#note-types"


def resources_help_url(language: str) -> str:
    """RESOURCES.md at *language*'s section; each heading there is a profile's English name."""
    from anki_miner.languages.registry import get_profile  # noqa: PLC0415

    return f"{RESOURCES_HELP_URL}#{get_profile(language).english_name.lower()}"


#: Family noun per catalog ``kind``, so a checkbox says what a resource *is*
#: rather than only what it is called. Keyed by ``ResourceSpec.kind``; a kind
#: with no entry here falls back to the display name alone.
_RESOURCE_KIND_NOUNS = {
    "dict": QT_TRANSLATE_NOOP("SetupWizard", "Dictionary"),
    "freq": QT_TRANSLATE_NOOP("SetupWizard", "Frequency"),
    "pitch": QT_TRANSLATE_NOOP("SetupWizard", "Pitch accent"),
}


def _open_url(url: str) -> None:
    QDesktopServices.openUrl(QUrl(url))


def _html_text(text: str) -> str:
    """Escape ``text`` for a rich-text label's text node, keeping apostrophes readable.

    ``html.escape`` also escapes ``'`` by default, and a rich-text QLabel's
    ``text()`` then hands back "You&#x27;re" to anyone reading it. Only
    ``&``, ``<`` and ``>`` matter outside an attribute.
    """
    return html.escape(text, quote=False)


def _add_page_header(layout: QVBoxLayout, title: str, subtitle: str) -> tuple[QLabel, QLabel]:
    """Put a page's title and subtitle at the top of ``layout``, dialog style (B08).

    QWizard's own header band draws the title in its own fonts, which ignore
    the stylesheet. The pages leave ``setTitle`` empty, which removes the band,
    and use the ``heading2`` / ``dialog-subtitle`` pair EnhancedDialog uses.
    An empty subtitle starts hidden.
    """
    title_label = QLabel(title)
    title_label.setObjectName("heading2")
    title_label.setWordWrap(True)
    subtitle_label = QLabel(subtitle)
    subtitle_label.setObjectName("dialog-subtitle")
    subtitle_label.setWordWrap(True)
    subtitle_label.setVisible(bool(subtitle))
    layout.addWidget(title_label)
    layout.addWidget(subtitle_label)
    return title_label, subtitle_label


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


class _WizardSection(QWidget):
    """A part of a wizard page that used to be a page of its own (D8).

    AnkiConnectPage, DeckPage and NoteTypePage are sections of AnkiPage. They
    keep QWizardPage's method names (``initializePage``, ``isComplete``,
    ``validatePage``, ``completeChanged``) so AnkiPage can forward each call and
    tests can treat a section like the page it was.
    """

    completeChanged = pyqtSignal()  # noqa: N815 - mirrors QWizardPage.completeChanged

    def __init__(self, wizard: SetupWizard) -> None:
        super().__init__()
        self._wizard = wizard

    def initializePage(self) -> None:
        """Called by the hosting page; the default has nothing to prepare."""

    def isComplete(self) -> bool:
        return True

    def validatePage(self) -> bool:
        return True

    def stage_current_edits(self) -> None:
        """Stage editor state into the working config, with no I/O."""


class MiningLanguagePage(QWizardPage):
    """Name the language being mined; registered on the first run only.

    First when it is registered at all, and not a selector found in Settings
    afterwards: every step from here on -- deck, note type, recommended
    resources -- is derived from the mining language, so a Mandarin learner who
    answers here is set up for Mandarin instead of being walked through a
    Japanese setup first. A wizard re-run from Tools does not ask, because by
    then the Settings selector and its guarded switch are a click away.

    The list is the one Settings offers, which already drops a language whose
    engine this build cannot supply. Installing one stays in Settings -> Mining
    Language: a download that size does not belong mid-setup, and a second
    downloader here would be a second thing to keep in step with the first.

    A pick reaches the working config on Next, because the deck, note type and
    resource steps read it from there; it survives only an accepted Finish.
    See :meth:`revert_language_change`.
    """

    def __init__(self, wizard: SetupWizard) -> None:
        super().__init__(wizard)
        self._wizard = wizard
        # Where the combo opens is a display default, not a
        # user decision. Only an actual pick may rewrite the config, so a
        # config naming a language this build cannot offer -- its engine pack
        # is gone -- is left alone rather than quietly switched to whatever
        # happens to show first.
        self._touched = False
        config = wizard.working_config()
        # The raw field, not config_language: switch_language parks the
        # outgoing snapshot under it, so that is the key a single switch from
        # here would leave behind. See _single_switch.
        self._kept_stash_codes = frozenset(config.language_stash) | {config.language}
        # Exactly the fields switch_language moves, as the wizard was opened
        # on: what revert_language_change puts back. Taken at construction,
        # which is before any page can have touched them.
        self._opening_language_state: dict[str, Any] = {
            "language": config.language,
            "language_stash": config.language_stash,
            **{name: getattr(config, name) for name in LANGUAGE_SCOPED_FIELDS},
        }

        layout = QVBoxLayout(self)
        self.title_label, self.subtitle_label = _add_page_header(
            layout,
            self.tr("Choose a Mining Language"),
            self.tr("The language you are learning. The interface language is separate."),
        )

        self.language_combo = QComboBox()
        for code, display_name in available_mining_languages():
            self.language_combo.addItem(display_name, code)
        self.language_combo.currentIndexChanged.connect(self._on_language_changed)
        layout.addWidget(self.language_combo)

        helper = QLabel(
            self.tr(
                "The deck, note type and resources in the next steps follow this choice. A language "
                "missing from the list needs its engine pack: Settings → Mining Language."
            )
        )
        helper.setObjectName("helper-text")
        helper.setWordWrap(True)
        layout.addWidget(helper)
        layout.addStretch(1)

        self._point_at_working_config()

    def initializePage(self) -> None:
        self._point_at_working_config()

    def isComplete(self) -> bool:
        # Always true. Every language in the combo can be mined here, and the
        # config already holds one, so this step must never hold the wizard up.
        return True

    def _point_at_working_config(self) -> None:
        """Show the language actually in force, without proposing a change.

        Signals blocked: ``currentIndexChanged`` is what marks a user's pick,
        and re-pointing the combo on page entry is not one.
        """
        index = self.language_combo.findData(config_language(self._wizard.working_config()))
        if index < 0:
            return
        self.language_combo.blockSignals(True)
        try:
            self.language_combo.setCurrentIndex(index)
        finally:
            self.language_combo.blockSignals(False)

    def _on_language_changed(self, _index: int) -> None:
        self._touched = True

    def _single_switch(self, config: AnkiMinerConfig, code: str) -> AnkiMinerConfig:
        """Switch to ``code``, leaving the stash one switch would have left.

        Back-and-pick-again chains switches, and each one parks the language it
        leaves. ``language_stash`` membership is what later marks a first visit
        to a language, so a detour through Chinese would silently spend
        Chinese's first visit -- no deck checklist, no setup offer, the next
        time the user really goes there. Snapshots only this page's own detours
        created are dropped again; the ones the wizard opened with, and the
        language it opened on, are what a single switch would leave.
        """
        switched = switch_language(config, code)
        detour = set(switched.language_stash) - self._kept_stash_codes
        if not detour:
            return switched
        kept = {parked: values for parked, values in switched.language_stash.items() if parked not in detour}
        return replace(switched, language_stash=kept)

    def _write_language_to_config(self) -> None:
        if not self._touched:
            return
        code = self.language_combo.currentData()
        if not isinstance(code, str) or not code:
            return
        self._wizard.update_working_config(self._single_switch(self._wizard.working_config(), code))

    def validatePage(self) -> bool:
        self._write_language_to_config()
        return True

    def revert_language_change(self) -> None:
        """Put the language back as the wizard was opened on it.

        The working config is what the caller persists, on every close path
        and not only on Finish, so a pick committed by one Next would outlive
        a Skip Setup or an Escape -- and a walk-away must not leave the user
        mining a language they never confirmed. Called from the wizard's close
        funnel for every result but an accepted Finish.

        Only the language's own fields go back. The AnkiConnect URL and the
        theme belong to no language and have always survived a walk-away; the
        deck and note type do belong to one, so a deck named for the language
        being reverted goes with it.
        """
        config = self._wizard.working_config()
        if config.language == self._opening_language_state["language"]:
            return
        self._wizard.update_working_config(replace(config, **self._opening_language_state))


class AnkiConnectPage(_WizardSection):
    """The Anki page's connection section (B03, D11).

    Connected, it is one line. Not connected, it is three numbered steps: open
    Anki (a button when Anki sits in its standard install place), install
    AnkiConnect with a one-click copy of its code, restart Anki; the page then
    connects by itself (B02). The AnkiConnect address is behind a small link,
    because Settings cannot be reached while this modal wizard is open.

    The section renders its own translated sentence from the check's ``ok``
    flag; the service's English message goes in the tooltip.
    """

    #: The latest answer: True when AnkiConnect replied, False otherwise.
    reachability_changed = pyqtSignal(bool)

    def __init__(self, wizard: SetupWizard) -> None:
        super().__init__(wizard)
        self._reachable = False
        #: False until the first answer lands; later checks keep the last answer
        #: on screen instead of flickering through "Checking".
        self._has_result = False
        self._worker: SingleCallWorker | None = None
        self._active_recheck_url: str | None = None
        self._copied = False
        self._launch_command = anki_launch_command()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.result_label = QLabel("")
        self.result_label.setWordWrap(True)
        layout.addWidget(self.result_label)

        open_step, _install_step, restart_step = ankiconnect_install_steps()
        self.steps = QWidget()
        steps_layout = QVBoxLayout(self.steps)
        steps_layout.setContentsMargins(0, 0, 0, 0)

        first_row = QHBoxLayout()
        self.step1_label = QLabel(f"1. {open_step}")
        first_row.addWidget(self.step1_label)
        self.open_anki_button = ModernButton(self.tr("Open Anki"), variant="secondary")
        self.open_anki_button.clicked.connect(self._on_open_anki_clicked)
        first_row.addWidget(self.open_anki_button)
        first_row.addStretch(1)
        steps_layout.addLayout(first_row)
        if self._launch_command is not None:
            # D11: with Anki in its standard install place, step 1 is a button.
            self.step1_label.setText("1.")
        else:
            self.open_anki_button.setVisible(False)

        self.step2_label = QLabel("")
        self.step2_label.setWordWrap(True)
        self.step2_label.setTextFormat(Qt.TextFormat.RichText)
        self.step2_label.linkActivated.connect(self._on_step_link)
        steps_layout.addWidget(self.step2_label)

        connects = self.tr("This page connects by itself.")
        self.step3_label = QLabel(f"3. {restart_step} {connects}")
        self.step3_label.setWordWrap(True)
        steps_layout.addWidget(self.step3_label)

        addon_page = self.tr("Open the AnkiConnect add-on page")
        self.addon_link = QLabel(f'<a href="{ANKICONNECT_URL}">{_html_text(addon_page)}</a>')
        self.addon_link.setOpenExternalLinks(False)
        self.addon_link.linkActivated.connect(self._on_addon_link)
        steps_layout.addWidget(self.addon_link)
        layout.addWidget(self.steps)
        self.steps.setVisible(False)

        other_address = self.tr("Use a different address…")
        self.address_link = QLabel(f'<a href="address">{_html_text(other_address)}</a>')
        self.address_link.setOpenExternalLinks(False)
        self.address_link.linkActivated.connect(self._on_address_link)
        layout.addWidget(self.address_link)
        self.address_link.setVisible(False)

        self.url_row = QWidget()
        url_layout = QHBoxLayout(self.url_row)
        url_layout.setContentsMargins(0, 0, 0, 0)
        url_layout.addWidget(QLabel(self.tr("AnkiConnect URL:")))
        self.url_input = QLineEdit(wizard.working_config().ankiconnect_url)
        self.url_input.setPlaceholderText("http://127.0.0.1:8765")
        url_layout.addWidget(self.url_input, 1)
        layout.addWidget(self.url_row)
        self.url_row.setVisible(False)

        self._render_step2()
        self.url_input.textChanged.connect(self._on_url_changed)
        # Leaving the field (or Return) re-checks the new address at once.
        self.url_input.editingFinished.connect(self.recheck)

    def initializePage(self) -> None:
        """Fire one check so the happy path is zero clicks."""
        self.url_input.setText(self._wizard.working_config().ankiconnect_url)
        self.recheck()

    def isComplete(self) -> bool:
        return self._reachable

    def _on_url_changed(self, _text: str) -> None:
        self._reachable = False
        self._has_result = False
        self.result_label.clear()
        self.result_label.setToolTip("")
        self.completeChanged.emit()
        self.reachability_changed.emit(False)

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

    def recheck(self) -> None:
        """Ask AnkiConnect again, off the GUI thread. The section's refresh path (B02)."""
        if still_running(self._worker):
            return
        self._write_url_to_config()
        url = self._normalized_url()
        if not url:
            self._reachable = False
            self._has_result = True
            self.result_label.setText(self.tr("Enter an AnkiConnect URL."))
            self.result_label.setToolTip("")
            self.steps.setVisible(False)
            self.address_link.setVisible(False)
            self.completeChanged.emit()
            self.reachability_changed.emit(False)
            return
        self._active_recheck_url = url
        if not self._has_result:
            self.result_label.setText(self.tr("Checking the connection to Anki…"))
        worker = SingleCallWorker(self._recheck_work, error_prefix="", parent=self)
        self._worker = worker
        self._wizard.register_worker(worker)
        worker.result_ready.connect(self._on_recheck_result)
        worker.error.connect(self._on_recheck_error)
        worker.start()

    def _on_recheck_result(self, result: object) -> None:
        """Main-thread slot: render the check's answer."""
        ok, message = result if isinstance(result, tuple) else (False, str(result))
        if self._active_recheck_url is not None and self._active_recheck_url != self._normalized_url():
            return
        self._settle(bool(ok), str(message))

    def _on_recheck_error(self, message: str) -> None:
        if self._active_recheck_url is not None and self._active_recheck_url != self._normalized_url():
            return
        self._settle(False, message)

    def _settle(self, reachable: bool, message: str) -> None:
        self._reachable = reachable
        self._has_result = True
        if reachable:
            self.result_label.setText(self.tr("Connected to Anki."))
            self.open_anki_button.setEnabled(True)
            self.open_anki_button.setText(self.tr("Open Anki"))
        else:
            self.result_label.setText(self.tr("Anki Miner can't reach Anki yet. Do this once:"))
        # The service's own sentence, for whoever wants the detail (B03).
        self.result_label.setToolTip(message)
        self.steps.setVisible(not reachable)
        self.address_link.setVisible(not reachable and not self.url_row.isVisibleTo(self))
        self.completeChanged.emit()
        self.reachability_changed.emit(reachable)

    def _render_step2(self) -> None:
        _open_step, install_step, _restart_step = ankiconnect_install_steps()
        link_text = self.tr("Copied") if self._copied else self.tr("Copy code")
        self.step2_label.setText(f'2. {_html_text(install_step)} <a href="copy">{_html_text(link_text)}</a>')

    def _on_step_link(self, href: str) -> None:
        if href != "copy":
            return
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(ANKICONNECT_ADDON_CODE)
        self._copied = True
        self._render_step2()

    def _on_addon_link(self, _href: str) -> None:
        _open_url(ANKICONNECT_URL)

    def _on_address_link(self, _href: str) -> None:
        self.url_row.setVisible(True)
        self.address_link.setVisible(False)
        self.url_input.setFocus()

    def _on_open_anki_clicked(self) -> None:
        command = self._launch_command
        if command is None:
            return
        if launch_anki(command):
            # The automatic re-check (B02) connects once Anki is up.
            self.open_anki_button.setEnabled(False)
            self.open_anki_button.setText(self.tr("Starting Anki…"))
            return
        self.open_anki_button.setVisible(False)
        failed = self.tr("Anki did not start. Open it yourself.")
        self.step1_label.setText(f"1. {failed}")


class DeckPage(_WizardSection):
    """The Anki page's deck section: the deck must already exist in Anki."""

    def __init__(self, wizard: SetupWizard) -> None:
        super().__init__(wizard)
        self._worker: SingleCallWorker | None = None
        self._fetched_decks: list[str] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.heading_label = QLabel(self.tr("Deck"))
        self.heading_label.setObjectName("heading3")
        layout.addWidget(self.heading_label)

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
        self.load_from_config()
        self._on_refresh_clicked()

    def load_from_config(self) -> None:
        """Show the working config's deck without fetching anything."""
        self.deck_combo.setCurrentText(self._wizard.working_config().anki_deck_name)

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


class NoteTypePage(_WizardSection):
    """The Anki page's note-type section; its fields map themselves when they arrive (D8)."""

    def __init__(self, wizard: SetupWizard) -> None:
        super().__init__(wizard)
        self._notetypes_worker: SingleCallWorker | None = None
        self._fields_worker: SingleCallWorker | None = None
        self._fields_generation = 0
        self._desired_note_type = ""
        self._active_fields_request: tuple[int, str] | None = None
        self._accept_field_fetches = True
        self._fetched_note_types: list[str] = []
        self._field_names: list[str] = []
        self._field_names_note_type: str | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.heading_label = QLabel(self.tr("Note type"))
        self.heading_label.setObjectName("heading3")
        layout.addWidget(self.heading_label)

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
        self.load_from_config()
        self._on_refresh_clicked()

    def load_from_config(self) -> None:
        """Show the working config's note type without fetching the list."""
        self.notetype_combo.setCurrentText(self._wizard.working_config().anki_note_type)

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
            # Anki keys duplicates on a note type's first field, so the card
            # builder refuses a word mapped anywhere else (anki_note_builder).
            and self._field_names[0] == word_field
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
            self.mapping_summary.setText("")
            self.warning_label.setText("")
            self._show_guidance(
                self.tr(
                    "No fields found. Make sure Anki is running and the note type name is spelled exactly as in Anki."
                )
            )
            self.completeChanged.emit()
            return
        self._sanitize_field_mappings(note_type, names)
        self._auto_fill(note_type)

    def _matching_preset(self, field_names: list[str]) -> NotePreset | None:
        """The preset for these field names, but only where presets apply.

        Lapis, Kiku and Senren are Japanese note types and their mappings carry
        furigana and pitch fields, so they ride the same ``note_presets``
        capability the Settings Preset row does: applying one elsewhere stages
        mappings the language cannot fill and every run's field check then
        rejects. Without it the caller falls through to the keyword pass.
        """
        from anki_miner.languages.registry import get_profile  # noqa: PLC0415

        capabilities = get_profile(config_language(self._wizard.working_config())).capabilities
        return preset_for_field_names(field_names) if "note_presets" in capabilities else None

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
        """Stage ``preset``'s whole answer (fields, pitch format, card markers) onto the working config."""
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
        self.mapping_summary.setText(tr_format(self.tr("%1 recognised: %2 fields filled."), preset.name, mapped))

    def _auto_fill(self, note_type: str) -> None:
        """Fill the field mappings the moment a note type's fields arrive (D8).

        A note type Anki Miner can name (Lapis, Kiku, Senren) takes its preset,
        which also carries the pitch format and card markers; anything else gets
        the keyword pass, which fills only keys that are still empty. There is
        no button: picking the note type is the whole action. Cross-workstream
        task TX.2.02 swaps the mapping part for the helper Settings uses (D13).
        """
        names = self._field_names
        if not names or self._field_names_note_type != note_type:
            return
        preset = self._matching_preset(names)
        if preset is not None:
            self.guidance_label.setVisible(False)
            self.guidance_label.setText("")
            self._apply_preset(preset)
        else:
            if self._has_mining_shape(names):
                self.guidance_label.setVisible(False)
                self.guidance_label.setText("")
            else:
                guidance = tr_format(
                    self.tr(
                        "This note type has no obvious word or sentence fields. "
                        '<a href="%1">Recheck</a> after importing a '
                        '<a href="%1">recommended note type</a> in Anki.'
                    ),
                    NOTE_TYPE_HELP_URL,
                )
                self._show_guidance(guidance.replace(f'href="{NOTE_TYPE_HELP_URL}"', 'href="recheck"', 1))
            self._apply_keyword_map()
        self._show_field_problem()
        self.completeChanged.emit()

    def _apply_keyword_map(self) -> None:
        """Fill every still-empty key whose Anki field name the keyword table knows."""
        from anki_miner.languages.registry import get_profile  # noqa: PLC0415

        mapped = auto_map_fields(self._field_names)
        config = self._wizard.working_config()
        # The chosen language's own card fields (Pinyin, Hanja, …) get the same
        # pass against their spec's placeholder, against THIS config's profile:
        # a learner who finishes setup in the wizard never opens the Settings
        # panel that would otherwise be the only place they are filled.
        mapped.update(
            auto_map_profile_fields(
                self._field_names,
                get_profile(config_language(config)).extra_card_fields,
                mapped.values(),
            )
        )
        merged = dict(config.anki_fields)
        for key, value in mapped.items():
            if value and not merged.get(key):
                merged[key] = value
        # Stage anki_fields as a PLAIN dict; config re-wraps it in MappingProxyType.
        if merged != dict(config.anki_fields):
            self._wizard.update_working_config(replace(config, anki_fields=merged))
        # The count, not the key → field dump: those keys are config names
        # (``expression_furigana``, ``frequency_sort``) nobody can act on.
        filled = sum(1 for key in mapped if merged.get(key))
        self.mapping_summary.setText(
            tr_format(self.tr("Fields filled automatically: %1."), filled)
            if filled
            else self.tr("No fields could be filled automatically.")
        )

    def _show_field_problem(self) -> None:
        """Say why Next is off when the word is not in the note type's first field.

        Checked from the field list already fetched, with no AnkiConnect round
        trip. An empty word mapping says nothing here (B01): the page's guidance
        already explains what to pick, and the Ready page re-checks everything.
        """
        word = self._wizard.working_config().anki_fields.get("word", "")
        names = self._field_names
        if not word or not names or names[0] == word:
            self.warning_label.setText("")
            return
        self.warning_label.setText(
            tr_format(
                self.tr(
                    "The word goes in the note type's first field, “%1”, but it is mapped to “%2”. "
                    "Change the order of the fields in Anki, or pick another note type."
                ),
                names[0],
                word,
            )
        )


class AnkiPage(QWizardPage):
    """Connection, deck and note type on one page (D8).

    While AnkiConnect is unreachable only the connection section shows; once it
    answers, the deck and note-type pickers appear and both lists are fetched,
    because a list asked of a closed Anki is a timeout, not a list. Each
    section keeps its own workers and staleness rules; this page forwards the
    page calls to them and is complete when all three are.
    """

    def __init__(self, wizard: SetupWizard) -> None:
        super().__init__(wizard)
        self._wizard = wizard
        layout = QVBoxLayout(self)
        self.title_label, self.subtitle_label = _add_page_header(
            layout,
            self.tr("Connect to Anki"),
            self.tr("Anki Miner talks to Anki through the AnkiConnect add-on."),
        )
        self.connect_section = AnkiConnectPage(wizard)
        layout.addWidget(self.connect_section)

        self.pickers = QWidget()
        pickers_layout = QVBoxLayout(self.pickers)
        pickers_layout.setContentsMargins(0, 0, 0, 0)
        self.deck_section = DeckPage(wizard)
        self.notetype_section = NoteTypePage(wizard)
        pickers_layout.addWidget(self.deck_section)
        pickers_layout.addWidget(self.notetype_section)
        layout.addWidget(self.pickers)
        layout.addStretch(1)
        self.pickers.setVisible(False)

        for section in self.sections():
            section.completeChanged.connect(self.completeChanged)
        self.connect_section.reachability_changed.connect(self._on_reachability_changed)

    def sections(self) -> tuple[_WizardSection, ...]:
        return (self.connect_section, self.deck_section, self.notetype_section)

    def initializePage(self) -> None:
        """Show the config's deck and note type, then ask AnkiConnect; an answer fetches both lists."""
        self.deck_section.load_from_config()
        self.notetype_section.load_from_config()
        self.connect_section.initializePage()

    def isComplete(self) -> bool:
        return all(section.isComplete() for section in self.sections())

    def validatePage(self) -> bool:
        return all(section.validatePage() for section in self.sections())

    def stage_current_edits(self) -> None:
        for section in self.sections():
            section.stage_current_edits()

    def _on_reachability_changed(self, reachable: bool) -> None:
        self.pickers.setVisible(reachable)
        if reachable:
            self.deck_section._on_refresh_clicked()
            self.notetype_section._on_refresh_clicked()

    def event(self, event: QEvent | None) -> bool:
        handled = super().event(event)
        if event is not None and event.type() == QEvent.Type.LayoutRequest:
            self._grow_wizard_to_fit()
        return handled

    def _grow_wizard_to_fit(self) -> None:
        """Make the wizard tall enough for what this page shows now.

        QWizard fits itself to a page only when the page is entered, and this
        page grows afterwards: the pickers appear once Anki answers, and the
        guidance and mapping lines fill in later still. Without this they are
        squeezed into the window the connection section alone asked for.
        """
        layout = self.layout()
        if layout is None or not self.isVisible():
            return
        width = self.width()
        needed = layout.totalHeightForWidth(width) if layout.hasHeightForWidth() else layout.totalMinimumSize().height()
        shortfall = needed - self.height()
        if shortfall > 0:
            self._wizard.resize(self._wizard.width(), self._wizard.height() + shortfall)


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

        layout = QVBoxLayout(self)
        # The subtitle is set by _rebuild_catalog_rows, from the kinds the
        # active language's catalog actually offers.
        self.title_label, self.subtitle_label = _add_page_header(layout, self.tr("Recommended Resources"), "")

        self.help_link = QLabel(f'<a href="{RESOURCES_HELP_URL}">{self.tr("What are these resources?")}</a>')
        self.help_link.setOpenExternalLinks(False)
        # Resolved on click: the wizard's language step can change the language after this page is built.
        self.help_link.linkActivated.connect(
            lambda: _open_url(resources_help_url(config_language(self._wizard.working_config())))
        )
        layout.addWidget(self.help_link)

        # _sync_download_button reads _download_running, so it is set before any
        # checkbox exists to toggle.
        self._download_running = False

        # The catalog rows live in a container of their own so a language
        # change can replace them without disturbing what surrounds them.
        self._catalog_rows = QWidget()
        self._catalog_rows_layout = QVBoxLayout(self._catalog_rows)
        self._catalog_rows_layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._catalog_rows)

        self.download_button = ModernButton(self.tr("Download recommended resources"), variant="primary")
        self.download_button.clicked.connect(self._on_download_clicked)
        layout.addWidget(self.download_button)

        self.status_label = QLabel("")
        self.status_label.setObjectName("helper-text")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self._specs_language: str | None = None
        self._specs: list[ResourceSpec] = []
        self.resource_checks: dict[str, QCheckBox] = {}
        self._rebuild_catalog_rows()

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

    def _rebuild_catalog_rows(self) -> None:
        """Offer the active language's catalog, never a hand-listed one.

        Re-derived on every page entry rather than read once when the page was
        built: the wizard's own language step comes before this one, so the
        catalog it has to offer is not known at construction time. A spec added
        to a profile's catalog appears here without touching this page, and ja's
        catalog IS RECOMMENDED_DEFAULT_SET, so the ja wizard is byte-identical
        to the pre-multilanguage one.
        """
        from anki_miner.languages.registry import get_profile  # noqa: PLC0415

        config = self._wizard.working_config()
        # config_language, never the raw field: a stored code whose profile this
        # build cannot supply — a language whitelisted in config but with its
        # engine extra absent — is legal on disk, and raising here would make the
        # whole wizard unconstructible on first run.
        language = config_language(config)
        if language == self._specs_language:
            return
        self._specs_language = language
        self._specs = list(get_profile(language).catalog)
        self.subtitle_label.setText(self._subtitle_for_kinds({spec.kind for spec in self._specs}))
        self.subtitle_label.setVisible(True)

        while (item := self._catalog_rows_layout.takeAt(0)) is not None:
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

        # A regional-variety resource (pt's two frequency lists) starts ticked
        # only for the variety the config holds; the other row stays offered.
        variant = config.script_variant
        self.resource_checks = {}
        for spec in self._specs:
            noun = _RESOURCE_KIND_NOUNS.get(spec.kind)
            label = (
                tr_format(self.tr("%1 — %2"), QCoreApplication.translate("SetupWizard", noun), spec.display_name)
                if noun
                else spec.display_name
            )
            box = QCheckBox(label)
            box.setToolTip(spec.license_note)
            # The toggled connection comes AFTER setChecked: a fresh unchecked
            # box emits toggled the first time it is checked.
            box.setChecked(not spec.variant or spec.variant == variant)
            box.toggled.connect(self._sync_download_button)
            self._catalog_rows_layout.addWidget(box)
            self.resource_checks[spec.id] = box

        # Derived, never assumed: the button was born enabled, and the only
        # thing that ever re-derived it was a checkbox toggling. A language
        # whose catalog is empty has no checkbox to toggle, so it kept an
        # enabled button over a handler that returns silently.
        self._sync_download_button()
        # ko's catalog is empty on purpose (languages/ko/catalog.py): no Korean
        # resource is both redistributable by link and shaped like an importer
        # here. Saying so beats a dead button, and the sentence has to name
        # where the resources DO come from. Cleared again for a language that
        # has a catalog — a status line from the outgoing language's download
        # describes a run that is no longer on screen.
        self.status_label.setText(
            ""
            if self._specs
            else self.tr("No recommended resources for this language. Import a dictionary in Settings → Dictionaries.")
        )

    def _subtitle_for_kinds(self, kinds: set[str]) -> str:
        """Name the optional families this catalog has, and nothing else.

        A whole sentence per combination rather than a stitched-together one:
        the optional clause and the required one share a subject in several
        languages, and a translator handed two fragments cannot make them
        agree. ja carries all three kinds, so its sentence is unchanged and
        keeps its existing translations.
        """
        has_freq = "freq" in kinds
        has_pitch = "pitch" in kinds
        if has_freq and has_pitch:
            return self.tr("Frequency and pitch accent are optional. A dictionary is required.")
        if has_freq:
            return self.tr("Frequency is optional. A dictionary is required.")
        if has_pitch:
            return self.tr("Pitch accent is optional. A dictionary is required.")
        return self.tr("A dictionary is required.")

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
        self._rebuild_catalog_rows()
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

        A walk-away is the one case where that config is the wrong one: the
        slots were picked for a language the close path has already reverted,
        and the chains would silently drop them for not matching.
        """
        from anki_miner.gui.utils.resource_setup import apply_download_summary
        from anki_miner.gui.workers.resource_download_worker import ResourceDownloadSummary

        if self._wizard.is_walking_away():
            return None
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
                self.tr("Download cancelled. Some resources were installed.")
                if summary.succeeded
                else self.tr("Download cancelled. No resources were installed.")
            )
        elif summary.succeeded and not outcome.activated:
            status = self.tr("Imported, but not active — Retry setup")
        elif summary.failed:
            status = (
                tr_format(self.tr("%1 installed, %2 failed."), len(summary.succeeded), len(summary.failed))
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
        self.setFinalPage(True)

        layout = QVBoxLayout(self)
        self.title_label, self.subtitle_label = _add_page_header(
            layout,
            self.tr("Ready to Mine"),
            self.tr("A last check of everything mining needs. You can change it later in Settings."),
        )
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
