"""Shared pysubs2 encoding fallback (BOM, caller's ladder, detector).

Japanese subtitle files are frequently cp932/Shift-JIS, but pysubs2 defaults to
UTF-8 and raises :class:`UnicodeDecodeError` on them. Both the Audio Condenser
(``services/audio_condenser.py``) and the mining parser
(``services/subtitle_parser.py``) do a UTF-8 ``pysubs2.load`` first — keeping
their own patchable seam and per-call-site exception handling — and, on a UTF-8
decode failure, delegate the retry to :func:`load_with_fallback_encoding` here so
the fallback chain lives in exactly one place.

:func:`detect_subtitle_encoding` runs that same chain for a caller that needs the
*name* of the encoding rather than a parsed file — subtitle retiming, which
declares it to alass via ``--encoding-inc``.

The ladder itself belongs to the caller: a config-bearing site passes
``encodings=get_profile(config_language(config)).import_encodings``, so a Chinese
subtitle is tried against gb18030/big5 and a Korean one against cp949. The
"use the built-in ladder" sentinel is ``None``, never ``()`` — an empty tuple is
an EMPTY ladder that silently stops every non-UTF-8 subtitle decoding.
"""

from __future__ import annotations

import codecs
from pathlib import Path

import pysubs2

from anki_miner.utils.cjk_encoding import prefers_big5

#: Python codec name → WHATWG encoding label, for callers that must name an
#: encoding to a non-Python consumer. alass parses its ``--encoding-*`` values
#: with encoding_rs and **panics** (aborting the retime) on any label it does
#: not know: ``cp932`` and ``utf_8`` both panic where ``shift_jis`` and
#: ``utf-8`` work. Only names in this table are ever emitted; anything else
#: falls back to letting the consumer auto-detect. UTF-32 is deliberately
#: absent — WHATWG has no label for it.
_WHATWG_LABELS = {
    "utf-8": "utf-8",
    "utf_8": "utf-8",
    "utf8": "utf-8",
    "utf-8-sig": "utf-8",
    "utf_8_sig": "utf-8",
    "ascii": "utf-8",
    "cp932": "shift_jis",
    "shift_jis": "shift_jis",
    "shift-jis": "shift_jis",
    "sjis": "shift_jis",
    "ms932": "shift_jis",
    "euc_jp": "euc-jp",
    "euc-jp": "euc-jp",
    "cp949": "euc-kr",
    "euc_kr": "euc-kr",
    "euc-kr": "euc-kr",
    "gbk": "gbk",
    "gb2312": "gbk",
    "gb18030": "gb18030",
    "big5": "big5",
    "cp950": "big5",
    "cp1251": "windows-1251",
    "cp1252": "windows-1252",
    "latin_1": "windows-1252",
    "latin-1": "windows-1252",
    "iso8859_1": "windows-1252",
    "iso-8859-1": "windows-1252",
    "utf_16_le": "utf-16le",
    "utf-16le": "utf-16le",
    "utf_16_be": "utf-16be",
    "utf-16be": "utf-16be",
    "utf_16": "utf-16le",
    "utf-16": "utf-16le",
}

#: Bound on the head sniffed by :func:`detect_subtitle_encoding`. Real subtitle
#: files (even a heavily-styled multi-hour .ass) run tens to a few hundred KB;
#: 1 MiB comfortably covers those whole, so detection is unaffected. It only
#: matters for a mis-picked huge file (a video, an archive) — this caps that
#: case to one bounded read instead of decoding the whole thing twice over.
_MAX_SNIFF_BYTES = 1024 * 1024

#: Ladder used when a caller passes no ``encodings``. UTF-8 is absent from the
#: load ladder because it is the attempt the caller already made and lost —
#: that failure is what ``original_error`` holds.
_DEFAULT_LOAD_LADDER = ("cp932", "euc_jp")
_DEFAULT_DETECT_LADDER = ("utf-8", "cp932", "euc_jp")


def _read_head(path: Path) -> bytes:
    """The bounded head the load path sniffs; ``b""`` when *path* is unreadable.

    :func:`detect_subtitle_encoding` reads its own head instead of calling this:
    there an unreadable file must come back *unnameable* (None), not empty.
    """
    try:
        with path.open("rb") as f:
            return f.read(_MAX_SNIFF_BYTES)
    except OSError:
        return b""


def load_with_fallback_encoding(
    path: str | Path,
    original_error: UnicodeDecodeError,
    *,
    encodings: tuple[str, ...] | None = None,
) -> pysubs2.SSAFile:
    """Retry loading *path* from its BOM, the *encodings* ladder, then detection (D10).

    UTF-16/UTF-32 BOMs are authoritative and checked before the ladder because
    their NUL-interleaved bytes can decode as cp932 without producing usable
    cues. For BOM-free input each ladder entry is tried in order, before the
    charset-normalizer detector on purpose: the detector confidently
    mis-detects real cp932 Japanese as ``cp949`` and decodes it *without*
    raising (silent mojibake), so for the app's dominant non-UTF-8 input the
    explicit cp932 attempt must win first. An ``euc_jp`` entry is special-cased:
    that candidate must decode to Japanese text before it can win. If the whole
    ladder and the detector fail, *original_error* (the UTF-8 error) is raised.

    *encodings* is the caller's ladder — ``get_profile(...).import_encodings``
    at a config-bearing site. ``None`` (never ``()``) means the built-in
    Japanese ladder: cp932, then validated EUC-JP, then the detector.
    """
    path = Path(path)
    if original_error.object.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        return pysubs2.load(str(path), encoding="utf_32")
    if original_error.object.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return pysubs2.load(str(path), encoding="utf_16")

    ladder = _DEFAULT_LOAD_LADDER if encodings is None else encodings
    head: bytes | None = None
    for candidate in ladder:
        if candidate == "euc_jp":
            if head is None:
                head = _read_head(path)
            # EUC-JP only wins when it produces real Japanese: gb18030 bytes
            # decode as EUC-JP into plausible-looking kanji, so an ungated
            # attempt steals Chinese subtitles.
            if not _is_japanese_euc_jp_bytes(head):
                continue
            # The guard has already decoded these bytes as Japanese EUC-JP, so
            # this leg is committed: a failure past the sniffed head propagates
            # instead of falling through to the detector. Pre-ladder behaviour,
            # kept exactly.
            return pysubs2.load(str(path), encoding=candidate)
        if candidate == "gb18030" and "big5" in ladder:
            if head is None:
                head = _read_head(path)
            # gb18030 accepts every valid Big5 sequence and decodes it into PUA
            # garbage without raising, so first-success could never reach the
            # big5 leg. Step over gb18030 only when its own result carries that
            # signature; a real GB18030 file scores zero and is unaffected.
            if prefers_big5(head):
                continue
        try:
            return pysubs2.load(str(path), encoding=candidate)
        except (UnicodeDecodeError, LookupError):
            continue

    if head is None:
        head = _read_head(path)
    encoding = _detect_encoding(head)
    if encoding:
        try:
            return pysubs2.load(str(path), encoding=encoding)
        except (UnicodeDecodeError, LookupError):
            pass
    raise original_error


def detect_subtitle_encoding(path: str | Path, *, encodings: tuple[str, ...] | None = None) -> str | None:
    """Return the WHATWG encoding label for *path*, or None when unsure.

    Runs the same precedence as :func:`load_with_fallback_encoding` — BOM, then
    the *encodings* ladder, then the charset-normalizer detector — but reports
    the encoding's *name* instead of a parsed file, for callers that must
    declare it to an external tool. ``None`` (never ``()``) means the built-in
    Japanese ladder: UTF-8, cp932, validated EUC-JP.

    None means "could not name it confidently"; callers must then omit the
    declaration rather than guess, because naming the wrong encoding is worse
    than letting the consumer detect. A ladder entry or a detected encoding
    outside :data:`_WHATWG_LABELS` also yields None for the same reason.

    Only sniffs the first ``_MAX_SNIFF_BYTES`` of *path* — every real subtitle
    file fits inside that whole, so detection is unaffected; it just stops a
    mis-picked huge file from being read (and decoded, twice over) in full.
    """
    path = Path(path)
    try:
        with path.open("rb") as f:
            head = f.read(_MAX_SNIFF_BYTES)
    except OSError:
        return None

    if head.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        # WHATWG has no UTF-32 label; let the consumer work it out.
        return None
    if head.startswith(codecs.BOM_UTF16_LE):
        return "utf-16le"
    if head.startswith(codecs.BOM_UTF16_BE):
        return "utf-16be"

    ladder = _DEFAULT_DETECT_LADDER if encodings is None else encodings
    for candidate in ladder:
        if candidate == "euc_jp":
            if not _is_japanese_euc_jp_bytes(head):
                continue
            return _WHATWG_LABELS.get(candidate)
        # Same guard as the load path: gb18030 names itself for Big5 bytes
        # unless its own decode is PUA mojibake and big5's is clean.
        if candidate == "gb18030" and "big5" in ladder and prefers_big5(head):
            continue
        try:
            head.decode(candidate)
        except UnicodeDecodeError as exc:
            # Compare against ``exc.object``, never ``head``: a BOM-stripping
            # codec (utf_8_sig, the first leg of every profile ladder) hands
            # the delegate the bytes AFTER the BOM, so its offsets are relative
            # to those and can never reach len(head) when a BOM is present.
            # Keying on head made this whole retry unreachable, and a BOM'd
            # UTF-8 subtitle with a cut tail got named cp932/windows-1251.
            if exc.end != len(exc.object):
                continue  # a genuine invalid byte, not a truncation artifact
            # The bounded read can cut mid multi-byte sequence right at the
            # tail; that's an artifact of the bound, not proof this candidate
            # is wrong. Retry without the truncated tail before giving up on it.
            try:
                exc.object[: exc.start].decode(candidate)
            except UnicodeDecodeError:
                continue
        except LookupError:
            continue  # a ladder entry Python has no codec for
        return _WHATWG_LABELS.get(candidate)

    detected = _detect_encoding(head)
    if detected is None:
        return None
    return _WHATWG_LABELS.get(detected.lower().replace(" ", ""))


def _is_japanese_euc_jp_bytes(data: bytes) -> bool:
    """True iff *data* decodes as EUC-JP and contains real Japanese script.

    *data* is a caller-owned, already-bounded head (never a full file read —
    both callers hold one already). EUC-JP is mostly double-byte, so a
    bounded head can cut mid-character right at the tail; same truncation
    tolerance as the ladder checks in :func:`detect_subtitle_encoding`, and
    keyed on ``exc.object`` for the same reason (here euc_jp strips nothing, so
    it is the same bytes — the spelling is what keeps the two in step).
    """
    try:
        text = data.decode("euc_jp")
    except UnicodeDecodeError as exc:
        if exc.end != len(exc.object):
            return False  # a genuine invalid byte, not a truncation artifact
        try:
            text = exc.object[: exc.start].decode("euc_jp")
        except UnicodeDecodeError:
            return False
    return any(
        0x3040 <= ord(char) <= 0x30FF
        or 0xFF66 <= ord(char) <= 0xFF9F
        or 0x3400 <= ord(char) <= 0x9FFF
        or 0xF900 <= ord(char) <= 0xFAFF
        for char in text
    )


def _detect_encoding(data: bytes) -> str | None:
    """Best-guess encoding for *data* via charset-normalizer, or None.

    Takes bytes, never a path: both callers already hold a bounded
    ``_MAX_SNIFF_BYTES`` head, and ``from_path`` would read the whole file
    regardless of that bound — exactly the case the bound exists to avoid.

    charset-normalizer is soft-imported so its absence simply means the
    detector leg of :func:`load_with_fallback_encoding` is skipped (for
    BOM-free input, the cp932 attempt there runs first and independently).
    """
    try:
        from charset_normalizer import from_bytes
    except ImportError:
        return None
    match = from_bytes(data).best()
    return match.encoding if match is not None else None
