"""Mining-language selection and the packs a language needs.

Its own destination rather than a section of Filtering: the switcher is not a
filter, and every language added after ja brings plumbing of its own -- an
engine probe, a model pack, a status line -- that would otherwise pile onto a
page users open to set frequency bands. The per-language *filtering* options
(kana, hangul, wordsets, character set) stay in Filtering, where they read as
filters.

Deliberately outside ``SettingsTab._save_panels`` and ``_wire_edit_signals``:
the panel writes no config field. Picking a language proposes a guarded switch
which commits its own config, so arming the autosave debounce here would save
the pre-switch panel state on top of it.
"""

from dataclasses import dataclass

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QWidget

from anki_miner.gui.resources.styles import SPACING
from anki_miner.gui.utils.language_choices import (
    available_mining_languages,
    mining_language_display_name,
    mining_language_english_name,
)
from anki_miner.gui.utils.language_gate import field_row_widgets
from anki_miner.gui.widgets.base import FormPanel
from anki_miner.gui.widgets.enhanced import ModernButton
from anki_miner.languages import AVAILABLE_LANGUAGES
from anki_miner.languages.pack_spec import LanguagePack
from anki_miner.languages.registry import config_language
from anki_miner.utils.i18n import tr_format


def pack_already_importable(pack: LanguagePack) -> bool:
    """Return True when every required package of *pack* already imports here.

    The site-packages tier alone: a pip install with the language's extra needs
    no pack at all, and offering it a download would be noise. Module-level so
    the rows can be probed (and stubbed) without importing an engine —
    ``find_spec`` answers without executing one.
    """
    from importlib.util import find_spec

    for comp in pack.components:
        if not comp.required:
            continue
        try:
            if find_spec(comp.import_name) is None:
                return False
        except (ImportError, ValueError):
            return False
    return True


def pack_on_disk(code: str, pack: LanguagePack) -> bool:
    """Return True when any of *pack*'s components is extracted under the app home.

    The DISK tier alone, so a row that has just finished downloading stays on
    screen to report itself installed instead of vanishing the moment its
    packages become importable. Covers the legacy ``ko_model/`` directory too,
    which ``component_path`` reads as a fallback.
    """
    from anki_miner.services.language_pack_installer import component_path

    return any(component_path(code, comp.import_name) is not None for comp in pack.components)


@dataclass(frozen=True)
class LanguagePackRow:
    """The widgets of one language's pack row, and the size it advertises.

    Keyed by code in ``language_pack_rows``, so the row carries no code of its
    own.
    """

    approx_download_mb: int
    button: ModernButton
    status_label: QLabel
    #: The whole form row, label included, hidden and shown as one.
    widgets: tuple[QWidget, ...]


class MiningLanguageSettingsPanel(FormPanel):
    """Panel for the mining language and the packs a language needs.

    Signals:
        mining_language_requested: Emitted when the user picks another mining
            language. The window runs the guard and decides.
        language_pack_download_requested: Emitted with a language code when the
            user asks for that language's pack. The download itself is owned by
            the caller.
    """

    ANCHOR_NAMESPACE = "mining_language"

    mining_language_requested = pyqtSignal(str)  # proposes a switch; never commits one
    language_pack_download_requested = pyqtSignal(str)  # asks the caller to fetch one language's pack

    def __init__(self, parent=None):
        """Initialize the mining language settings panel."""
        super().__init__("Mining Language", parent=parent)
        self._setup_fields()

    def _setup_fields(self) -> None:
        """Set up the panel fields."""
        self.add_section(self.tr("Language"))

        self.mining_language_combo = QComboBox()
        for code, display_name in available_mining_languages():
            self.mining_language_combo.addItem(display_name, code)
        self.mining_language_combo.currentIndexChanged.connect(self._on_mining_language_changed)
        self.add_field(
            self.tr("Mining Language"),
            self.mining_language_combo,
            helper=self.tr(
                "The language you mine. Separate from the interface language "
                "(Settings -> Appearance & Language). Switching swaps dictionaries, "
                "filters, deck and card fields to that language's own settings."
            ),
        )

        self._setup_language_pack_rows()

        self.add_stretch()

    def set_mining_language(self, code: str) -> None:
        """Point the combo at ``code`` without proposing a switch.

        Signals blocked: this runs from ``load_from_config`` and from the
        window's re-point after a REFUSED switch, and an emit there would ask
        for the switch that was just refused.
        """
        index = self.mining_language_combo.findData(code)
        if index < 0:
            return
        self.mining_language_combo.blockSignals(True)
        try:
            self.mining_language_combo.setCurrentIndex(index)
        finally:
            self.mining_language_combo.blockSignals(False)

    def _on_mining_language_changed(self, index: int) -> None:
        """Propose a switch. The window decides, and re-points this combo."""
        code = self.mining_language_combo.itemData(index)
        if isinstance(code, str) and code:
            self.mining_language_requested.emit(code)

    # ------------------------------------------------------------------
    # Language pack downloads (services/language_pack_installer.py)
    # ------------------------------------------------------------------

    def _setup_language_pack_rows(self) -> None:
        """Build one download row per language that ships a pack manifest.

        Under the selector the packs unlock, not beside the transcription packs:
        the frozen bundle ships Korean and Chinese without the engines and models
        they mine with, and the availability probe gates on exactly those — so
        until a pack lands, its language is not even in the combo above. These
        rows are the only thing on screen that explains where it went.

        Manifest-driven rather than one hand-written row per language: the
        bespoke Korean row this replaced would have been copied for Chinese and
        again for every language after it.
        """
        from anki_miner.services.language_pack_installer import load_pack

        self.language_pack_rows: dict[str, LanguagePackRow] = {}
        #: Codes with a download in flight. Per code, not per panel: two packs
        #: can be fetched at once and each row owns its own button.
        self._language_pack_active: set[str] = set()

        for code in AVAILABLE_LANGUAGES:
            pack = load_pack(code)
            if pack is None:
                continue  # ja: its engine is bundled, so there is nothing to offer
            self.language_pack_rows[code] = self._build_language_pack_row(code, pack)
            self._refresh_language_pack_row(code)

    def _build_language_pack_row(self, code: str, pack: LanguagePack) -> LanguagePackRow:
        """Build (and register) one language's download row."""
        display_name = mining_language_display_name(code)
        # Every string on the row is the native name, so "Korean" reached
        # nothing. English is not translated and never changes, so it is read
        # here rather than re-resolved on every search.
        english_name = mining_language_english_name(code)

        button = ModernButton(tr_format(self.tr("Download %1 pack"), display_name), variant="secondary")
        button.setToolTip(
            tr_format(
                self.tr("Download the engine and data Anki Miner needs to mine %1, into its own folder."),
                display_name,
            )
        )
        # ``code`` is this call's own parameter, so the closures below capture one
        # row's code each -- no late-binding default argument needed.
        button.clicked.connect(lambda: self._on_language_pack_download_clicked(code))

        status_label = QLabel("")
        status_label.setObjectName("settings-save-status")

        container = QWidget()
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(SPACING.xs)
        row.addWidget(button)
        row.addWidget(status_label)
        row.addStretch()
        self.add_field(
            display_name,
            container,
            anchor=f"language_pack_{code}",
            anchor_focus=button,
            anchor_text=lambda: (button.text(), button.toolTip(), english_name),
        )
        # The whole row (label included) disappears where the pack buys nothing.
        # NOT language-gated: a row is about a language the user is not on yet,
        # so a capability gate would hide the one control that gets them there.
        return LanguagePackRow(
            approx_download_mb=pack.approx_download_mb,
            button=button,
            status_label=status_label,
            widgets=field_row_widgets(self, container),
        )

    def _refresh_language_pack_row(self, code: str) -> None:
        """Show, hide and label one row from what this install actually has."""
        from anki_miner.services.language_pack_installer import is_installed, load_pack, pack_supported

        row = self.language_pack_rows[code]
        pack = load_pack(code)
        # Visible when the pack has something to offer: either the packages are
        # not importable yet, or one is already extracted under the app home —
        # the second clause is what keeps a finished download on screen to report
        # itself installed. A pip install with the language's extra satisfies the
        # first and owns no pack directory, so it sees no row at all.
        visible = (
            pack is not None
            and pack_supported(code)
            and (not pack_already_importable(pack) or pack_on_disk(code, pack))
        )
        for widget in row.widgets:
            widget.setVisible(visible)
        if not visible:
            return
        if code in self._language_pack_active:
            row.button.setEnabled(False)
            return
        installed = is_installed(code)
        row.button.setEnabled(not installed)
        row.status_label.setText(
            self.tr("Installed")
            if installed
            else tr_format(self.tr("Not installed - about %1 MB download"), str(row.approx_download_mb))
        )

    def _on_language_pack_download_clicked(self, code: str) -> None:
        """Guard against a second press and ask the caller to run the download."""
        if code in self._language_pack_active:
            return
        self._language_pack_active.add(code)
        self.language_pack_rows[code].button.setEnabled(False)
        self.language_pack_download_requested.emit(code)

    def set_language_pack_status(self, code: str, text: str) -> None:
        """Show a status line on *code*'s row, if it has one.

        Silent for a language with no row: the status arrives from a background
        worker, and a language with no pack manifest is not a failure to report.
        """
        row = self.language_pack_rows.get(code)
        if row is not None:
            row.status_label.setText(text)

    def notify_language_pack_download_finished(self, code: str) -> None:
        """Clear *code*'s in-flight guard and re-read what the download left behind.

        The mining-language combo is repopulated too: ``available_mining_languages``
        drops a language whose pack is missing, so a combo built at startup has no
        entry to select even once the pack is on disk. The caller must have put
        the pack on ``sys.path`` first — both the repopulation and the row refresh
        answer from ``find_spec``.
        """
        self._language_pack_active.discard(code)
        self._repopulate_mining_languages()
        if code in self.language_pack_rows:
            self._refresh_language_pack_row(code)

    def _repopulate_mining_languages(self) -> None:
        """Rebuild the combo from the current availability, keeping the selection.

        Signals blocked throughout: a rebuild reshuffles indices, and the
        resulting ``currentIndexChanged`` would propose a language switch the
        user never asked for.
        """
        current = self.mining_language_combo.currentData()
        self.mining_language_combo.blockSignals(True)
        try:
            self.mining_language_combo.clear()
            for code, display_name in available_mining_languages():
                self.mining_language_combo.addItem(display_name, code)
            index = self.mining_language_combo.findData(current)
            if index >= 0:
                self.mining_language_combo.setCurrentIndex(index)
        finally:
            self.mining_language_combo.blockSignals(False)

    # ------------------------------------------------------------------
    # Config marshalling
    # ------------------------------------------------------------------

    def load_from_config(self, config) -> None:
        """Point the selector at the language actually in force.

        No ``contribute`` counterpart, so this panel is not a ``_SavePathPanel``
        and :meth:`SettingsTab._load_config` calls this one explicitly. The
        switch controller writes ``config.language`` after stashing the outgoing
        language's scoped values, and a second writer would race it.
        """
        # config_language, not config.language: an unregistered code mines as
        # Japanese, and the selector has to show the language actually in force.
        self.set_mining_language(config_language(config))
