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

import contextlib
import html
from collections.abc import Callable
from dataclasses import replace
from functools import partial
from typing import TYPE_CHECKING, Any

from PyQt6.QtCore import QT_TRANSLATE_NOOP, QCoreApplication, QEvent, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QGuiApplication
from PyQt6.QtWidgets import (
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
from anki_miner.gui.utils.language_choices import MiningLanguageChoice, mining_language_choices
from anki_miner.gui.utils.run_off_thread import still_running
from anki_miner.gui.utils.task_lines import format_task_line
from anki_miner.gui.widgets.enhanced import ModernButton
from anki_miner.gui.workers.base_worker import SingleCallWorker
from anki_miner.gui.workers.fetch_workers import (
    FetchDecksWorker,
    FetchFieldsWorker,
    FetchNotetypesWorker,
)
from anki_miner.languages.registry import config_language
from anki_miner.languages.switching import LANGUAGE_SCOPED_FIELDS, switch_language
from anki_miner.services.anki_note_builder import configured_target_field_names
from anki_miner.services.language_pack_installer import ensure_language_packs_on_syspath, language_pack_root
from anki_miner.services.note_presets import FIELD_KEYWORDS, NotePreset, NoteTypeFill, fill_note_type_fields
from anki_miner.utils.i18n import tr_format

if TYPE_CHECKING:
    from anki_miner.config import AnkiMinerConfig
    from anki_miner.gui.controllers.task_registry import TaskRegistry
    from anki_miner.gui.widgets.dialogs.resource_download_dialog import ResourceDownloadSession
    from anki_miner.services.resource_catalog import ResourceSpec
    from anki_miner.services.validation_service import ValidationService

    from .setup_wizard import SetupWizard

ANKICONNECT_URL = "https://ankiweb.net/shared/info/2055492159"
RESOURCES_HELP_URL = "https://github.com/0xzerolight/anki_miner/blob/main/RESOURCES.md"
# Note types that map cleanly (the Lapis/Kiku/Senren presets, or any word + sentence field list).
NOTE_TYPE_HELP_URL = f"{RESOURCES_HELP_URL}#note-types"
#: The note type this app fills out of the box for Japanese (config default).
LAPIS_NOTE_TYPE = "Lapis"
#: Lapis' release page; it carries ``Lapis.apkg`` for Anki's File → Import (B01).
LAPIS_RELEASES_URL = "https://github.com/donkuri/lapis/releases/latest"


def resources_help_url(language: str) -> str:
    """RESOURCES.md at *language*'s section; each heading there is a profile's English name."""
    from anki_miner.languages.registry import get_profile  # noqa: PLC0415

    return f"{RESOURCES_HELP_URL}#{get_profile(language).english_name.lower()}"


#: Family noun per catalog ``kind`` for the readiness lines ("Frequency ready: …").
#: A kind with no entry here has no readiness line.
_RESOURCE_KIND_NOUNS = {
    "dict": QT_TRANSLATE_NOOP("SetupWizard", "Dictionary"),
    "freq": QT_TRANSLATE_NOOP("SetupWizard", "Frequency"),
    "pitch": QT_TRANSLATE_NOOP("SetupWizard", "Pitch accent"),
}

#: Lower-case family nouns for the "Downloads …" sentence (D9), by ``ResourceSpec.kind``.
_KIND_PHRASE_NOUNS = {
    "dict": QT_TRANSLATE_NOOP("SetupWizard", "dictionary"),
    "freq": QT_TRANSLATE_NOOP("SetupWizard", "word frequency"),
    "pitch": QT_TRANSLATE_NOOP("SetupWizard", "pitch accent"),
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


def _isolated(name: str) -> str:
    """Wrap a language's own name in Unicode isolates (FSI … PDI) for a sentence.

    A label takes its paragraph direction from its first strong character, so
    an Arabic or Persian name opening an English sentence would turn the whole
    line right-to-left (the full stop lands on the far left).
    """
    return f"\u2068{name}\u2069"


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

    The list is the one Settings offers (D12): every language this build can
    mine, plus every language one pack download would unlock, marked
    "(download)". Picking one of those and pressing Next switches as usual and
    starts the pack download in the background; the Ready page waits for it
    like it waits for the dictionary. This reverses the old note that a pack
    download "does not belong mid-setup": for a bundle user it was the only way
    to set up in their language.

    A pick reaches the working config on Next, because the deck, note type and
    resource steps read it from there; it survives only an accepted Finish.
    See :meth:`revert_language_change`.
    """

    #: The pack download started, moved or ended; the Ready page redraws.
    pack_state_changed = pyqtSignal()
    #: The pack download ended; the Ready page re-checks.
    pack_finished = pyqtSignal()

    def __init__(self, wizard: SetupWizard) -> None:
        super().__init__(wizard)
        self._wizard = wizard
        # Where the combo opens is a display default, not a
        # user decision. Only an actual pick may rewrite the config, so a
        # config naming a language this build cannot offer -- its engine pack
        # is gone -- is left alone rather than quietly switched to whatever
        # happens to show first.
        self._touched = False
        self._choices: dict[str, MiningLanguageChoice] = {}
        #: The pack being (or last) downloaded from here, and how it is going:
        #: "" (none), "running", "done" or "failed".
        self._pack_code: str | None = None
        self._pack_state = ""
        self._pack_status = ""
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
        for choice in mining_language_choices():
            self._choices[choice.code] = choice
            text = tr_format(self.tr("%1 (download)"), choice.label) if choice.needs_download else choice.label
            self.language_combo.addItem(text, choice.code)
        self.language_combo.currentIndexChanged.connect(self._on_language_changed)
        layout.addWidget(self.language_combo)

        helper = QLabel(self.tr("The deck, note type and dictionary in the next steps follow this choice."))
        helper.setObjectName("helper-text")
        helper.setWordWrap(True)
        layout.addWidget(helper)
        self.download_note = QLabel("")
        self.download_note.setWordWrap(True)
        layout.addWidget(self.download_note)
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
        self._update_download_note()

    def _current_choice(self) -> MiningLanguageChoice | None:
        code = self.language_combo.currentData()
        return self._choices.get(code) if isinstance(code, str) else None

    def _update_download_note(self) -> None:
        choice = self._current_choice()
        if choice is None or not choice.needs_download:
            self.download_note.setText("")
            return
        self.download_note.setText(
            tr_format(
                self.tr(
                    "%1 needs a one-time download of about %2 MB. It starts when you press Next and runs "
                    "while you finish setup."
                ),
                _isolated(choice.native_name),
                choice.download_mb,
            )
        )

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
        choice = self._current_choice()
        if (
            self._touched
            and choice is not None
            and choice.needs_download
            and not self._start_pack_download(choice.code)
        ):
            self.download_note.setText(
                self.tr(
                    "This language's download cannot start from setup. Pick it in Settings → Mining Language after setup."
                )
            )
            return False
        self._write_language_to_config()
        return True

    def _start_pack_download(self, code: str) -> bool:
        """Start (or join) ``code``'s pack download in the background; False when there is no downloader."""
        if self._pack_code == code and self._pack_state in ("running", "done"):
            return True
        tasks = getattr(self._wizard.parent(), "background_tasks", None)
        start = getattr(tasks, "start_language_pack_download", None)
        if tasks is None or not callable(start):
            return False
        running = tasks.language_pack_workers.get(code)
        if still_running(running):
            # Settings is already fetching it: listen to that run instead.
            running.status.connect(self._on_pack_status)
            running.result_ready.connect(self._on_pack_finished)
        else:
            start(code, language_pack_root(code), self._on_pack_status, self._on_pack_finished)
        self._pack_code = code
        self._pack_state = "running"
        self._pack_status = ""
        self.pack_state_changed.emit()
        return True

    def _on_pack_status(self, text: str) -> None:
        self._pack_status = text
        self.pack_state_changed.emit()

    def _on_pack_finished(self, ok: bool, message: str) -> None:
        code = self._pack_code
        self._pack_state = "done" if ok else "failed"
        self._pack_status = message
        if ok and code is not None:
            # Order is load-bearing (see app._connect_language_pack_download):
            # the pack must be importable before anything re-probes the language.
            ensure_language_packs_on_syspath()
            window = self._wizard.parent()
            index_of = getattr(window, "_settings_tab_index", None)
            tabs = getattr(window, "tabs", None)
            if callable(index_of) and tabs is not None and index_of() >= 0:
                notify = getattr(tabs.widget(index_of()), "notify_language_pack_download_finished", None)
                if callable(notify):
                    notify(code)
        self.pack_state_changed.emit()
        self.pack_finished.emit()

    def pack_ready(self) -> bool:
        """False while the working config's language waits on a pack from here."""
        language = self._wizard.working_config().language
        return not (self._pack_code == language and self._pack_state in ("running", "failed"))

    def ready_page_pack_line(self) -> str:
        """The Ready page's rich-text line for a pack that is not in yet."""
        choice = self._choices.get(self._pack_code or "")
        name = _isolated(choice.native_name if choice is not None else (self._pack_code or ""))
        if self._pack_state == "failed":
            failed = tr_format(self.tr("%1 language pack: download failed."), name)
            retry = self.tr("Retry")
            return f'{_html_text(failed)} <a href="pack">{_html_text(retry)}</a>'
        if self._pack_status:
            return _html_text(tr_format(self.tr("%1 language pack: %2"), name, self._pack_status))
        return _html_text(tr_format(self.tr("%1 language pack: downloading…"), name))

    def activate_link(self, href: str) -> None:
        """The Ready page's "pack" link: try the failed pack download again."""
        if href == "pack" and self._pack_code is not None and self._pack_state == "failed":
            self._pack_state = ""
            self._start_pack_download(self._pack_code)

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

        self.deck_combo = QComboBox()
        # B06: a real list. A name Anki does not have is listed as "(not in
        # Anki yet)" instead of typed into an editable box Next then refuses.
        self.deck_combo.setPlaceholderText(self.tr("Pick a deck"))
        self.deck_combo.currentIndexChanged.connect(self._on_index_changed)
        layout.addWidget(self.deck_combo)

        # Normal text size (B06): this line is the instruction, not a footnote.
        # Hidden while empty, so it leaves no gap above the note type.
        self.deck_hint = QLabel("")
        self.deck_hint.setWordWrap(True)
        self.deck_hint.setVisible(False)
        layout.addWidget(self.deck_hint)
        #: False until the first deck answer (or error) lands.
        self._loaded = False

    def initializePage(self) -> None:
        self.load_from_config()
        self.refresh()

    def current_deck(self) -> str:
        """The selected deck's Anki name ("" when nothing is selected)."""
        data = self.deck_combo.currentData()
        return data if isinstance(data, str) else ""

    def select_deck(self, name: str) -> None:
        """Select ``name``; a name Anki does not list gets its own "(not in Anki yet)" item."""
        name = name.strip()
        self.deck_combo.blockSignals(True)
        try:
            index = self.deck_combo.findData(name) if name else -1
            if name and index < 0:
                self.deck_combo.addItem(tr_format(self.tr("%1 (not in Anki yet)"), name), name)
                index = self.deck_combo.count() - 1
            self.deck_combo.setCurrentIndex(index)
        finally:
            self.deck_combo.blockSignals(False)
        self._on_selection_changed()

    def load_from_config(self) -> None:
        """Show the working config's deck without fetching anything."""
        self.select_deck(self._wizard.working_config().anki_deck_name)

    def isComplete(self) -> bool:
        # Decks are no longer auto-created at mine time, so only a deck Anki
        # actually reports can be accepted here. Every path that mutates
        # _fetched_decks must emit completeChanged or Next stays disabled.
        name = self.current_deck()
        return bool(name) and name in self._fetched_decks

    def _on_index_changed(self, _index: int) -> None:
        self._on_selection_changed()

    def _on_selection_changed(self) -> None:
        self._update_deck_hint()
        self.completeChanged.emit()

    def _write_deck_to_config(self) -> None:
        name = self.current_deck()
        if name and name != self._wizard.working_config().anki_deck_name:
            self._wizard.update_working_config(replace(self._wizard.working_config(), anki_deck_name=name))

    def stage_current_edits(self) -> None:
        """Stage editor state without fetching decks."""
        self._write_deck_to_config()

    def validatePage(self) -> bool:
        self._write_deck_to_config()
        return True

    def refresh(self) -> None:
        """Fetch Anki's deck names off the GUI thread (on page entry and every re-check, B02)."""
        if still_running(self._worker):
            return
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
        self._loaded = True
        self._update_deck_hint()
        self.completeChanged.emit()

    def _on_decks_fetched(self, deck_names: object) -> None:
        names = [str(name) for name in deck_names] if isinstance(deck_names, list) else []
        self._fetched_decks = names
        self._loaded = True
        wanted = self.current_deck() or self._wizard.working_config().anki_deck_name
        self.deck_combo.blockSignals(True)
        try:
            self.deck_combo.clear()
            for name in names:
                self.deck_combo.addItem(name, name)
        finally:
            self.deck_combo.blockSignals(False)
        # select_deck emits completeChanged, so Next is re-evaluated once the list lands.
        self.select_deck(wanted)

    def _update_deck_hint(self) -> None:
        name = self.current_deck()
        if not self._loaded:
            text = ""
        elif not self._fetched_decks:
            text = self.tr("Could not load decks. Is Anki running with AnkiConnect?")
        elif not name:
            text = self.tr("Pick a deck.")
        elif name not in self._fetched_decks:
            text = tr_format(
                self.tr(
                    "Anki doesn't have a deck called “%1” yet. In Anki, click Create Deck at the bottom of the "
                    "main window and name it %1, or pick one of your decks above. This page updates when you "
                    "come back."
                ),
                name,
            )
        else:
            text = ""
        self.deck_hint.setText(text)
        self.deck_hint.setVisible(bool(text))


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
        self._notetypes_loaded = False
        # The note type the wizard was opened on: only its mapping is the user's
        # own on a re-run. One they pick here still takes its preset.
        self._opened_note_type = wizard.working_config().anki_note_type

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.heading_label = QLabel(self.tr("Note type"))
        self.heading_label.setObjectName("heading3")
        layout.addWidget(self.heading_label)

        self.notetype_combo = QComboBox()
        # B06: a real list, like the deck combo.
        self.notetype_combo.setPlaceholderText(self.tr("Pick a note type"))
        self.notetype_combo.currentIndexChanged.connect(self._on_index_changed)
        layout.addWidget(self.notetype_combo)

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
        self.refresh()

    def load_from_config(self) -> None:
        """Show the working config's note type without fetching the list."""
        self.select_note_type(self._wizard.working_config().anki_note_type)

    def isComplete(self) -> bool:
        note_type = self.current_note_type()
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

    def current_note_type(self) -> str:
        """The selected note type's Anki name ("" when nothing is selected)."""
        data = self.notetype_combo.currentData()
        return data if isinstance(data, str) else ""

    def select_note_type(self, name: str, *, notify: bool = True) -> None:
        """Select ``name``; a name Anki does not list gets its own "(not in Anki yet)" item.

        ``notify=False`` changes only what is shown, as a signal-blocked edit
        used to: no field fetch, no config write.
        """
        previous = self.current_note_type()
        name = name.strip()
        self.notetype_combo.blockSignals(True)
        try:
            index = self.notetype_combo.findData(name) if name else -1
            if name and index < 0:
                self.notetype_combo.addItem(tr_format(self.tr("%1 (not in Anki yet)"), name), name)
                index = self.notetype_combo.count() - 1
            self.notetype_combo.setCurrentIndex(index)
        finally:
            self.notetype_combo.blockSignals(False)
        if notify and self.current_note_type() != previous:
            self._on_notetype_changed()

    def _on_index_changed(self, _index: int) -> None:
        self._on_notetype_changed()

    def _on_notetype_changed(self) -> None:
        self._record_desired_note_type(self.current_note_type())
        self._fetch_fields()
        self._update_guidance()

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
        name = self.current_note_type()
        if name and name != self._wizard.working_config().anki_note_type:
            self._wizard.update_working_config(replace(self._wizard.working_config(), anki_note_type=name))

    def stage_current_edits(self) -> None:
        """Stage editor state without fetching note-type fields."""
        self._write_notetype_to_config()

    # --- note-type list fetch ---

    def refresh(self) -> None:
        """Fetch Anki's note type names off the GUI thread (on page entry and every re-check, B02)."""
        if still_running(self._notetypes_worker):
            return
        worker = FetchNotetypesWorker(self._wizard.anki_service(), self)
        self._notetypes_worker = worker
        self._wizard.register_worker(worker)
        worker.result_ready.connect(self._on_notetypes_fetched)
        worker.error.connect(self._on_notetypes_error)
        self.completeChanged.emit()
        worker.start()

    def _on_notetypes_fetched(self, model_names: object) -> None:
        names = [str(name) for name in model_names] if isinstance(model_names, list) else []
        self._fetched_note_types = names
        self._notetypes_loaded = True
        # Read the selection before clear(): the automatic re-check (B02) refreshes
        # this list on every window focus, and comparing against the cleared combo
        # would call every refresh a change, drop the known fields and flicker Next.
        previous = self.current_note_type()
        wanted = previous or self._wizard.working_config().anki_note_type
        self.notetype_combo.blockSignals(True)
        try:
            self.notetype_combo.clear()
            for name in names:
                self.notetype_combo.addItem(name, name)
        finally:
            self.notetype_combo.blockSignals(False)
        self.select_note_type(wanted, notify=False)
        if self.current_note_type() != previous:
            self._on_notetype_changed()
        self.completeChanged.emit()
        # Fetch the fields of the selected note type so they fill themselves.
        self._fetch_fields()
        self._update_guidance()

    def _on_notetypes_error(self, _message: str) -> None:
        self._notetypes_loaded = True
        self._update_guidance()
        self.completeChanged.emit()

    # --- field list fetch ---

    def _fetch_fields(self) -> None:
        if not self._accept_field_fetches:
            return
        note_type = self.current_note_type()
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
            current_note_type = self.current_note_type()
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

    @staticmethod
    def _has_mining_shape(field_names: list[str]) -> bool:
        """True if the field list has both a word-ish and a sentence-ish field.

        Normalizes each name the same way the keyword pass does, then
        checks for ANY match against the word and sentence keyword sets.
        """
        word_kw = {kw.lower() for kw in FIELD_KEYWORDS["word"]}
        sentence_kw = {kw.lower() for kw in FIELD_KEYWORDS["sentence"]}
        normalized = {name.lower().replace(" ", "").replace("_", "") for name in field_names}
        return bool(normalized & word_kw) and bool(normalized & sentence_kw)

    def _show_guidance(self, html: str) -> None:
        self.guidance_label.setText(html)
        self.guidance_label.setVisible(True)

    def _hide_guidance(self) -> None:
        self.guidance_label.setVisible(False)
        self.guidance_label.setText("")

    def _presets_apply(self) -> bool:
        """Lapis, Kiku and Senren are Japanese note types; presets ride the ``note_presets`` capability."""
        from anki_miner.languages.registry import get_profile  # noqa: PLC0415

        return "note_presets" in get_profile(config_language(self._wizard.working_config())).capabilities

    def _show_note_type_help(self) -> None:
        """What any note type needs (D10 = B): the app ships none, any one works once mapped.

        The wizard has no field-mapping table: it maps only by name, through
        the keyword pass (`FIELD_KEYWORDS`, whole normalised name), and the
        word falls back to the first field (`_apply_keyword_fill`). So the text
        says the word goes in the first field, names field names that pass
        recognises ("Word", "Sentence", ...) and points to Settings for the
        rest after setup. It never says Skip Setup, which reverts the picked
        language and cancels the dictionary download (WB I1). It never tells the user to build or rename a note type
        (the owner's D10 note: the user picks a note type they like). A fresh
        Anki holds only Basic (Front/Back), which is the common case for the 31
        languages without note presets.
        """
        self._show_guidance(
            tr_format(
                self.tr(
                    "Any note type works once its fields are mapped. Pick one of your note types: Anki Miner "
                    "puts the word in its first field and fills the fields it recognises by name, such as Word, "
                    "Sentence, Reading, Definition, Picture and audio. You can change which field gets what "
                    "in Settings → Cards & Anki after setup. "
                    '<a href="%1">Which fields can Anki Miner fill?</a>'
                ),
                NOTE_TYPE_HELP_URL,
            )
        )

    def _update_guidance(self) -> None:
        """Say what to pick when the note type cannot hold mined cards (B01, D10).

        Nothing until the list has loaded. A present note type with no word or
        sentence field, or a missing one other than Lapis, gets what any note
        type needs. A missing Lapis under a language with presets gets where to
        download it: the config default is Lapis, and a brand-new Anki lacks it.
        """
        if not self._notetypes_loaded:
            self._hide_guidance()
            return
        name = self.current_note_type()
        if name and name in self._fetched_note_types:
            fields_known = self._field_names_note_type == name and bool(self._field_names)
            if fields_known and not self._has_mining_shape(self._field_names):
                self._show_note_type_help()
            else:
                self._hide_guidance()
            return
        if name == LAPIS_NOTE_TYPE and self._presets_apply():
            self._show_guidance(
                tr_format(
                    self.tr(
                        "Anki Miner fills a note type called Lapis. Your Anki doesn't have it yet. "
                        '<a href="%1">Get Lapis</a> (free), then in Anki choose File → Import and pick the file. '
                        "This page updates when you come back."
                    ),
                    LAPIS_RELEASES_URL,
                )
            )
            return
        self._show_note_type_help()

    def _on_guidance_link_activated(self, url: str) -> None:
        if url in (NOTE_TYPE_HELP_URL, LAPIS_RELEASES_URL):
            _open_url(url)

    def _sanitize_field_mappings(self, note_type: str, field_names: list[str]) -> None:
        config = self._wizard.working_config()
        actual_fields = set(field_names)
        sanitized_fields = dict.fromkeys(FIELD_KEYWORDS, "")
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

        The same helper as Settings' "Fill in automatically" (D13): a note type
        it recognises (Lapis, Kiku, Senren) takes its preset, which also carries
        the pitch format and card markers; anything else gets the keyword pass,
        which fills only keys that are still empty here.
        """
        from anki_miner.languages.registry import get_profile  # noqa: PLC0415

        names = self._field_names
        if not names or self._field_names_note_type != note_type:
            return
        config = self._wizard.working_config()
        # A re-run (Tools, System Health's Fix) re-fetches the note type it was
        # opened on. When setup already ran and that mapping already works, it is
        # the user's own: re-applying a preset would silently switch features
        # they turned off (an empty field) and change later cards, and every
        # close path persists the working config. First runs, a note type picked
        # here and a mapping that does not work yet still fill (D8/D13).
        if config.first_run_setup_done and note_type == self._opened_note_type and self.isComplete():
            self._update_guidance()
            self._show_field_problem()
            self.completeChanged.emit()
            return
        fill = fill_note_type_fields(
            names,
            allow_presets=self._presets_apply(),
            extra_specs=get_profile(config_language(config)).extra_card_fields,
        )
        if fill.preset is not None:
            self._apply_preset(fill.preset)
        else:
            self._apply_keyword_fill(fill)
        self._update_guidance()
        self._show_field_problem()
        self.completeChanged.emit()

    def _apply_keyword_fill(self, fill: NoteTypeFill) -> None:
        """Stage the keyword pass: fill every still-empty key it matched."""
        mapped = dict(fill.fields)
        mapped.update(fill.extra_fields)
        config = self._wizard.working_config()
        merged = dict(config.anki_fields)
        for key, value in mapped.items():
            if value and not merged.get(key):
                merged[key] = value
        # The word goes in the first field when no name gave it one (WB I1): a
        # fresh Anki holds only Basic (Front/Back), and without this Next stays
        # off, leaving Skip Setup, which reverts the picked language and cancels
        # the dictionary download. A first field another key already holds stays
        # with that key.
        first = self._field_names[0] if self._field_names else ""
        if first and not merged.get("word") and first not in merged.values():
            merged["word"] = first
            mapped["word"] = first
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
        # B02: while AnkiConnect is unreachable, ask again every 3 s; stop once
        # it answers or the user leaves the page.
        self._poll = QTimer(self)
        self._poll.setInterval(3000)
        self._poll.timeout.connect(self._on_poll)

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
            self.deck_section.refresh()
            self.notetype_section.refresh()
        self._sync_poll()

    def recheck(self) -> None:
        """B02: ask AnkiConnect again; an answer re-fetches the deck and note-type lists."""
        self.connect_section.recheck()

    def on_shown(self) -> None:
        self._sync_poll()

    def _sync_poll(self) -> None:
        wanted = (
            not self._wizard.is_closing()
            and self._wizard.currentPage() is self
            and not self.connect_section.isComplete()
        )
        if not wanted:
            self._poll.stop()
        elif not self._poll.isActive():
            self._poll.start()

    def _on_poll(self) -> None:
        self._sync_poll()
        if self._poll.isActive():
            self.connect_section.recheck()

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
    """The dictionary step: one sentence, one Download button, no checklist (D9).

    A dictionary is required: without one every mined card comes out with no
    definition (D26). Next opens once a download has started or a dictionary
    can already answer, so the transfer runs while the user deals with Anki;
    Finish on the Ready page still waits for the dictionary. Progress is drawn
    here and on the Ready page from the TaskRegistry, not in a second window.
    **Skip Setup** remains for anyone who cannot download right now.

    Everything the language's catalogue offers is fetched, minus the other
    regional variety (pt's two frequency lists): the checklist a newcomer could
    not judge is gone, and anything unwanted can be removed in Settings.
    """

    #: The download started, moved or ended; the Ready page redraws its line.
    download_state_changed = pyqtSignal()
    #: The download ended, however it ended; the Ready page re-checks (B02).
    download_finished = pyqtSignal()

    def __init__(self, wizard: SetupWizard) -> None:
        super().__init__(wizard)
        self._dictionary_ready = False
        # _sync_download_button reads these, so they exist before any widget.
        self._download_running = False
        #: The mining language the current/last run was started for; its slots
        #: belong to that language's chains only. None before any run.
        self._download_language: str | None = None
        #: How the last run ended, for the Ready page: "", "failed" or "cancelled".
        self._download_ending = ""
        #: The registry's latest detailed line for the running download.
        self._progress_text = ""
        self._registry: TaskRegistry | None = None
        # Retained past the run's end: a started run is what opens Next
        # (isComplete).
        self._session: ResourceDownloadSession | None = None
        self._specs_language: str | None = None
        self._specs: list[ResourceSpec] = []

        layout = QVBoxLayout(self)
        self.title_label, self.subtitle_label = _add_page_header(
            layout,
            self.tr("Get a Dictionary"),
            self.tr("Mined cards take their definitions from an offline dictionary. This step is required."),
        )

        #: "Downloads JMdict (dictionary), …", built from the catalogue; licences in its tooltip.
        self.contents_label = QLabel("")
        self.contents_label.setWordWrap(True)
        layout.addWidget(self.contents_label)

        button_row = QHBoxLayout()
        self.download_button = ModernButton(self.tr("Download"), variant="primary")
        self.download_button.clicked.connect(self._on_download_clicked)
        button_row.addWidget(self.download_button)
        button_row.addStretch(1)
        layout.addLayout(button_row)

        self.status_label = QLabel("")
        self.status_label.setObjectName("helper-text")
        self.status_label.setWordWrap(True)
        self.status_label.setTextFormat(Qt.TextFormat.RichText)
        self.status_label.setOpenExternalLinks(False)
        self.status_label.linkActivated.connect(self.activate_link)
        layout.addWidget(self.status_label)

        # Kept apart from status_label: one reports how the *download* went,
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
        self._language_gate_pairs: list[tuple[QWidget, str]] = [(self.pitch_label, "pitch")]
        self._apply_language_gate()

        self.help_link = QLabel(f'<a href="{RESOURCES_HELP_URL}">{self.tr("What are these resources?")}</a>')
        self.help_link.setOpenExternalLinks(False)
        # Resolved on click: the wizard's language step can change the language after this page is built.
        self.help_link.linkActivated.connect(
            lambda: _open_url(resources_help_url(config_language(self._wizard.working_config())))
        )
        layout.addWidget(self.help_link)
        layout.addStretch(1)

        self._rebuild_catalog_rows()

    def selected_specs(self) -> list[ResourceSpec]:
        """What Download fetches: the catalogue, minus the other regional variety (D9).

        A regional-variety resource (pt's two frequency lists) is fetched only
        for the variety the config holds. That used to be a pre-ticked checkbox;
        with no checklist it is a silent default, and the other list stays one
        Add away in Settings → Frequency.
        """
        variant = self._wizard.working_config().script_variant
        return [spec for spec in self._specs if not spec.variant or spec.variant == variant]

    def _rebuild_catalog_rows(self) -> None:
        """Describe the active language's catalogue, never a hand-listed one.

        Re-derived on every page entry rather than once at construction: the
        wizard's language step comes before this page, so the catalogue is not
        known when the page is built. A spec added to a profile's catalogue
        appears here with no page edit.
        """
        from anki_miner.languages.registry import get_profile  # noqa: PLC0415

        config = self._wizard.working_config()
        # config_language, never the raw field: a stored code whose profile this
        # build cannot supply is legal on disk, and raising here would make the
        # whole wizard unconstructible on first run.
        language = config_language(config)
        if language == self._specs_language:
            return
        self._specs_language = language
        self._specs = list(get_profile(language).catalog)
        if not self._download_running:
            # A finished run for the outgoing language describes nothing on screen now.
            self._session = None
            self._download_ending = ""
            self.status_label.clear()
            self.status_label.setToolTip("")
        specs = self.selected_specs()
        if specs:
            self.contents_label.setText(self._contents_sentence(specs))
            self.contents_label.setToolTip("\n".join(spec.license_note for spec in specs))
        else:
            # No shipped language has an empty catalogue; this is the fallback
            # for one that would, and isComplete() then does not block Next.
            self.contents_label.setText(
                self.tr("No recommended resources for this language. Import a dictionary in Settings → Dictionaries.")
            )
            self.contents_label.setToolTip("")
        self.download_button.setVisible(bool(specs))
        self._sync_download_button()

    def _contents_sentence(self, specs: list[ResourceSpec]) -> str:
        """Build the one sentence, e.g. "Downloads JMdict (dictionary) and Kanjium (pitch accent)"."""
        groups: list[str] = []
        for kind in ("dict", "freq", "pitch"):
            names = [spec.display_name for spec in specs if spec.kind == kind]
            if not names:
                continue
            noun = QCoreApplication.translate("SetupWizard", _KIND_PHRASE_NOUNS[kind])
            groups.append(tr_format(self.tr("%1 (%2)"), self._and_list(names), noun))
        return tr_format(self.tr("Downloads %1."), self._and_list(groups))

    def _and_list(self, items: list[str]) -> str:
        """Join as "A", "A and B" or "A, B and C"; the joining word is translated."""
        if len(items) == 1:
            return items[0]
        return tr_format(self.tr("%1 and %2"), ", ".join(items[:-1]), items[-1])

    def _sync_download_button(self) -> None:
        """Nothing to fetch, or a run already going, is not a run."""
        self.download_button.setEnabled(bool(self.selected_specs()) and not self._download_running)

    def _apply_language_gate(self) -> None:
        """Re-derive the paired rows' visibility from the active language.

        Re-applied on every page entry because the wizard can be re-entered
        after a language switch. The gate is two-way and owns the whole
        visibility of a paired widget, so a switch back re-shows the row.
        """
        from anki_miner.gui.utils.language_gate import apply_language_gate  # noqa: PLC0415
        from anki_miner.languages.registry import get_profile  # noqa: PLC0415

        capabilities = get_profile(config_language(self._wizard.working_config())).capabilities
        apply_language_gate(self._language_gate_pairs, capabilities)

    def initializePage(self) -> None:
        """Ask the disk, every time the page is entered."""
        self._rebuild_catalog_rows()
        self._apply_language_gate()
        self._recheck_resources()

    def recheck(self) -> None:
        """B02: coming back to the wizard asks the disk again (not while downloading)."""
        if not self._download_running:
            self._recheck_resources()

    def download_running(self) -> bool:
        """True while the wizard's own download is going (T2.14 holds Finish for it)."""
        return self._download_running

    def isComplete(self) -> bool:
        # D9: a started download is enough to move on; the Ready page waits for
        # it. Nothing to download is nothing to block on: a language with an
        # empty catalogue has no button, so the gate would leave Skip Setup as
        # the only exit.
        return self._dictionary_ready or self._session is not None or not self._specs

    # --- live dictionary readiness ---

    def _recheck_resources(self) -> None:
        """Probe off-thread what all three resource families can do.

        Off-thread because the probe scans three resource folders, any of which
        can be a slow network path. One worker, not three: the base class keeps
        a single ``_live_check`` as its generation counter.
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
        self.dictionary_label.setText(self._dictionary_line(bool(ok), message))

        # Nouns come from the shared "SetupWizard"-context table, so the two
        # readiness lines cannot drift from each other in translation.
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

    def _dictionary_line(self, ok: bool, message: str) -> str:
        """The dictionary verdict in the wizard's own words where it can say more (B11)."""
        if ok:
            return tr_format(self.tr("Dictionary ready: %1"), message)
        if self._dictionary_not_downloaded():
            return self.tr("Dictionary: not downloaded yet (required)")
        return message

    def _dictionary_not_downloaded(self) -> bool:
        """True when no dictionary is enabled, or every enabled one is one this page downloads.

        A fresh config already names the recommended dictionary before anything
        is on disk; the service's sentence for that ("Download it with Tools →
        …") is right for the main window but not for a page with its own button.
        """
        config = self._wizard.working_config()
        enabled = {e.dict_id for e in config.dictionary_chain if e.kind == "indexed" and e.enabled and e.dict_id}
        offered = {spec.id for spec in self._specs if spec.kind == "dict"}
        return enabled <= offered

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

    # --- the download ---

    def _on_download_clicked(self) -> None:
        """Start the download in the background and hand the page back at once.

        No window (D9): progress is read from the TaskRegistry and drawn here
        and on the Ready page. Worker ownership goes to the wizard, whose close
        path cancels every registered worker and waits for its native thread.
        """
        from anki_miner.gui.controllers.task_registry import TaskRegistry  # noqa: PLC0415
        from anki_miner.gui.widgets.dialogs.resource_download_dialog import start_resource_download  # noqa: PLC0415

        if self._download_running:
            return
        self.status_label.clear()
        self.status_label.setToolTip("")
        specs = self.selected_specs()
        if not specs:
            return
        registry = getattr(self._wizard.parent(), "task_registry", None)
        session = start_resource_download(
            self,
            self._wizard.working_config(),
            activate=self._activate_resources,
            release_resources=self._wizard._release_resources,
            task_registry=registry,
            adopt_worker=self._wizard.register_worker,
            specs=specs,
            show_window=False,
        )
        if session is None:
            return
        self._session = session
        self._download_language = config_language(self._wizard.working_config())
        self._download_running = True
        self._download_ending = ""
        self._progress_text = ""
        self._sync_download_button()
        session.finished.connect(self._on_download_finished)
        if isinstance(registry, TaskRegistry):
            self._registry = registry
            registry.snapshot_changed.connect(self._on_registry_snapshot)
        self._render_progress()
        self.completeChanged.emit()
        self.download_state_changed.emit()

    def _on_registry_snapshot(self, task_id: str) -> None:
        registry = self._registry
        session = self._session
        if registry is None or session is None or not self._download_running or task_id != session.task_id:
            return
        snapshot = registry.snapshot(task_id)
        if snapshot is None:
            return
        self._progress_text = format_task_line(snapshot)
        self._render_progress()
        self.download_state_changed.emit()

    def _render_progress(self) -> None:
        if not self._download_running:
            return
        progress = self._progress_text or self.tr("Starting…")
        cancel = self.tr("Cancel")
        self.status_label.setText(
            _html_text(tr_format(self.tr("Downloading: %1"), progress)) + f' <a href="cancel">{_html_text(cancel)}</a>'
        )

    def _disconnect_registry(self) -> None:
        registry = self._registry
        self._registry = None
        if registry is not None:
            with contextlib.suppress(TypeError, RuntimeError):
                registry.snapshot_changed.disconnect(self._on_registry_snapshot)

    def activate_link(self, href: str) -> None:
        """This page's and the Ready page's inline links: cancel, or (re)start the download."""
        if href == "cancel":
            if self._session is not None and self._download_running:
                self._session.cancel()
        elif href == "download":
            self._on_download_clicked()

    def _activate_resources(self, summary: object) -> AnkiMinerConfig | None:
        """Fold a completed summary into the wizard's working config.

        Read from ``working_config()`` at activation time, never from a config
        captured when the download started: the user picks the deck and note
        type on the Anki page while the transfer runs.

        A walk-away is the one case where that config is the wrong one: the
        slots were picked for a language the close path has already reverted,
        and the chains would silently drop them for not matching. A language
        changed inside the wizard (Back, another language, Next) while the run
        was going is the same case.
        """
        from anki_miner.gui.utils.resource_setup import apply_download_summary  # noqa: PLC0415
        from anki_miner.gui.workers.resource_download_worker import ResourceDownloadSummary  # noqa: PLC0415

        if self._wizard.is_walking_away():
            return None
        if (
            self._download_language is not None
            and config_language(self._wizard.working_config()) != self._download_language
        ):
            return None
        if not isinstance(summary, ResourceDownloadSummary) or not summary.succeeded:
            return None
        new_config = apply_download_summary(self._wizard.working_config(), summary)
        self._wizard.update_working_config(new_config)
        return new_config

    def _on_download_finished(self, outcome: object) -> None:
        """Report the run's real ending, then ask the disk again.

        The session runs with no window, so the window's **Retry setup** button
        never exists and this fires once per run. The per-item detail the
        download window used to show goes in the status line's tooltip.
        """
        from anki_miner.gui.widgets.dialogs.resource_download_dialog import (  # noqa: PLC0415
            ResourceDownloadOutcome,
            result_lines,
        )

        self._download_running = False
        self._progress_text = ""
        self._disconnect_registry()
        # Not setEnabled(True): the button follows what there is to fetch.
        self._sync_download_button()
        if not isinstance(outcome, ResourceDownloadOutcome):
            self._download_ending = "failed"
            self.status_label.setText(self.tr("The download stopped before it finished."))
            self.status_label.setToolTip("")
        else:
            summary = outcome.summary
            if summary.cancelled:
                self._download_ending = "cancelled"
                status = (
                    self.tr("Download cancelled. Some resources were installed.")
                    if summary.succeeded
                    else self.tr("Download cancelled. No resources were installed.")
                )
            elif summary.succeeded and not outcome.activated:
                # No window here (show_window=False), so there is no "Retry
                # setup" button to point at: Download runs the whole thing again.
                self._download_ending = "failed"
                status = self.tr("Imported, but not switched on. Press Download to try again.")
            elif summary.failed:
                self._download_ending = "failed"
                status = (
                    tr_format(self.tr("%1 installed, %2 failed."), len(summary.succeeded), len(summary.failed))
                    if summary.succeeded
                    else self.tr("No resources were installed.")
                )
            else:
                self._download_ending = ""
                status = self.tr("Resources installed.")
            self.status_label.setText(status)
            self.status_label.setToolTip("\n".join(result_lines(summary)))
        # Re-ask rather than infer: a summary saying the dictionary imported is
        # not the same claim as the chain being able to answer with it.
        self._recheck_resources()
        self.completeChanged.emit()
        self.download_state_changed.emit()
        self.download_finished.emit()

    def ready_page_dictionary_line(self) -> str:
        """The Ready page's rich-text line while the dictionary is missing or the download runs (D9).

        While the run is going the line names the downloads, not the
        dictionary: JMdict is item 1 of 4, so the dictionary can already be
        ready while JPDB, Jiten and Kanjium are still coming, and T2.14 holds
        Finish until they are (Finish closes the wizard, and closing cancels
        every worker it owns). Links ("download") come back through
        :meth:`activate_link`.
        """
        if not self._specs:
            return _html_text(self.tr("Dictionary: none installed. Add one in Settings → Dictionaries after setup."))
        if self._download_running:
            if self._progress_text:
                return _html_text(tr_format(self.tr("Downloads: still running — %1"), self._progress_text))
            return _html_text(self.tr("Downloads: still running…"))
        if self._download_ending == "failed":
            failed = self.tr("Dictionary: download failed.")
            retry = self.tr("Retry")
            return f'{_html_text(failed)} <a href="download">{_html_text(retry)}</a>'
        missing = self.tr("Dictionary: not downloaded yet (required)")
        download = self.tr("Download")
        return f'{_html_text(missing)} <a href="download">{_html_text(download)}</a>'


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
    """Ready: re-check everything mining needs; list only what is missing (B04).

    Finish ("Open Video Mining", D26) stays disabled until every required check
    passes and the wizard's download has ended. With everything in place the page says what to do next; otherwise
    it lists only the missing items, in plain words, each with its way forward.
    It re-checks on entry, when the wizard window becomes active again, and
    when the dictionary download ends (B02).
    """

    def __init__(self, wizard: SetupWizard) -> None:
        super().__init__(wizard)
        self._results: dict[str, bool] = {}
        self.setFinalPage(True)

        layout = QVBoxLayout(self)
        self.title_label, self.subtitle_label = _add_page_header(layout, self.tr("Ready to Mine"), "")
        self.summary_label = QLabel("")
        self.summary_label.setWordWrap(True)
        self.summary_label.setTextFormat(Qt.TextFormat.RichText)
        self.summary_label.setOpenExternalLinks(False)
        self.summary_label.linkActivated.connect(self._on_link)
        layout.addWidget(self.summary_label)
        layout.addStretch(1)

        # The dictionary line follows the download live, and its end re-checks.
        wizard.resources_page.download_state_changed.connect(self._redraw)
        wizard.resources_page.download_finished.connect(self._on_download_finished)
        if wizard.language_page is not None:
            wizard.language_page.pack_state_changed.connect(self._redraw)
            wizard.language_page.pack_finished.connect(self._on_download_finished)

    def isComplete(self) -> bool:
        # Finish closes the wizard, and closing cancels every worker it owns
        # (SetupWizard.done), so it waits for the whole download (T2.14) and
        # for a language pack started from the first page.
        checks_pass = all(self._results.get(name, False) for name in _FINAL_CHECKS)
        language_page = self._wizard.language_page
        return (
            checks_pass
            and not self._wizard.resources_page.download_running()
            and (language_page is None or language_page.pack_ready())
        )

    def initializePage(self) -> None:
        """Run one fresh readiness sweep; render it when it lands."""
        previous_check = self._live_check
        if still_running(previous_check):
            assert previous_check is not None
            previous_check.cancel()
        self._live_check = None
        self._start_sweep()

    def recheck(self) -> None:
        """B02: re-run the sweep when the wizard window becomes active again."""
        self._start_sweep()

    def _start_sweep(self) -> None:
        if still_running(self._live_check):
            return
        self._results = {}
        self.summary_label.setText(self.tr("Checking your setup..."))
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
        self.completeChanged.emit()

    def _on_sweep_error(self, message: str) -> None:
        if not self._is_live_check():
            return
        self._results = {}
        self.summary_label.setText(_html_text(message))
        self.completeChanged.emit()

    def _redraw(self) -> None:
        if self._results:
            self.summary_label.setText(self._summary_html())
        # A download starting or ending moves the Finish gate (isComplete).
        self.completeChanged.emit()

    def _on_download_finished(self) -> None:
        if self._wizard.currentPage() is self:
            # Cancel-and-restart, not _start_sweep: a sweep already in flight
            # bound the validation service before the new dictionary chain was
            # staged, and _start_sweep would skip while it runs, leaving a stale
            # "dictionary missing" and Finish disabled.
            self.initializePage()
        else:
            self._redraw()

    def _on_link(self, href: str) -> None:
        if href == "pack" and self._wizard.language_page is not None:
            self._wizard.language_page.activate_link(href)
            return
        self._wizard.resources_page.activate_link(href)

    def _summary_html(self) -> str:
        results = self._results
        downloading = self._wizard.resources_page.download_running()
        if self.isComplete():
            return _html_text(
                self.tr(
                    "You're ready. Pick a video and its subtitle file, then press Mine Episode. Books, manga "
                    "and subtitles are under Reading, audiobooks under Audiobooks, tools under Utilities. "
                    "Press F1 any time for the Usage Guide."
                )
            )
        cfg = self._wizard.working_config()
        lines: list[str] = []
        if not results.get("ankiconnect", False):
            # The deck, note-type and field checks were never asked (_final_sweep).
            lines.append(_html_text(self.tr("Anki isn't reachable. Open Anki.")))
        else:
            if not results.get("deck", False):
                lines.append(
                    _html_text(
                        tr_format(
                            self.tr("Anki has no deck called “%1”. Go back to the Anki step and pick one."),
                            cfg.anki_deck_name,
                        )
                    )
                )
            if not results.get("note_type", False):
                lines.append(
                    _html_text(
                        tr_format(
                            self.tr("Anki has no note type called “%1”. Go back to the Anki step and pick one."),
                            cfg.anki_note_type,
                        )
                    )
                )
            elif not results.get("fields", False):
                lines.append(
                    _html_text(
                        self.tr(
                            "The card fields don't match the note type. Go back to the Anki step and pick it again."
                        )
                    )
                )
        if not results.get("dictionary", False) or downloading:
            # While the run goes this reads "Downloads: still running — …".
            lines.append(self._wizard.resources_page.ready_page_dictionary_line())
        language_page = self._wizard.language_page
        if language_page is not None and not language_page.pack_ready():
            lines.insert(0, language_page.ready_page_pack_line())
        intro = _html_text(self.tr("Before you can mine:"))
        return intro + "<br>" + "<br>".join(f"• {line}" for line in lines)
