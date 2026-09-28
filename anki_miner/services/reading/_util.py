"""Shared pure-stdlib helpers for the reading-tab source loaders.

Internal-but-tested: this private module (leading underscore) is imported directly by
``tests/unit/reading/test_reading_util.py``. The underscore stays and the module path
is a stable test surface; do not rename it.

One name is public and imported from outside the reading package:
:func:`decode_with_ladder`, the single ladder decoder the word lists and the
Manage Known Words importer share so the Big5-versus-GB18030 rule exists once.
"""

from __future__ import annotations

import codecs
import logging
import re
import unicodedata
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from anki_miner.exceptions import SetupError
from anki_miner.utils.cjk_encoding import prefers_big5
from anki_miner.utils.subtitle_encoding import (
    big5_family_codec,
    is_single_byte_codec,
    plausible_single_byte_text,
)

if TYPE_CHECKING:
    from anki_miner.languages.profile import SentenceRules

logger = logging.getLogger(__name__)

# Cap on a ``.mokuro`` sidecar JSON read. A whole-volume OCR sidecar is a few
# MB in practice; 64 MiB is far above any real volume while still bounding a
# hostile multi-GB file (mirrors the Yomitan importer's capped index.json peek).
MAX_MOKURO_JSON_BYTES = 64 * 1024 * 1024

# The message every reading source passes to raise_if_cancelled. One constant
# so the five loaders (subtitle, mokuro, aozora, text, epub) can't drift from
# each other one call site at a time.
READING_CANCELLED = "Reading load cancelled"


def read_text_capped(path: Path, cap: int, description: str) -> str:
    """UTF-8 ``read_text`` with a stat-before-read size gate.

    Raises :class:`SetupError` when the on-disk size exceeds ``cap`` or the file
    is not valid UTF-8; ``OSError`` from ``stat``/``read_text`` propagates for
    the caller's existing wrapping.
    """
    size = path.stat().st_size
    if size > cap:
        raise SetupError(f"{description} '{path.name}' is too large to mine.")
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        logger.debug(
            "Reading text decode failed: file=%s error=%s detail=%s",
            path,
            type(e).__name__,
            e,
        )
        raise SetupError(f"{description} '{path.name}' is not valid UTF-8.") from e


def read_zip_member_text_capped(
    archive: Path,
    entry: str,
    cap: int,
    description: str,
    *,
    log_failures: bool = True,
) -> str:
    """UTF-8 read of one archive member with a declared-size gate.

    Mirrors :func:`read_text_capped` for archive members: the ZipInfo's
    declared ``file_size`` is checked against ``cap`` before any bytes are
    read (CPython enforces the declared size/CRC at read time, so a lying
    header can't overshoot the gate). Every failure mode — missing/corrupt
    archive, missing member, over-cap member, non-UTF-8 bytes — raises
    :class:`SetupError` so callers get one exception type to wrap or skip.
    ``log_failures=False`` lets a hot-loop caller centralize and cap the same
    translated diagnostic without changing which exception is raised.
    """
    try:
        with zipfile.ZipFile(archive) as zf:
            try:
                info = zf.getinfo(entry)
            except KeyError as exc:
                if log_failures:
                    logger.debug(
                        "Reading archive member missing: archive=%s entry=%s error=%s detail=%s",
                        archive,
                        entry,
                        type(exc).__name__,
                        exc,
                    )
                raise SetupError(f"{description} '{entry}' not found in '{archive.name}'.") from None
            if info.file_size > cap:
                raise SetupError(f"{description} '{entry}' is too large to mine.")
            raw = zf.read(entry)
    except (zipfile.BadZipFile, OSError) as e:
        if log_failures:
            logger.debug(
                "Reading archive text failed: archive=%s entry=%s error=%s detail=%s",
                archive,
                entry,
                type(e).__name__,
                e,
            )
        raise SetupError(f"Cannot read '{archive.name}': {e}") from e
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as e:
        if log_failures:
            logger.debug(
                "Reading archive text decode failed: archive=%s entry=%s error=%s detail=%s",
                archive,
                entry,
                type(e).__name__,
                e,
            )
        raise SetupError(f"{description} '{entry}' in '{archive.name}' is not valid UTF-8.") from e


_NUM_RE = re.compile(r"(\d+)")


def natural_sort_key(s: str) -> list[int | str]:
    """Classic natural-sort key: digit runs compare numerically.

    Splitting on a captured ``(\\d+)`` yields alternating text/number chunks;
    numeric chunks are int-cast so "Vol2" sorts before "Vol10".
    """
    return [int(chunk) if chunk.isdigit() else chunk for chunk in _NUM_RE.split(s)]


# --- line wraps (shared by the aozora, text and epub loaders) --------------


def _line_join(rules: SentenceRules | None) -> str:
    """What a source line wrap becomes inside a paragraph for this language."""
    return " " if rules is not None and rules.space_aware else ""


def join_hard_wraps(lines: list[str], rules: SentenceRules | None) -> list[str]:
    """Join each run of non-blank plain-text lines into one paragraph line.

    Plain text in a space-delimited language (a Project Gutenberg ``.txt``, a
    paste of one) is hard-wrapped at about 70 columns with a blank line between
    paragraphs, so a physical line is a sentence fragment: split alone, it
    becomes a half-sentence card. Each run is stripped line by line and joined
    with :func:`_line_join`; blank lines stay, so the caller still counts them.

    Text in a language that is not ``space_aware`` (CJK, Thai) comes back
    unchanged: CJK plain text writes a paragraph per line, often with no blank
    line anywhere, so there a physical line already is the paragraph.
    """
    joiner = _line_join(rules)
    if not joiner:
        return lines
    out: list[str] = []
    run: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped:
            run.append(stripped)
            continue
        if run:
            out.append(joiner.join(run))
            run = []
        out.append(line)
    if run:
        out.append(joiner.join(run))
    return out


# --- decoding (shared by the aozora and subtitle loaders) ------------------


def _is_jp(ch: str) -> bool:
    o = ord(ch)
    return (
        0x3040 <= o <= 0x30FF  # hiragana + katakana
        or 0xFF66 <= o <= 0xFF9F  # halfwidth katakana letters + marks
        or 0x3400 <= o <= 0x9FFF  # CJK ideographs (+ ext A)
        or 0xF900 <= o <= 0xFAFF  # CJK compatibility ideographs
    )


_EUC_JP_HIRAGANA_AS_CP932_RE = re.compile(r"(?:､[ｦ-ﾟ]){2,}")


def _jp_ratio(text: str) -> float:
    if not text:
        return 0.0
    score = sum(_is_jp(c) for c in text)
    # EUC-JP hiragana byte pairs decode under CP932 as repeated
    # halfwidth-comma + katakana pairs; discount that narrow signature.
    score -= sum(len(match.group()) // 2 for match in _EUC_JP_HIRAGANA_AS_CP932_RE.finditer(text))
    return score / len(text)


def decode_with_ladder(
    raw: bytes,
    *,
    encodings: tuple[str, ...],
    script_check: Callable[[str], bool] | None = None,
) -> tuple[str, str]:
    """Decode *raw* against *encodings*, first success wins; report the winner.

    The one ladder decoder in the app: the reading loaders, the word lists and
    the Manage Known Words importer all come through here, so the Big5 rule
    below is written once. *encodings* is the mining language's
    ``get_profile(...).import_encodings``; a caller with a default of its own
    resolves it before calling, because ``()`` is an EMPTY ladder and never a
    "use the default" sentinel.

    Exactly ordered first-success, with no Japanese heuristic on top: nothing
    here knows which of several successful decodes of another language's bytes
    is the right one. Exhausting the ladder raises rather than returning a
    replacement-character string, because a novel that decoded to U+FFFD noise
    would mine into cards.

    A single-byte leg (cp1252, cp1258, …) must also pass
    ``plausible_single_byte_text`` with *script_check* — such codecs almost never
    raise — and its result is NFC-composed: cp1258 decodes Vietnamese to
    combining sequences, and the decoded text becomes the card sentence.

    ``prefers_big5`` weighs the whole input, so a Big5 file under roughly ten
    words scores below its threshold and gb18030 still wins it.

    The winning encoding comes back with the text: mojibake is reported as "the
    words are wrong", never as an encoding name, so a caller's receipt cannot
    name the leg that produced it unless this says which one won.

    A UTF-32/UTF-16 BOM replaces the ladder, the precedence the subtitle loader
    (``utils/subtitle_encoding.py``) applies: every single-byte leg decodes
    UTF-16 (Excel's "Unicode Text") without raising, into NUL-riddled words
    that pass its plausibility check. UTF-32 goes first because its
    little-endian BOM starts with UTF-16's.
    """
    if raw.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        encodings = ("utf_32",)
    elif raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        encodings = ("utf_16",)
    for encoding in encodings:
        # gb18030 accepts every valid Big5 sequence and decodes it into PUA
        # garbage without raising, so first-success could never reach a big5
        # leg further down. Step over gb18030 only when its own result
        # carries that signature; a real GB18030 file scores zero and is
        # unaffected. Reordering the ladder instead would mis-decode the GB
        # majority, which decodes cleanly (and wrongly) under big5.
        # The FAMILY, not the literal "big5": yue's ladder names big5hkscs,
        # and a literal test would leave this guard dead for it.
        if encoding == "gb18030":
            big5_codec = big5_family_codec(encodings)
            if big5_codec is not None and prefers_big5(raw, big5_codec):
                continue
        try:
            text = raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
        if is_single_byte_codec(encoding):
            if not plausible_single_byte_text(text, script_check):
                continue
            return unicodedata.normalize("NFC", text), encoding
        return text, encoding
    raise SetupError("Text encoding could not be detected.")


def _decode(
    raw: bytes,
    *,
    encodings: tuple[str, ...] | None = None,
    script_check: Callable[[str], bool] | None = None,
) -> str:
    """Decode bytes: BOM sniff → strict utf-8 → cp932/euc_jp (JP-ratio tiebreak).

    ``encodings`` is the mining language's ``get_profile(...).import_encodings``
    and hands the decode to :func:`decode_with_ladder`. ``None`` — never ``()``,
    which is an EMPTY ladder — selects the built-in Japanese path below,
    unchanged, and is what every Japanese call site passes.

    The two are not interchangeable for Japanese, and that is why the sentinel
    exists rather than ja simply handing over its own ladder: the built-in path
    carries a UTF-16 BOM branch and the cp932-versus-EUC-JP tiebreak, and EUC-JP
    bytes usually decode *without error* as cp932. A first-success ladder would
    stop at that mojibake, so ``("utf-8-sig", "cp932", "euc_jp")`` is a different
    decoder from this one, not a spelling of it.
    """
    if encodings is not None:
        return decode_with_ladder(raw, encodings=encodings, script_check=script_check)[0]
    if raw[:3] == b"\xef\xbb\xbf":
        return raw.decode("utf-8-sig")
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16")  # BOM picks endianness
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    candidates: list[str] = []
    for enc in ("cp932", "euc_jp"):
        try:
            candidates.append(raw.decode(enc))
        except UnicodeDecodeError:
            continue
    if not candidates:
        return raw.decode("cp932", errors="replace")
    if len(candidates) == 1:
        return candidates[0]
    return max(candidates, key=_jp_ratio)
