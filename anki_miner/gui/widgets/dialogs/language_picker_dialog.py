"""Subtitle-language picker for the Utilities → Download tool.

Replaces the free-text ``--sub-langs`` box, which required the user to know
that Simplified Chinese is ``zh-Hans`` and not ``zh-CN``. Names come from
``QLocale`` (see :mod:`anki_miner.gui.utils.language_names`), so no ISO table is
maintained here.

Three tiers, in the order a user reaches for them:

* **Available for this URL** — populated from a real probe, so the codes shown
  are the ones the site actually publishes. Present only when the tab has
  detected tracks.
* **Common languages** — the curated fallback, so the picker is useful before
  any URL is pasted.
* **Advanced** — a raw ``--sub-langs`` expression. yt-dlp accepts regexes
  (``en.*``), exclusions (``-live_chat``) and ``all``; none of those can be a
  checkbox, so a non-empty Advanced field wins outright and visibly disables the
  list rather than pretending the two can be merged.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from anki_miner.gui.utils.language_names import (
    COMMON_SUBTITLE_LANGS,
    format_lang_list,
    language_display_name,
    parse_lang_list,
)
from anki_miner.gui.utils.qt_helpers import add_min_max_buttons
from anki_miner.services.media_downloader import UrlTracks


class LanguagePickerDialog(QDialog):
    """Pick subtitle languages by name, returning a ``--sub-langs`` string.

    Args:
        selection: The current ``--sub-langs`` value. A plain comma list ticks
            its codes; anything yt-dlp-shaped seeds the Advanced field instead.
        detected: Tracks from a probe of the user's URL, or None.
        parent: Optional parent widget.
    """

    def __init__(
        self,
        selection: str,
        *,
        detected: UrlTracks | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Subtitle Languages"))
        self.setMinimumSize(460, 480)

        seeded = parse_lang_list(selection)
        # None means the value is an expression the checkboxes cannot express.
        expression = "" if seeded is not None else selection.strip()
        checked = set(seeded or ())

        layout = QVBoxLayout(self)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(self.tr("Search languages…"))
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._apply_search)
        layout.addWidget(self.search_edit)

        self.lang_list = QListWidget()
        self.lang_list.itemChanged.connect(self._refresh_ok_state)
        layout.addWidget(self.lang_list, 1)
        self._populate(detected, checked)

        self.translations_note = QLabel(
            self.tr(
                "This URL also offers machine-translated captions in many more "
                "languages. Type a code in Advanced to request one."
            )
        )
        self.translations_note.setObjectName("helper-text")
        self.translations_note.setWordWrap(True)
        self.translations_note.setHidden(not (detected is not None and detected.has_auto_translations))
        layout.addWidget(self.translations_note)

        advanced_label = QLabel(self.tr("Advanced (raw yt-dlp language expression):"))
        advanced_label.setObjectName("helper-text")
        layout.addWidget(advanced_label)

        self.advanced_edit = QLineEdit(expression)
        self.advanced_edit.setPlaceholderText(self.tr("e.g. en.*,-live_chat"))
        self.advanced_edit.textChanged.connect(self._on_advanced_changed)
        layout.addWidget(self.advanced_edit)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        assert ok_button is not None
        self.ok_button = ok_button

        self._on_advanced_changed(self.advanced_edit.text())

        add_min_max_buttons(self)

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------

    def _populate(self, detected: UrlTracks | None, checked: set[str]) -> None:
        """Fill the list: detected codes first, then the curated tail."""
        seen: set[str] = set()

        if detected is not None:
            auto_only = [c for c in detected.auto_sub_langs if c not in detected.manual_sub_langs]
            available = list(detected.manual_sub_langs) + auto_only
            if available:
                self._add_header(self.tr("Available for this URL"))
                for code in available:
                    self._add_code(code, checked, automatic=code in auto_only)
                    seen.add(code)

        # The four mining languages keep their head position; the rest sort by
        # the name the user actually reads.
        head = [c for c in COMMON_SUBTITLE_LANGS[:4] if c not in seen]
        tail = sorted(
            (c for c in COMMON_SUBTITLE_LANGS[4:] if c not in seen),
            key=language_display_name,
        )
        # A code the user already selected but that is neither detected nor
        # curated must still be visible and ticked, or opening the dialog and
        # pressing OK would silently drop it.
        extra = [c for c in checked if c not in seen and c not in head and c not in tail]
        common = head + tail + sorted(extra, key=language_display_name)
        if common:
            self._add_header(self.tr("Common languages"))
            for code in common:
                self._add_code(code, checked, automatic=False)

    def _add_header(self, text: str) -> None:
        """Append a non-selectable section header row."""
        item = QListWidgetItem(text)
        item.setData(Qt.ItemDataRole.UserRole, None)
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        self.lang_list.addItem(item)

    def _add_code(self, code: str, checked: set[str], *, automatic: bool) -> None:
        """Append one checkable language row."""
        label = f"{language_display_name(code)}  ({code})"
        if automatic:
            label += self.tr("  · automatic")
        item = QListWidgetItem(label)
        item.setData(Qt.ItemDataRole.UserRole, code)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Checked if code in checked else Qt.CheckState.Unchecked)
        self.lang_list.addItem(item)

    # ------------------------------------------------------------------
    # Interaction
    # ------------------------------------------------------------------

    def _apply_search(self, text: str) -> None:
        """Hide rows not matching *text*, and any header left with no rows."""
        needle = text.strip().lower()
        header: QListWidgetItem | None = None
        header_has_match = False
        for row in range(self.lang_list.count()):
            item = self.lang_list.item(row)
            assert item is not None
            code = item.data(Qt.ItemDataRole.UserRole)
            if code is None:
                if header is not None:
                    header.setHidden(not header_has_match)
                header, header_has_match = item, False
                continue
            match = not needle or needle in item.text().lower() or needle in str(code).lower()
            item.setHidden(not match)
            header_has_match = header_has_match or match
        if header is not None:
            header.setHidden(not header_has_match)

    def _on_advanced_changed(self, text: str) -> None:
        """A raw expression takes over: the checkboxes cannot express it."""
        self.lang_list.setEnabled(not text.strip())
        self._refresh_ok_state()

    def _refresh_ok_state(self, *_: object) -> None:
        """OK needs something to send — ``--sub-langs ''`` is not valid."""
        self.ok_button.setEnabled(bool(self.selected_langs()))

    # ------------------------------------------------------------------
    # Result
    # ------------------------------------------------------------------

    def selected_langs(self) -> str:
        """Return the ``--sub-langs`` value this dialog represents."""
        advanced = self.advanced_edit.text().strip()
        if advanced:
            return advanced
        return format_lang_list(self._checked_codes())

    def _checked_codes(self) -> list[str]:
        """Ticked codes in list order, including rows hidden by the search."""
        codes: list[str] = []
        for row in range(self.lang_list.count()):
            item = self.lang_list.item(row)
            assert item is not None
            code = item.data(Qt.ItemDataRole.UserRole)
            if code is not None and item.checkState() == Qt.CheckState.Checked:
                codes.append(str(code))
        return codes
