"""Load one ``.pdf`` book's text layer into a :class:`ReadingDocument` (TS-05).

pypdf (BSD-3, pure Python) extracts each page's text; the page is the unit's
position (``p.N``). A page's wrapped lines are joined into one paragraph the
way the mining language writes a line wrap (``_util._line_join``: a space, or
nothing for CJK), and in a space-delimited language a word hyphenated across
a line end is rejoined. The paragraph is then sentence-split like every other
book.

Only the embedded text layer is read. A scanned PDF has none and is refused
with a clear message; OCR is not this loader's job. Reading order is pypdf's:
vertical Japanese and two-column pages can come out in the wrong order, which
is the documented limit of PDF sources.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from pypdf import PdfReader
from pypdf.errors import PyPdfError

from anki_miner.exceptions import SetupError, raise_if_cancelled
from anki_miner.models.reading import ReadingDocument, ReadingSourceRef, ReadingUnit
from anki_miner.services.reading._util import READING_CANCELLED, _line_join
from anki_miner.services.reading.sentence_splitter import split_sentences
from anki_miner.utils.logging_ext import log_summary

if TYPE_CHECKING:
    from anki_miner.languages.profile import SentenceRules

logger = logging.getLogger(__name__)

#: Largest PDF opened. Text-heavy books are a few MB; image-heavy ones reach
#: hundreds, and pypdf reads only what each page needs.
_MAX_PDF_BYTES = 512 * 1024 * 1024


def _join_page_lines(lines: list[str], rules: SentenceRules | None) -> str:
    """One paragraph from a page's text lines.

    Lines are stripped and blank ones dropped. In a space-delimited language a
    line ending in a hyphen after a letter joins the next line with no space:
    before a lowercase letter the hyphen is a word broken by the wrap and is
    dropped (``ca-``/``sa`` → ``casa``), before anything else it is the word's
    own (``Castilla-``/``La Mancha``). Every other wrap becomes
    :func:`_line_join`'s joiner.
    """
    joiner = _line_join(rules)
    out = ""
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if not out:
            out = line
        elif joiner and out.endswith("-") and len(out) > 1 and out[-2].isalpha():
            out = (out[:-1] if line[0].islower() else out) + line
        else:
            out = out + joiner + line
    return out


def _open(ref: ReadingSourceRef) -> PdfReader:
    assert ref.path is not None
    pdf_path = ref.path
    try:
        if pdf_path.stat().st_size > _MAX_PDF_BYTES:
            raise SetupError(f"'{pdf_path.name}' is too large to mine.")
        reader = PdfReader(pdf_path)
        if reader.is_encrypted and not reader.decrypt(""):
            raise SetupError(f"'{pdf_path.name}' is password-protected and cannot be mined.")
        return reader
    except SetupError:
        raise
    except (PyPdfError, OSError, ValueError, KeyError, TypeError) as exc:
        log_summary(logger, "PDF rejected", level=logging.WARNING, file=pdf_path, error=type(exc).__name__)
        raise SetupError(f"'{pdf_path.name}' is not a readable PDF.") from exc


def load(
    ref: ReadingSourceRef,
    *,
    cancel_check: Callable[[], bool] | None = None,
    rules: SentenceRules | None = None,
) -> ReadingDocument:
    """Load ``ref.path`` (a ``.pdf``) into a book :class:`ReadingDocument`.

    The title is the file name's stem (PDF metadata titles are too often a
    word processor's leftovers to put on cards). Raises :class:`SetupError`
    for an unreadable, password-protected or text-less PDF; an unreadable page
    is skipped with a warning and the rest of the book still mines.
    """
    raise_if_cancelled(cancel_check, READING_CANCELLED)
    reader = _open(ref)
    assert ref.path is not None
    title = ref.title or ref.path.stem
    doc = ReadingDocument(title=title, kind="book", series="Books", episode=title)
    index = 0
    pages = 0
    text_pages = 0
    unreadable = 0
    for page_no, page in enumerate(reader.pages, start=1):
        raise_if_cancelled(cancel_check, READING_CANCELLED)
        pages += 1
        try:
            raw = page.extract_text() or ""
        except Exception as exc:  # noqa: BLE001 — one damaged page must not lose the book
            logger.debug("Ignored failure during PDF page %d text extraction: %s: %s", page_no, type(exc).__name__, exc)
            unreadable += 1
            continue
        paragraph = _join_page_lines(raw.splitlines(), rules)
        if not paragraph:
            continue
        text_pages += 1
        for sentence in split_sentences(paragraph, rules=rules):
            raise_if_cancelled(cancel_check, READING_CANCELLED)
            doc.units.append(ReadingUnit(text=sentence, index=index, location_label=f"p.{page_no}"))
            index += 1
    if unreadable:
        doc.warnings.append(f"Skipped {unreadable} PDF page(s) whose text could not be read.")
    log_summary(
        logger,
        "PDF parse",
        file=ref.path,
        pages=pages,
        text_pages=text_pages,
        units=index,
        chars=sum(len(unit.text) for unit in doc.units),
        unreadable=unreadable,
    )
    if not doc.units:
        raise SetupError(
            f"'{ref.path.name}' has no text layer (a scanned PDF?). Anki Miner reads the text inside a PDF, not its images."
        )
    return doc
