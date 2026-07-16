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

import re
import unicodedata

from anki_miner.models.reading import (
    ReadingDocument,
    ReadingSourceRef,
    ReadingUnit,
)

# _decode's canonical home is _util (shared with subtitle_source); imported
# here both for load() and as a re-export for tests that patch/call it.
from anki_miner.services.reading._util import _decode
from anki_miner.services.reading.sentence_splitter import split_sentences

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
            return chr(int(m.group(1), 16))
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

# A ruby span *attached* to base text: the ｜ base-marker or a kanji/kana run
# directly before 《. A bare, standalone 《…》 (a plain novel using the double-
# angle bracket as title/quotation punctuation) has whitespace / line-start /
# punctuation before 《 and is NOT ruby — so it must not, on its own, mark a
# file as Aozora (Bug Y4: doing so dropped the first block as a "header" and
# stripped every 《…》 span silently).
_RUBY_ATTACHED_RE = re.compile(r"[｜々぀-ヿ一-鿿]《")


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
    ``［＃``, ruby *attached* to a kanji/kana base, or a header ruler line.
    """
    if "［＃" in text:
        return True
    if _RUBY_ATTACHED_RE.search(text):
        return True
    return any(_RULE_RE.match(ln) for ln in _splitlines(text))


def _extract_header(lines: list[str]) -> tuple[str, list[str]]:
    """Return (header title, body lines) for the Aozora path."""
    title = _strip_ruby(_resolve_gaiji(lines[0])).strip() if lines else ""
    i = 0
    while i < len(lines) and lines[i].strip():  # pre-blank block = header
        i += 1
    while i < len(lines) and not lines[i].strip():  # skip the blank gap
        i += 1
    return title, _drop_symbol_block(lines[i:])


# --- unit emission -------------------------------------------------------


def _emit_units(body_lines: list[str], aozora: bool = True) -> list[ReadingUnit]:
    units: list[ReadingUnit] = []
    index = 0
    para_no = 0
    current_chapter: str | None = None
    heading_block = False
    heading_buf: list[str] = []

    for raw in body_lines:
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
            for sentence in split_sentences(text):
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

    return units


# --- public API ----------------------------------------------------------


def load(ref: ReadingSourceRef) -> ReadingDocument:
    """Load an Aozora or plain-text novel into a book ``ReadingDocument``."""
    # Per-kind ref contract: file-backed kinds always carry a path.
    assert ref.path is not None
    text = _decode(ref.path.read_bytes())
    lines = _cut_footer(_splitlines(text))

    aozora = _is_aozora(text)
    if aozora:
        title, body_lines = _extract_header(lines)
        title = title or ref.title
    else:
        title = ref.title
        body_lines = lines

    return ReadingDocument(
        title=title,
        kind="book",
        series="Books",
        episode=title,
        units=_emit_units(body_lines, aozora=aozora),
    )
