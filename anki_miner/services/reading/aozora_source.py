"""Load one ``.txt`` novel (Aozora Bunko markup or plain text) into units.

Pure stdlib, no chardet dependency. The detector hands a ``txt`` ref whose
``title`` is a provisional file-stem label; for Aozora files the header title
here becomes the authoritative document title/episode, so this loader is the
final say on metadata (series is always the constant ``"Books"``).

Transform order on an Aozora body line is fixed: **gaiji → ruby → annotations**.
Gaiji must resolve first so a gaiji-produced kanji can anchor a ruby base and so
its inner ``［＃`` never looks like an annotation; ruby strips before annotations
so a ``《reading》`` can't confuse the annotation scanner.
"""

from __future__ import annotations

import dataclasses
import logging
import re
import unicodedata
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, BinaryIO

from anki_miner.exceptions import SetupError, raise_if_cancelled
from anki_miner.models.reading import (
    ReadingDocument,
    ReadingSourceRef,
    ReadingUnit,
)

# _decode's canonical home is _util (shared with subtitle_source); imported
# here both for load() and as a re-export for tests that patch/call it.
from anki_miner.services.reading._util import READING_CANCELLED, _decode
from anki_miner.services.reading.sentence_splitter import split_sentences
from anki_miner.utils.logging_ext import log_summary

if TYPE_CHECKING:
    from anki_miner.languages.profile import SentenceRules

logger = logging.getLogger(__name__)

# The three over-cap SetupErrors (two in load(), one in _read_part) name this in
# MB ("over 32 MB"); change them if this changes.
_MAX_TEXT_FILE_BYTES = 32 * 1024 * 1024

# A .txt over the cap is mined as consecutive parts of this size, one queue item
# each (split_oversize), because one run over the whole file holds every unit in
# memory and sits in an uncancellable parse for minutes. A quarter of the cap
# keeps each part's parse short. The last part runs to end of file, so it holds
# up to two parts' worth and must still fit under the cap.
_PART_BYTES = _MAX_TEXT_FILE_BYTES // 4

# Longest line a part boundary may fall inside. A part owns every line that
# starts in it, so it reads the line crossing its end in full, up to this.
# _long_line_error names it ("over 1 MB").
_LINE_SLACK = 1024 * 1024

assert _LINE_SLACK < _PART_BYTES and 2 * _PART_BYTES <= _MAX_TEXT_FILE_BYTES

# A b"\n" split is safe in every ASCII-compatible encoding the ladders decode
# (0x0A is never a trail byte), but not in UTF-16, where it can sit inside a
# character.
_UTF16_BOMS = (b"\xff\xfe", b"\xfe\xff")

# --- gaiji (external characters) -----------------------------------------

_UPLUS_RE = re.compile(r"U\+([0-9A-Fa-f]{4,6})")
# men-区-点 triple; the trailing 第N水準 is a single number, never a triple, so
# it can't be mistaken for the men-ku-ten here. Dash class covers full-width /
# minus-sign / bar variants seen in the wild (NFKC already folds － FF0D).
_MENKUTEN_RE = re.compile(r"(\d{1,2})[-−―‐](\d{1,3})[-−―‐](\d{1,3})")

_GETA = "〓"  # U+3013, the geta mark used for an unresolvable gaiji


def _menkuten_char(men: int, ku: int, ten: int) -> str:
    if not (1 <= men <= 2 and 1 <= ku <= 94 and 1 <= ten <= 94):
        return _GETA
    body = bytes([0xA0 + ku, 0xA0 + ten])
    if men == 2:
        body = b"\x8f" + body  # SS3: JIS X 0213 plane 2
    try:
        return body.decode("euc_jis_2004")
    except UnicodeDecodeError:
        return _GETA


def _gaiji_char(control: str) -> str:
    """Resolve a gaiji marker body to one character (U+ form, then 面区点, else 〓)."""
    norm = unicodedata.normalize("NFKC", control)
    m = _UPLUS_RE.search(norm)
    if m:
        try:
            codepoint = int(m.group(1), 16)
            if 0xD800 <= codepoint <= 0xDFFF:
                return _GETA
            return chr(codepoint)
        except (ValueError, OverflowError):
            return _GETA
    triples = _MENKUTEN_RE.findall(norm)
    if triples:
        men, ku, ten = (int(x) for x in triples[-1])
        return _menkuten_char(men, ku, ten)
    return _GETA


def _find_close(line: str, start: int) -> int:
    """Index of the ``］`` closing an annotation opened before ``start``.

    Skips ``］`` inside ``「」`` spans (nesting legal); a plain regex can't. -1
    when unterminated.
    """
    depth = 0
    for i in range(start, len(line)):
        c = line[i]
        if c == "「":
            depth += 1
        elif c == "」":
            if depth > 0:
                depth -= 1
        elif c == "］" and depth == 0:
            return i
    return -1


def _resolve_gaiji(line: str) -> str:
    """Replace every ``※［＃…］`` gaiji marker with its resolved character."""
    if "※［＃" not in line:
        return line
    out: list[str] = []
    i = 0
    n = len(line)
    while i < n:
        if line.startswith("※［＃", i):
            close = _find_close(line, i + 3)
            if close == -1:
                out.append(line[i])
                i += 1
                continue
            out.append(_gaiji_char(line[i + 3 : close]))
            i = close + 1
        else:
            out.append(line[i])
            i += 1
    return "".join(out)


# --- ruby ----------------------------------------------------------------

# A ruby span; readings are discarded. Deleting every 《…》 span plus every ｜
# base-marker is provably equivalent to the "replace base《reading》 with base"
# semantics: both keep exactly the text before 《. Aozora never uses 《》 for
# quotation, so an unconditional strip is safe on the Aozora path.
_RUBY_RE = re.compile(r"《[^》]*》")

# A ruby span *attached* to base text: the ｜ base-marker and its bounded base
# through 《, or a kanji/kana run directly before 《 — in either case holding a
# kana reading, which is what an Aozora ruby always is. A bare, standalone 《…》
# (a plain novel using the double-angle bracket as title/quotation punctuation)
# has whitespace / line-start / punctuation before 《 and is NOT ruby — so it
# must not, on its own, mark a file as Aozora (Bug Y4: doing so dropped the first
# block as a "header" and stripped every 《…》 span silently). The kana reading is
# what keeps Chinese out: there 他读了《红楼梦》 is a work title, and the character
# before 《 (or a ｜ used as a chapter separator earlier in the line) is no signal.
_RUBY_ATTACHED_RE = re.compile(r"(?:｜[^｜《》\r\n]+|[々぀-ヿ一-鿿])《[ぁ-ヿ]")


def _strip_ruby(line: str) -> str:
    return _RUBY_RE.sub("", line).replace("｜", "")


# --- annotations ---------------------------------------------------------


def _classify(control: str) -> str:
    """One of block_start / block_end / inline_heading / other for an annotation."""
    if "見出し" in control:
        if control.startswith("ここから"):
            return "block_start"
        if control.startswith("ここで") and "終わり" in control:
            return "block_end"
        return "inline_heading"
    return "other"


def _strip_annotations(line: str) -> tuple[str, bool, bool, bool]:
    """Remove every ``［＃…］`` marker, keeping surrounding/inner body text.

    Returns ``(cleaned, inline_heading, block_start, block_end)``. Heading
    markers only set flags; 傍点/太字/割り注/改ページ and all other forms are
    removed with their body text left intact.
    """
    if "［＃" not in line:
        return line, False, False, False
    out: list[str] = []
    inline = block_start = block_end = False
    i = 0
    n = len(line)
    while i < n:
        if line.startswith("［＃", i):
            close = _find_close(line, i + 2)
            if close == -1:
                out.append(line[i:])
                break
            kind = _classify(line[i + 2 : close])
            if kind == "block_start":
                block_start = True
            elif kind == "block_end":
                block_end = True
            elif kind == "inline_heading":
                inline = True
            i = close + 1
        else:
            out.append(line[i])
            i += 1
    return "".join(out), inline, block_start, block_end


# --- header / footer -----------------------------------------------------

_RULE_RE = re.compile(r"^-{8,}$")
_FOOTER_PREFIXES = ("底本：", "底本:", "青空文庫作成ファイル：")


def _splitlines(text: str) -> list[str]:
    """Physical lines only (no splitting on other Unicode line boundaries)."""
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def _cut_footer(lines: list[str]) -> list[str]:
    """Drop the colophon: everything from the last 底本 (fallbacks) line on."""
    for prefix in _FOOTER_PREFIXES:
        cut = None
        for idx, ln in enumerate(lines):
            if ln.startswith(prefix):
                cut = idx
        if cut is not None:
            return lines[:cut]
    return lines


def _drop_symbol_block(lines: list[str]) -> list[str]:
    """Drop the optional ``-{8,}``…``-{8,}`` 記号説明 block (first pair)."""
    r1 = next((i for i, ln in enumerate(lines) if _RULE_RE.match(ln)), None)
    if r1 is None:
        return lines
    r2 = next((i for i in range(r1 + 1, len(lines)) if _RULE_RE.match(lines[i])), None)
    if r2 is None:
        return lines
    return lines[:r1] + lines[r2 + 1 :]


def _is_aozora(text: str) -> bool:
    """Detect a genuine Aozora Bunko file (vs. a plain ``.txt`` novel).

    A bare standalone ``《…》`` is NOT sufficient — a plain novel may write a
    work title / quotation with the double-angle bracket, and treating that as
    Aozora dropped its first block as a "header" and stripped every ``《…》``
    span (Bug Y4). Require a real Aozora signal: an accent/annotation marker
    ``［＃``, a kana ruby *attached* to a base, or a header ruler line.
    """
    if "［＃" in text:
        return True
    if _RUBY_ATTACHED_RE.search(text):
        return True
    return any(_RULE_RE.match(ln) for ln in _splitlines(text))


def _extract_header(lines: list[str]) -> tuple[str, list[str]]:
    """Return (header title, body lines) for the Aozora path."""
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    title = _strip_ruby(_resolve_gaiji(lines[i])).strip() if i < len(lines) else ""
    while i < len(lines) and lines[i].strip():  # pre-blank block = header
        i += 1
    while i < len(lines) and not lines[i].strip():  # skip the blank gap
        i += 1
    return title, _drop_symbol_block(lines[i:])


# --- unit emission -------------------------------------------------------


def _emit_units(
    body_lines: list[str],
    aozora: bool = True,
    *,
    cancel_check: Callable[[], bool] | None = None,
    rules: SentenceRules | None = None,
) -> tuple[list[ReadingUnit], int, int]:
    units: list[ReadingUnit] = []
    index = 0
    para_no = 0
    skipped = 0
    current_chapter: str | None = None
    heading_block = False
    heading_buf: list[str] = []

    for raw in body_lines:
        raise_if_cancelled(cancel_check, READING_CANCELLED)
        line = raw[1:] if raw.startswith("　") else raw  # strip one indent
        line = _resolve_gaiji(line)
        # Ruby stripping is unconditional (any 《…》), so only on the Aozora path
        # — a plain novel's 《…》 is real punctuation, not a ruby reading (Y4).
        if aozora:
            line = _strip_ruby(line)
        text, inline, block_start, block_end = _strip_annotations(line)
        stripped = text.strip()

        if block_start:
            heading_block = True
            heading_buf = []

        if not stripped:  # blank or marker-only line: a break, no unit
            skipped += 1
            if block_end:
                heading_block = False
            continue

        if inline or (heading_block and stripped):
            if heading_block:
                heading_buf.append(stripped)
                current_chapter = "".join(heading_buf)
            else:
                current_chapter = stripped
            units.append(
                ReadingUnit(
                    text=stripped,
                    index=index,
                    location_label=current_chapter,
                    image_ref=None,
                )
            )
            index += 1
        else:
            para_no += 1
            label = current_chapter if current_chapter else f"¶{para_no}"
            for sentence in split_sentences(text, rules=rules):
                raise_if_cancelled(cancel_check, READING_CANCELLED)
                units.append(
                    ReadingUnit(
                        text=sentence,
                        index=index,
                        location_label=label,
                        image_ref=None,
                    )
                )
                index += 1

        if block_end:
            heading_block = False

    return units, para_no, skipped


# --- parts of an over-cap file -------------------------------------------


def _line_too_long(chunk: bytes, limit: int) -> bool:
    # A short read without a newline is end of file, never an error.
    return len(chunk) == limit and not chunk.endswith(b"\n")


def _long_line_error(name: str) -> SetupError:
    # A file with CR-only line ends has no b"\n" at all, so it lands here too.
    return SetupError(f"'{name}' has a line over 1 MB, so it can't be mined in parts.")


def _read_part(f: BinaryIO, name: str, start: int, end: int | None) -> bytes:
    """Read one part: every line whose first byte lies in ``[start, end)``.

    Neighbouring parts meet from both sides of one boundary. The part before
    reads the line crossing it to its end; this part skips that line. The skip
    starts one byte earlier and reads one byte more, so both stop on the same
    byte: a boundary line lands whole in one part, or is too long for both.
    """
    if start > 0:
        f.seek(start - 1)
        if _line_too_long(f.readline(_LINE_SLACK + 1), _LINE_SLACK + 1):
            raise _long_line_error(name)
    if end is None:
        body = f.read(_MAX_TEXT_FILE_BYTES + 1)
        if len(body) > _MAX_TEXT_FILE_BYTES:
            raise SetupError(f"'{name}' is too large to mine (over 32 MB).")
        return body
    body = f.read(max(0, end - f.tell()))
    # An empty body means the skipped line ran past this part's end: the line
    # crossing it started in an earlier part, which owns it.
    if body and not body.endswith(b"\n"):
        tail = f.readline(_LINE_SLACK)
        if _line_too_long(tail, _LINE_SLACK):
            raise _long_line_error(name)
        body += tail
    return body


def _parts_of(ref: ReadingSourceRef) -> list[ReadingSourceRef] | None:
    if ref.kind != "txt" or ref.path is None or ref.byte_range is not None:
        return None
    try:
        size = ref.path.stat().st_size
        if size <= _MAX_TEXT_FILE_BYTES:
            return None
        with ref.path.open("rb") as f:
            if f.read(2) in _UTF16_BOMS:
                return None
    except OSError:
        return None
    count = size // _PART_BYTES
    log_summary(logger, "Reading split", file=ref.path, size=size, parts=count)
    return [
        dataclasses.replace(
            ref,
            title=f"{ref.title} ({i + 1}/{count})",
            byte_range=(i * _PART_BYTES, (i + 1) * _PART_BYTES if i < count - 1 else None),
        )
        for i in range(count)
    ]


def split_oversize(refs: Iterable[ReadingSourceRef]) -> list[ReadingSourceRef]:
    """Replace each ``.txt`` ref over the cap with its parts, in file order.

    Every part is ``_PART_BYTES`` long except the last, which runs to end of
    file and so also takes whatever was appended after the ``stat()``. Parts
    are titled ``"<stem> (i/n)"``, which is what their cards' Source names.

    Reads nothing but a ``stat()`` and two bytes, so the Novels tab can call it
    on the GUI thread. Everything else passes through unchanged: other kinds,
    files within the cap, a path that can't be read (the loader reports it when
    the item runs), and UTF-16 files (see ``_UTF16_BOMS``), which then fail once
    with the over-cap message.

    Deliberately not part of ``detector.detect``: Audiobook Sync takes
    ``detect()[0]`` as the whole book, and would silently get part 1 alone.
    """
    out: list[ReadingSourceRef] = []
    for ref in refs:
        out.extend(_parts_of(ref) or [ref])
    return out


# --- public API ----------------------------------------------------------


def load(
    ref: ReadingSourceRef,
    *,
    cancel_check: Callable[[], bool] | None = None,
    encodings: tuple[str, ...] | None = None,
    rules: SentenceRules | None = None,
    script_check: Callable[[str], bool] | None = None,
) -> ReadingDocument:
    """Load an Aozora or plain-text novel into a book ``ReadingDocument``.

    ``encodings`` is the mining language's decode ladder; ``None`` keeps the
    built-in Japanese sniffing path (see ``_util._decode``). ``rules`` is that
    language's sentence-splitting policy; ``None`` is the built-in Japanese one.
    ``script_check`` validates a single-byte ladder leg (``_util._decode``).

    A ref with a ``byte_range`` is one part of an over-cap file
    (:func:`split_oversize`). It reads only its own lines and decodes them on
    their own. Only the first part drops the Aozora header and only the last
    cuts the colophon, as on the whole file: a corpus of concatenated books has
    one 底本 colophon per book, and a per-part cut would drop the next book's
    opening. A part keeps its ``"<stem> (i/n)"`` title so its cards name it.
    """
    raise_if_cancelled(cancel_check, READING_CANCELLED)
    # Per-kind ref contract: file-backed kinds always carry a path.
    assert ref.path is not None
    try:
        if ref.byte_range is not None:
            with ref.path.open("rb") as f:
                raw = _read_part(f, ref.path.name, *ref.byte_range)
            raise_if_cancelled(cancel_check, READING_CANCELLED)
        else:
            size = ref.path.stat().st_size
            if size > _MAX_TEXT_FILE_BYTES:
                raise SetupError(f"'{ref.path.name}' is too large to mine (over 32 MB).")
            with ref.path.open("rb") as f:
                raw = f.read(_MAX_TEXT_FILE_BYTES + 1)
            raise_if_cancelled(cancel_check, READING_CANCELLED)
            if len(raw) > _MAX_TEXT_FILE_BYTES:
                raise SetupError(f"'{ref.path.name}' is too large to mine (over 32 MB).")
    except OSError as e:
        logger.debug("Aozora read failed: file=%s error=%s detail=%s", ref.path, type(e).__name__, e)
        raise SetupError(f"Cannot read novel file '{ref.path.name}': {e}") from e
    first_part = ref.byte_range is None or ref.byte_range[0] == 0
    last_part = ref.byte_range is None or ref.byte_range[1] is None
    text = _decode(raw, encodings=encodings, script_check=script_check)
    lines = _splitlines(text)
    if last_part:
        lines = _cut_footer(lines)

    aozora = _is_aozora(text)
    title = ref.title
    body_lines = lines
    if aozora and first_part:
        header_title, body_lines = _extract_header(lines)
        if ref.byte_range is None:
            title = header_title or ref.title

    units, paragraphs, skipped = _emit_units(
        body_lines,
        aozora=aozora,
        cancel_check=cancel_check,
        rules=rules,
    )
    doc = ReadingDocument(
        title=title,
        kind="book",
        series="Books",
        episode=title,
        units=units,
    )
    log_summary(
        logger,
        "Aozora parse",
        file=ref.path,
        byte_range=ref.byte_range,
        paragraphs=paragraphs,
        units=len(units),
        chars=sum(len(unit.text) for unit in units),
        skipped=skipped,
    )
    return doc
