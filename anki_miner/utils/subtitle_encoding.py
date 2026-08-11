"""Shared pysubs2 encoding fallback (BOM, cp932, validated EUC-JP, detector).

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
"""

from __future__ import annotations

import codecs
from pathlib import Path

import pysubs2

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


def load_with_fallback_encoding(path: str | Path, original_error: UnicodeDecodeError) -> pysubs2.SSAFile:
    """Retry loading *path* from its BOM, cp932, EUC-JP, then detection (D10).

    UTF-16/UTF-32 BOMs are authoritative and checked before cp932 because their
    NUL-interleaved bytes can decode as cp932 without producing usable cues.
    For BOM-free input, cp932 is tried before the charset-normalizer detector
    on purpose: the detector confidently mis-detects real cp932 Japanese as
    ``cp949`` and decodes it *without* raising (silent mojibake), so for the
    app's dominant non-UTF-8 input the explicit cp932 attempt must win first.
    Only if cp932 itself raises :class:`UnicodeDecodeError` do we try EUC-JP.
    That candidate must decode to Japanese text before it can win; otherwise
    unrestricted detection follows. If that also fails, *original_error* (the
    UTF-8 error) is raised.
    """
    path = Path(path)
    if original_error.object.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        return pysubs2.load(str(path), encoding="utf_32")
    if original_error.object.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return pysubs2.load(str(path), encoding="utf_16")
    try:
        return pysubs2.load(str(path), encoding="cp932")
    except UnicodeDecodeError:
        pass
    if _is_japanese_euc_jp(path):
        return pysubs2.load(str(path), encoding="euc_jp")
    encoding = _detect_encoding(path)
    if encoding:
        try:
            return pysubs2.load(str(path), encoding=encoding)
        except (UnicodeDecodeError, LookupError):
            pass
    raise original_error


def detect_subtitle_encoding(path: str | Path) -> str | None:
    """Return the WHATWG encoding label for *path*, or None when unsure.

    Runs the same precedence as :func:`load_with_fallback_encoding` — UTF-8,
    then BOM, then cp932, then validated EUC-JP, then the charset-normalizer
    detector — but reports the encoding's *name* instead of a parsed file, for
    callers that must declare it to an external tool.

    None means "could not name it confidently"; callers must then omit the
    declaration rather than guess, because naming the wrong encoding is worse
    than letting the consumer detect. A detected encoding outside
    :data:`_WHATWG_LABELS` also yields None for the same reason.
    """
    path = Path(path)
    try:
        head = path.read_bytes()
    except OSError:
        return None

    if head.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        # WHATWG has no UTF-32 label; let the consumer work it out.
        return None
    if head.startswith(codecs.BOM_UTF16_LE):
        return "utf-16le"
    if head.startswith(codecs.BOM_UTF16_BE):
        return "utf-16be"

    for candidate in ("utf-8", "cp932"):
        try:
            head.decode(candidate)
        except UnicodeDecodeError:
            continue
        return _WHATWG_LABELS[candidate]

    if _is_japanese_euc_jp_bytes(head):
        return "euc-jp"

    detected = _detect_encoding(path)
    if detected is None:
        return None
    return _WHATWG_LABELS.get(detected.lower().replace(" ", ""))


def _is_japanese_euc_jp(path: Path) -> bool:
    try:
        data = path.read_bytes()
    except OSError:
        return False
    return _is_japanese_euc_jp_bytes(data)


def _is_japanese_euc_jp_bytes(data: bytes) -> bool:
    try:
        text = data.decode("euc_jp")
    except UnicodeDecodeError:
        return False
    return any(
        0x3040 <= ord(char) <= 0x30FF
        or 0xFF66 <= ord(char) <= 0xFF9F
        or 0x3400 <= ord(char) <= 0x9FFF
        or 0xF900 <= ord(char) <= 0xFAFF
        for char in text
    )


def _detect_encoding(path: Path) -> str | None:
    """Best-guess encoding for *path* via charset-normalizer, or None.

    charset-normalizer is soft-imported so its absence simply means the
    detector leg of :func:`load_with_fallback_encoding` is skipped (for
    BOM-free input, the cp932 attempt there runs first and independently).
    """
    try:
        from charset_normalizer import from_path
    except ImportError:
        return None
    match = from_path(str(path)).best()
    return match.encoding if match is not None else None
