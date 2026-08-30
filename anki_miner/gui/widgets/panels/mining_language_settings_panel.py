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

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QWidget

from anki_miner.gui.resources.styles import SPACING
from anki_miner.gui.utils.language_choices import available_mining_languages
from anki_miner.gui.utils.language_gate import field_row_widgets
from anki_miner.gui.widgets.base import FormPanel
from anki_miner.gui.widgets.enhanced import ModernButton
from anki_miner.languages.registry import config_language


def kiwipiepy_installed() -> bool:
    """Return True when the Korean ENGINE is importable in this install.

    Module-level so the Korean model row can be probed (and stubbed) without
    importing kiwipiepy: ``find_spec`` answers without executing it. The MODEL is
    a separate question — ``ko_model_installer.is_installed`` answers that one.
    """
    from importlib.util import find_spec

    try:
        return find_spec("kiwipiepy") is not None
    except (ImportError, ValueError):
        return False


class MiningLanguageSettingsPanel(FormPanel):
    """Panel for the mining language and the packs a language needs.

    Signals:
        mining_language_requested: Emitted when the user picks another mining
            language. The window runs the guard and decides.
        ko_model_download_requested: Emitted when the user asks for the Korean
            model pack. The download itself is owned by the caller.
    """

    ANCHOR_NAMESPACE = "mining_language"

    mining_language_requested = pyqtSignal(str)  # proposes a switch; never commits one
    ko_model_download_requested = pyqtSignal()  # asks the caller to fetch the Korean model

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

        self._setup_ko_model_row()

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
    # Korean model download (services/ko_model_installer.py)
    # ------------------------------------------------------------------

    def _setup_ko_model_row(self) -> None:
        """Build the Korean model download row, under the language it belongs to.

        The frozen bundle ships the kiwipiepy engine without its ~88 MB model,
        and the availability probe gates Korean on that model — so until the pack
        lands, ko is not even in the combo above. That is exactly why the row
        lives here rather than beside the transcription packs: it is the only
        thing on screen that explains where Korean went.
        """
        self.download_ko_model_button = ModernButton(self.tr("Download Korean model"), variant="secondary")
        self.download_ko_model_button.setToolTip(
            self.tr(
                "Download the Korean language model into Anki Miner's folder. "
                "Bundled installs ship the Korean engine without its model."
            )
        )
        self.download_ko_model_button.clicked.connect(self._on_ko_model_download_clicked)

        self.ko_model_status_label = QLabel("")
        self.ko_model_status_label.setObjectName("settings-save-status")

        ko_container = QWidget()
        ko_row = QHBoxLayout(ko_container)
        ko_row.setContentsMargins(0, 0, 0, 0)
        ko_row.setSpacing(SPACING.xs)
        ko_row.addWidget(self.download_ko_model_button)
        ko_row.addWidget(self.ko_model_status_label)
        ko_row.addStretch()
        self.add_field(
            self.tr("Korean model"),
            ko_container,
            anchor="ko_model",
            anchor_focus=self.download_ko_model_button,
            anchor_text=lambda: (self.download_ko_model_button.text(), self.download_ko_model_button.toolTip()),
        )
        # The whole row (label included) disappears where kiwipiepy is absent.
        # NOT language-gated: this row is about a language the user is not on
        # yet, so a capability gate would hide the one control that gets them
        # there.
        self._ko_model_row_widgets = field_row_widgets(self, ko_container)
        self._ko_model_active = False
        self._refresh_ko_model_row()

    def _refresh_ko_model_row(self) -> None:
        """Show, hide and label the row from what this install actually has."""
        from anki_miner.services.ko_model_installer import is_installed, ko_model_root

        engine = kiwipiepy_installed()
        for widget in self._ko_model_row_widgets:
            widget.setVisible(engine)
        if not engine:
            return
        if self._ko_model_active:
            self.download_ko_model_button.setEnabled(False)
            return
        installed = is_installed(ko_model_root())
        self.download_ko_model_button.setEnabled(not installed)
        self.ko_model_status_label.setText(self.tr("Installed") if installed else self.tr("Not installed"))

    def _on_ko_model_download_clicked(self) -> None:
        """Guard against a second press and ask the caller to run the download."""
        if self._ko_model_active:
            return
        self._ko_model_active = True
        self.download_ko_model_button.setEnabled(False)
        self.ko_model_download_requested.emit()

    def set_ko_model_status(self, text: str) -> None:
        """Show a Korean model download status line."""
        self.ko_model_status_label.setText(text)

    def notify_ko_model_download_finished(self) -> None:
        """Clear the in-flight guard and re-read what the download left behind.

        The mining-language combo is repopulated too: ``available_mining_languages``
        drops Korean while the model is missing, so a combo built at startup has
        no ko entry to select even once the pack is on disk.
        """
        self._ko_model_active = False
        self._repopulate_mining_languages()
        self._refresh_ko_model_row()

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
