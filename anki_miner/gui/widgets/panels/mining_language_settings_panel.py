"""Mining-language selection and the packs a language needs.

Its own destination rather than a section of Filtering: the switcher is not a
filter, and every language added after ja brings plumbing of its own. Since
D12 (UI/UX audit 2026-09-29) a language that needs its pack is picked straight
from the list, which then offers "Download and switch"; the per-language pack
rows are gone. The per-language *filtering* options
(kana, hangul, wordsets) stay in Filtering, where they read as filters; the
character-set/regional-variety choice lives here instead, beside the selector
whose language it varies with (T10).

In ``SettingsTab._save_panels`` (T10): the variant combos write
``script_variant``, so the panel takes part in the Save round-trip like any
other. Its own ``mining_language_combo`` stays out of ``_wire_edit_signals``
though -- picking a language proposes a guarded switch which commits its own
config, so arming the autosave debounce on that combo would save the
pre-switch panel state on top of it.
"""

from dataclasses import replace

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QWidget

from anki_miner.gui.resources.styles import SPACING
from anki_miner.gui.utils.language_choices import MiningLanguageChoice, bidi_isolated, mining_language_choices
from anki_miner.gui.utils.language_gate import apply_language_gate, field_row_widgets
from anki_miner.gui.widgets.base import FormPanel
from anki_miner.gui.widgets.enhanced import ModernButton
from anki_miner.languages.registry import config_language, get_profile
from anki_miner.utils.i18n import tr_format


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
        super().__init__(self.tr("Mining Language"), parent=parent)
        # D12: the language offered for download and awaiting its switch, and
        # the language actually in force (what a cancel re-points the combo to).
        self._choices_by_code: dict[str, MiningLanguageChoice] = {}
        self._pending_code: str | None = None
        self._live_code = "ja"
        #: Codes with a pack download in flight (two can run at once).
        self._language_pack_active: set[str] = set()
        #: Each in-flight download's latest status line. The row shows only the
        #: pending pick's, so a re-pick restores its own line instead of a blank.
        self._language_pack_status: dict[str, str] = {}
        self._setup_fields()

    def _setup_fields(self) -> None:
        """Set up the panel fields."""
        # Every capability contributor extends this list; a second assignment
        # would drop the pairs already in it, so this is the only place it is
        # bound.
        self._language_gate_pairs: list[tuple[QWidget, str]] = []

        self.add_section(self.tr("Language"))

        self.mining_language_combo = QComboBox()
        self._populate_mining_languages()
        self.mining_language_combo.currentIndexChanged.connect(self._on_mining_language_changed)
        self.add_field(
            self.tr("Mining Language"),
            self.mining_language_combo,
            helper=self.tr(
                "Switching swaps dictionaries, filters, deck and card fields to that "
                "language's own settings. The interface language is separate "
                "(Settings → General)."
            ),
            # Every listed English name, so "Korean" finds this row (the pack
            # rows that used to carry them are gone, D12).
            anchor_text=lambda: tuple(choice.english_name for choice in self._choices_by_code.values()),
        )

        # D12: shown when a language that needs its pack is picked.
        self.pending_download_row = QWidget()
        pending = QHBoxLayout(self.pending_download_row)
        pending.setContentsMargins(0, 0, 0, 0)
        pending.setSpacing(SPACING.xs)
        self.pending_download_label = QLabel()
        self.pending_download_label.setWordWrap(True)
        pending.addWidget(self.pending_download_label, 1)
        self.download_and_switch_button = ModernButton(self.tr("Download and switch"), variant="primary")
        self.download_and_switch_button.clicked.connect(self._on_download_and_switch_clicked)
        pending.addWidget(self.download_and_switch_button)
        self.pending_download_status = QLabel()
        self.pending_download_status.setObjectName("validation-status")
        pending.addWidget(self.pending_download_status)
        self.pending_download_row.setVisible(False)
        # The combo above is the setting (and its search anchor); this row is
        # only the offer that follows one pick.
        self.add_widget(
            self.pending_download_row, anchor_ignore="download offer for the picked language, not a setting"
        )

        # Chinese script preference (T10, moved from Word Filters). Generic,
        # language-scoped field - ja and ko carry "" here and never see this
        # row.
        self.add_section(self.tr("Script Variants"))
        self._script_variants_section_label = self._active_section_label

        self.script_variant_combo = QComboBox()
        # "" leads because it is the zh default. It must have an item of its
        # own: findData returns -1 for a value the combo does not carry, the
        # panel then shows item 0, and contribute() writes that item back on
        # the next Save.
        self.script_variant_combo.addItem(self.tr("As written"), "")
        self.script_variant_combo.addItem(self.tr("Simplified (简体)"), "simplified")
        self.script_variant_combo.addItem(self.tr("Traditional (繁體)"), "traditional")
        self.add_field(
            self.tr("Character Set"),
            self.script_variant_combo,
            helper=self.tr(
                "Which spelling the card front and the dictionary lookup prefer; "
                "As written keeps the source's own spelling."
            ),
        )

        # Portuguese national variety (T10, moved from Word Filters): the same
        # language-scoped field as the zh combo above, its own ids and
        # capability, so at most one of the two is ever visible and
        # contribute() writes only the visible one.
        self.add_section(self.tr("Regional Variety"))
        self._regional_variants_section_label = self._active_section_label

        self.regional_variant_combo = QComboBox()
        self.regional_variant_combo.addItem(self.tr("Brazilian Portuguese"), "br")
        self.regional_variant_combo.addItem(self.tr("European Portuguese"), "pt")
        self.add_field(
            self.tr("Variety"),
            self.regional_variant_combo,
            helper=self.tr(
                "Which Google voice reads word and sentence audio, and which frequency list setup suggests."
            ),
        )

        self._language_gate_pairs.extend(
            (w, "script_variants") for w in field_row_widgets(self, self.script_variant_combo)
        )
        if self._script_variants_section_label is not None:
            self._language_gate_pairs.append((self._script_variants_section_label, "script_variants"))
        self._language_gate_pairs.extend(
            (w, "regional_variants") for w in field_row_widgets(self, self.regional_variant_combo)
        )
        if self._regional_variants_section_label is not None:
            self._language_gate_pairs.append((self._regional_variants_section_label, "regional_variants"))

        self.add_stretch()

    def set_mining_language(self, code: str) -> None:
        """Point the combo at the language in force, without proposing a switch.

        Signals blocked: this runs from ``load_from_config`` and from the
        window's re-point after a refused switch, and an emit there would ask
        for the switch that was just refused. Any pending download offer is
        dropped: the language in force changed under it.
        """
        self._live_code = code
        self._hide_pending()
        index = self.mining_language_combo.findData(code)
        if index < 0:
            return
        self.mining_language_combo.blockSignals(True)
        try:
            self.mining_language_combo.setCurrentIndex(index)
        finally:
            self.mining_language_combo.blockSignals(False)

    def _on_mining_language_changed(self, index: int) -> None:
        """Propose a switch, or offer the download a language still needs (D12)."""
        code = self.mining_language_combo.itemData(index)
        if not isinstance(code, str) or not code:
            return
        if self.propose_download(code):
            return
        self._hide_pending()
        self.mining_language_requested.emit(code)

    # ------------------------------------------------------------------
    # D12: languages that need a pack download, straight from the list
    # ------------------------------------------------------------------

    def _populate_mining_languages(self) -> None:
        """Fill the combo from ``mining_language_choices`` (signals blocked)."""
        combo = self.mining_language_combo
        current = combo.currentData()
        self._choices_by_code = {choice.code: choice for choice in mining_language_choices()}
        combo.blockSignals(True)
        try:
            combo.clear()
            for choice in self._choices_by_code.values():
                name = choice.native_name
                label = tr_format(self.tr("%1 (download)"), name) if choice.needs_download else name
                combo.addItem(label, choice.code)
            index = combo.findData(current)
            if index >= 0:
                combo.setCurrentIndex(index)
        finally:
            combo.blockSignals(False)

    def _repopulate_mining_languages(self) -> None:
        """Rebuild the list after a pack lands, keeping the selection (never proposes a switch)."""
        self._populate_mining_languages()

    def propose_download(self, code: str) -> bool:
        """Show the "needs a one-time download" row for ``code``; False if it needs none."""
        choice = self._choices_by_code.get(code)
        if choice is None or not choice.needs_download:
            return False
        self._pending_code = code
        self.pending_download_label.setText(
            tr_format(
                self.tr("%1 needs a one-time download of about %2 MB."),
                bidi_isolated(choice.native_name),
                str(choice.download_mb),
            )
        )
        downloading = code in self._language_pack_active
        self.set_status_text(
            self.pending_download_status,
            self._language_pack_status.get(code, "") if downloading else "",
            status="info",
        )
        self.pending_download_label.setVisible(True)
        self.download_and_switch_button.setVisible(True)
        self.download_and_switch_button.setEnabled(not downloading)
        self.pending_download_row.setVisible(True)
        return True

    def cancel_pending_switch(self) -> None:
        """Drop the pending switch (the download, if running, carries on) and re-point the combo."""
        if self._pending_code is None:
            return
        self.set_mining_language(self._live_code)

    def _hide_pending(self) -> None:
        self._pending_code = None
        self.pending_download_row.setVisible(False)

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt override
        """Leaving the page cancels a pending switch (D12). A minimise is spontaneous and does not."""
        super().hideEvent(event)
        if event is not None and not event.spontaneous():
            self.cancel_pending_switch()

    def _on_download_and_switch_clicked(self) -> None:
        code = self._pending_code
        if code is None or code in self._language_pack_active:
            return
        self._language_pack_active.add(code)
        self.download_and_switch_button.setEnabled(False)
        self.language_pack_download_requested.emit(code)

    def set_language_pack_status(self, code: str, text: str) -> None:
        """Show a download status line on the pending row, if ``code`` is the pending pick."""
        self._language_pack_status[code] = text
        if code == self._pending_code:
            self.set_status_text(self.pending_download_status, text, status="info")

    def notify_language_pack_download_finished(self, code: str) -> None:
        """Clear the in-flight guard, rebuild the list, and switch if still wanted.

        The caller must have put the pack on ``sys.path`` first: the rebuild
        answers from each profile's availability probe. A failed download
        leaves the language needing its pack, so the offer (and the worker's
        message) stays and Download and switch works again. A pack that installs
        but still leaves the language failing its probe drops it from the list
        (an installed pack is no longer a download), so the combo goes back to
        the language in force and the row says the language is still unusable.
        """
        self._language_pack_active.discard(code)
        self._language_pack_status.pop(code, None)
        offered = self._choices_by_code.get(code)
        self._repopulate_mining_languages()
        if code != self._pending_code:
            return
        choice = self._choices_by_code.get(code)
        if choice is None:
            # Without this the combo sat on whatever the rebuild put at item 0,
            # under an offer that could no longer help.
            self.set_mining_language(self._live_code)
            name = offered.native_name if offered is not None else code
            # The reason takes the row: the offer's sentence and button are moot.
            self.pending_download_label.setVisible(False)
            self.download_and_switch_button.setVisible(False)
            self.set_status_text(
                self.pending_download_status,
                tr_format(self.tr("%1 still can't be mined after its download."), bidi_isolated(name)),
                status="error",
            )
            self.pending_download_row.setVisible(True)
            return
        if not choice.needs_download:
            self._hide_pending()
            self.mining_language_requested.emit(code)
            return
        self.download_and_switch_button.setEnabled(True)

    # ------------------------------------------------------------------
    # Config marshalling
    # ------------------------------------------------------------------

    def load_from_config(self, config) -> None:
        """Point the selector at the language in force, and load the variant combos.

        A ``_SavePathPanel`` since T10: the two variant combos write
        ``script_variant``, so :meth:`SettingsTab._load_config` reaches this
        through the ``_save_panels`` loop like any other panel. The mining
        language selector itself is unaffected -- the switch controller writes
        ``config.language`` after stashing the outgoing language's scoped
        values, and a second writer would race it, so this only ever points the
        combo, never proposes a switch (see :meth:`set_mining_language`).
        """
        # config_language, not config.language: an unregistered code mines as
        # Japanese, and the selector has to show the language actually in force.
        self.set_mining_language(config_language(config))
        index = self.script_variant_combo.findData(config.script_variant)
        if index >= 0:
            self.script_variant_combo.setCurrentIndex(index)
        index = self.regional_variant_combo.findData(config.script_variant)
        if index >= 0:
            self.regional_variant_combo.setCurrentIndex(index)
        apply_language_gate(self._language_gate_pairs, get_profile(config_language(config)).capabilities)

    def contribute(self, config):
        """Return a new config with the visible variant combo's value applied.

        Uses ``dataclasses.replace`` so the frozen-config invariant is
        preserved. Called by :meth:`SettingsTab.commit_settings` as part of the
        contribute fold.

        Both combos write the same field, ``script_variant``, and the gate
        shows at most one of them: a blind write would stamp the hidden one's
        own default ("br", the Portuguese row's first item) onto a language
        that has neither setting. Visibility is the gate's own output, so
        there is one source of truth for "does this language have this
        setting". With neither combo visible, ``config`` is returned unchanged.
        """
        if self.script_variant_combo.isVisibleTo(self):
            return replace(config, script_variant=str(self.script_variant_combo.currentData()))
        if self.regional_variant_combo.isVisibleTo(self):
            return replace(config, script_variant=str(self.regional_variant_combo.currentData()))
        return config
