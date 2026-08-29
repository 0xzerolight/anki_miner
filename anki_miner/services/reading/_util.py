"""Shared pure-stdlib helpers for the reading-tab source loaders.

Internal-but-tested: this private module (leading underscore) has no public facade —
``tests/unit/reading/test_reading_util.py`` imports it directly. The underscore stays
and the module path is a stable test surface; do not rename it.
"""

from __future__ import annotations

import logging
import re
import zipfile
from pathlib import Path

from anki_miner.exceptions import SetupError
from anki_miner.utils.cjk_encoding import prefers_big5

logger = logging.getLogger(__name__)

# Cap on a ``.mokuro`` sidecar JSON read. A whole-volume OCR sidecar is a few
# MB in practice; 64 MiB is far above any real volume while still bounding a
# hostile multi-GB file (mirrors the Yomitan importer's capped index.json peek).
MAX_MOKURO_JSON_BYTES = 64 * 1024 * 1024


def read_text_capped(path: Path, cap: int, description: str) -> str:
    """UTF-8 ``read_text`` with a stat-before-read size gate.

    Raises :class:`SetupError` when the on-disk size exceeds ``cap`` or the file
    is not valid UTF-8; ``OSError`` from ``stat``/``read_text`` propagates for
    the caller's existing wrapping.
    """
    size = path.stat().st_size
    if size > cap:
        raise SetupError(f"{description} '{path.name}' is {size:,} bytes (cap {cap:,}); refusing to load.")
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
                raise SetupError(
                    f"{description} '{entry}' is {info.file_size:,} bytes (cap {cap:,}); refusing to load."
                )
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


# Listings junk dropped from both directory walks and archive namelists so the
# two paths filter identically (see is_junk_path). __MACOSX and $RECYCLE.BIN are
# directory components; .DS_Store and Thumbs.db are files.
JUNK_NAMES: frozenset[str] = frozenset({"__MACOSX", ".DS_Store", "Thumbs.db", "$RECYCLE.BIN"})

_NUM_RE = re.compile(r"(\d+)")


def natural_sort_key(s: str) -> list[int | str]:
    """Classic natural-sort key: digit runs compare numerically.

    Splitting on a captured ``(\\d+)`` yields alternating text/number chunks;
    numeric chunks are int-cast so "Vol2" sorts before "Vol10".
    """
    return [int(chunk) if chunk.isdigit() else chunk for chunk in _NUM_RE.split(s)]


def is_junk_path(name: str) -> bool:
    """True when any path component is OS/archive listing junk.

    Accepts a bare name or a ``/``- (or ``\\``-) separated path; matches junk
    in nested components too, e.g. ``foo/__MACOSX/bar.jpg``. Also drops macOS
    AppleDouble sidecars (``._Book.epub``), which mirror every file on a
    non-HFS volume and would otherwise spawn a failing per-book queue item.
    """
    return any(part in JUNK_NAMES or part.startswith("._") for part in name.replace("\\", "/").split("/") if part)


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


def _decode(raw: bytes, *, encodings: tuple[str, ...] | None = None) -> str:
    """Decode bytes: BOM sniff → strict utf-8 → cp932/euc_jp (JP-ratio tiebreak).

    ``encodings`` is the mining language's ``get_profile(...).import_encodings``.
    ``None`` — never ``()``, which is an EMPTY ladder — selects the built-in
    Japanese path above, unchanged, and is what every Japanese call site passes.

    The two are not interchangeable for Japanese, and that is why the sentinel
    exists rather than ja simply handing over its own ladder: the built-in path
    carries a UTF-16 BOM branch and the cp932-versus-EUC-JP tiebreak, and EUC-JP
    bytes usually decode *without error* as cp932. A first-success ladder would
    stop at that mojibake, so ``("utf-8-sig", "cp932", "euc_jp")`` is a different
    decoder from this one, not a spelling of it.

    A supplied ladder is exactly ordered first-success, with no Japanese
    heuristic on top: nothing here knows which of several successful decodes of
    another language's bytes is the right one. Exhausting it raises rather than
    returning a replacement-character string, because a novel that decoded to
    U+FFFD noise would mine into cards.
    """
    if encodings is not None:
        for encoding in encodings:
            # gb18030 accepts every valid Big5 sequence and decodes it into PUA
            # garbage without raising, so first-success could never reach a big5
            # leg further down. Step over gb18030 only when its own result
            # carries that signature; a real GB18030 file scores zero and is
            # unaffected. Reordering the ladder instead would mis-decode the GB
            # majority, which decodes cleanly (and wrongly) under big5.
            if encoding == "gb18030" and "big5" in encodings and prefers_big5(raw):
                continue
            try:
                return raw.decode(encoding)
            except (UnicodeDecodeError, LookupError):
                continue
        raise SetupError(f"Could not decode {len(raw):,} bytes as any of: {', '.join(encodings) or '(none)'}.")
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
