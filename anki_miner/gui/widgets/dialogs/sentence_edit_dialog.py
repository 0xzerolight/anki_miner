"""Word Curator sentence editor: rewrite a line, pick the word to mine from it.

A mistranscribed subtitle (時給系 for 持久系) or a mokuro OCR slip used to leave
one recourse — exclude the word and fix the card by hand in Anki, losing the
definition, pitch, audio and rank the pipeline would have attached. This window
lets the user rewrite the sentence and choose, from the words the mining parser
finds in the rewritten text, which one becomes the card. The parser is the
run's own (``EpisodeProcessor.parse_sentence_fn``), so the list here is exactly
what ``resolve_sentence_edit`` will rebuild after Confirm.

Window-modal to the curator and ``open()``-ed, never ``exec()``-ed: the curator
itself is a non-modal window (D33) and its tab keeps working while this is up.
"""

from __future__ import annotations

import html
import logging
from collections.abc import Callable

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from anki_miner.gui.resources.styles import SPACING
from anki_miner.gui.utils.content_text import content_cell_font
from anki_miner.gui.utils.keyboard_shortcuts import disown_default_buttons, primary_action_shortcut
from anki_miner.gui.utils.run_off_thread import run_off_thread
from anki_miner.languages.profile import ContentTextStyle
from anki_miner.models import TokenizedWord
from anki_miner.services.sentence_edit import nearest_token
from anki_miner.services.word_filter import CUE_JOINER
from anki_miner.utils.text_utils import wrap_target_plain

logger = logging.getLogger(__name__)

ParseLine = Callable[[str], list[TokenizedWord]]

#: Keystroke debounce before the sentence is re-parsed. One tagger pass over a
#: sentence is cheap; one per character from a fast typist is not.
_PARSE_DEBOUNCE_MS = 250


class SentenceEditDialog(QDialog):
    """Sentence box → live list of mineable words → bold preview → OK."""

    def __init__(
        self,
        word: TokenizedWord,
        *,
        parse_fn: ParseLine,
        content_style: ContentTextStyle,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._parse_fn = parse_fn
        self._tokens: list[TokenizedWord] = []
        # Generation guard + single-flight queue, the curator's own
        # _lookup_and_render contract: a result that arrives after the text has
        # moved on is dropped, and only the newest request waits behind the one
        # in flight.
        self._gen = 0
        self._inflight = False
        self._pending = False
        self._closing = False
        # What to preselect after a parse: the word the user last chose (by card
        # front), else the token starting nearest to where it stood. Seeded from
        # the row being edited, so opening the window changes nothing.
        self._prefer_form = word.mined_form
        self._prefer_start = word.surface_start

        self.setWindowTitle(self.tr("Edit word and sentence"))
        self.setWindowModality(Qt.WindowModality.WindowModal)
        font = content_cell_font(content_style)

        layout = QVBoxLayout(self)
        layout.setSpacing(SPACING.sm)
        layout.addWidget(QLabel(self.tr("Sentence")))
        self.sentence_edit = QPlainTextEdit()
        self.sentence_edit.setFont(font)
        self.sentence_edit.setTabChangesFocus(True)
        self.sentence_edit.setPlainText(word.sentence)
        layout.addWidget(self.sentence_edit, 1)

        layout.addWidget(QLabel(self.tr("Word to mine")))
        self.word_list = QListWidget()
        self.word_list.setFont(font)
        layout.addWidget(self.word_list, 1)

        self.preview_label = QLabel()
        self.preview_label.setTextFormat(Qt.TextFormat.RichText)
        self.preview_label.setWordWrap(True)
        self.preview_label.setFont(font)
        layout.addWidget(self.preview_label)

        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self._accept_if_ready)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        # Return commits kana in a Japanese IME; it must never confirm this
        # window. Ctrl+Enter is the confirming key, as everywhere else (D49).
        disown_default_buttons(self)
        primary_action_shortcut(self, self._accept_if_ready)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(_PARSE_DEBOUNCE_MS)
        self._debounce.timeout.connect(self._request_parse)
        self.sentence_edit.textChanged.connect(self._on_text_changed)
        self.word_list.currentRowChanged.connect(self._on_word_selected)

        self._set_ready(False)
        self._request_parse()

    # ------------------------------------------------------------------
    # Result
    # ------------------------------------------------------------------

    def result_word(self) -> TokenizedWord | None:
        """The chosen parsed token, or ``None`` when nothing is (yet) choosable."""
        row = self.word_list.currentRow()
        if 0 <= row < len(self._tokens):
            return self._tokens[row]
        return None

    def done(self, a0: int) -> None:  # noqa: D102 - Qt override
        # Set before the window goes: a parse landing after this must not paint.
        self._closing = True
        super().done(a0)

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def _text(self) -> str:
        """The box's text as one line: newlines join the way merged cues do."""
        return CUE_JOINER.join(line.strip() for line in self.sentence_edit.toPlainText().splitlines() if line.strip())

    def _on_text_changed(self) -> None:
        # The list describes the text it was parsed from; until the new text is
        # parsed there is nothing OK could honestly accept.
        self._set_ready(False)
        self._debounce.start()

    def _request_parse(self) -> None:
        if self._closing:
            return
        self._gen += 1
        text = self._text()
        if not text:
            self._tokens = []
            self._fill_word_list()
            self.status_label.setText(self.tr("Type a sentence."))
            return
        if self._inflight:
            self._pending = True
            return
        self._dispatch(text, self._gen)

    def _dispatch(self, text: str, gen: int) -> None:
        self._inflight = True
        self.status_label.setText(self.tr("Finding words…"))
        parse_fn = self._parse_fn
        run_off_thread(
            self,
            lambda: parse_fn(text),
            lambda tokens: self._on_parsed(gen, tokens),
            lambda message: self._on_parse_failed(gen, message),
        )

    def _on_parsed(self, gen: int, tokens: object) -> None:
        if self._closing:
            return
        self._inflight = False
        if gen == self._gen:
            self._tokens = list(tokens) if isinstance(tokens, list) else []
            self._fill_word_list()
            self.status_label.setText("" if self._tokens else self.tr("No mineable word found in this sentence."))
        self._drain()

    def _on_parse_failed(self, gen: int, message: str) -> None:
        if self._closing:
            return
        self._inflight = False
        logger.warning("sentence editor: parse failed: %s", message)
        if gen == self._gen:
            self._tokens = []
            self._fill_word_list()
            self.status_label.setText(self.tr("Could not parse this sentence."))
        self._drain()

    def _drain(self) -> None:
        if self._pending and not self._closing:
            self._pending = False
            self._request_parse()

    # ------------------------------------------------------------------
    # Word list / preview
    # ------------------------------------------------------------------

    def _fill_word_list(self) -> None:
        self.word_list.blockSignals(True)
        try:
            self.word_list.clear()
            for token in self._tokens:
                reading = token.expression_reading or token.reading
                label = f"{token.mined_form}  {reading}" if reading else token.mined_form
                self.word_list.addItem(QListWidgetItem(label))
            self.word_list.setCurrentRow(self._preselect_row())
        finally:
            self.word_list.blockSignals(False)
        self._refresh_preview()
        self._set_ready(self.result_word() is not None)

    def _preselect_row(self) -> int:
        """The row to land on after a parse: same card front, else nearest start."""
        if not self._tokens:
            return -1
        for row, token in enumerate(self._tokens):
            if token.mined_form == self._prefer_form:
                return row
        nearest = nearest_token(self._tokens, self._prefer_start)
        return 0 if nearest is None else self._tokens.index(nearest)

    def _on_word_selected(self, row: int) -> None:
        if 0 <= row < len(self._tokens):
            token = self._tokens[row]
            self._prefer_form = token.mined_form
            self._prefer_start = token.surface_start
        self._refresh_preview()
        self._set_ready(self.result_word() is not None)

    def _refresh_preview(self) -> None:
        token = self.result_word()
        if token is None:
            self.preview_label.setText("")
            return
        if token.surface_start >= 0:
            self.preview_label.setText(wrap_target_plain(token.sentence, token.surface_start, token.bold_end))
        else:
            self.preview_label.setText(html.escape(token.sentence))

    # ------------------------------------------------------------------
    # Accept
    # ------------------------------------------------------------------

    def _set_ready(self, ready: bool) -> None:
        button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        if button is not None:
            button.setEnabled(ready)

    def _accept_if_ready(self) -> None:
        button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        if button is not None and button.isEnabled() and self.result_word() is not None:
            self.accept()
