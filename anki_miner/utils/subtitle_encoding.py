"""Shared pysubs2 encoding fallback (cp932-first, then detector).

Japanese subtitle files are frequently cp932/Shift-JIS, but pysubs2 defaults to
UTF-8 and raises :class:`UnicodeDecodeError` on them. Both the Audio Condenser
(``services/audio_condenser.py``) and the mining parser
(``services/subtitle_parser.py``) do a UTF-8 ``pysubs2.load`` first — keeping
their own patchable seam and per-call-site exception handling — and, on a UTF-8
decode failure, delegate the retry to :func:`load_with_fallback_encoding` here so
the fallback chain lives in exactly one place.
"""

from __future__ import annotations

from pathlib import Path

import pysubs2


def load_with_fallback_encoding(path: str | Path, original_error: UnicodeDecodeError) -> pysubs2.SSAFile:
    """Retry loading *path* with cp932, then a detected encoding (D10).

    cp932 is tried before the charset-normalizer detector on purpose: the
    detector confidently mis-detects real cp932 Japanese as ``cp949`` and
    decodes it *without* raising (silent mojibake), so for the app's dominant
    non-UTF-8 input the explicit cp932 attempt must win first. Only if cp932
    itself raises :class:`UnicodeDecodeError` do we consult the (soft-imported)
    detector; if that also fails, *original_error* (the UTF-8 error) is raised.
    """
    path = Path(path)
    try:
        return pysubs2.load(str(path), encoding="cp932")
    except UnicodeDecodeError:
        pass
    encoding = _detect_encoding(path)
    if encoding:
        try:
            return pysubs2.load(str(path), encoding=encoding)
        except (UnicodeDecodeError, LookupError):
            pass
    raise original_error


def _detect_encoding(path: Path) -> str | None:
    """Best-guess encoding for *path* via charset-normalizer, or None.

    charset-normalizer is soft-imported so its absence simply means the
    detector leg of :func:`load_with_fallback_encoding` is skipped (the cp932
    attempt there runs first and independently).
    """
    try:
        from charset_normalizer import from_path
    except ImportError:
        return None
    match = from_path(str(path)).best()
    return match.encoding if match is not None else None
