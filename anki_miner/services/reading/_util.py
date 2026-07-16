"""Shared pure-stdlib helpers for the reading-tab source loaders.

Internal-but-tested: this private module (leading underscore) has no public facade —
``tests/unit/reading/test_reading_util.py`` imports it directly. The underscore stays
and the module path is a stable test surface; do not rename it.
"""

from __future__ import annotations

import re
from pathlib import Path

from anki_miner.exceptions import SetupError

# Cap on a ``.mokuro`` sidecar JSON read. A whole-volume OCR sidecar is a few
# MB in practice; 64 MiB is far above any real volume while still bounding a
# hostile multi-GB file (mirrors the Yomitan importer's capped index.json peek).
MAX_MOKURO_JSON_BYTES = 64 * 1024 * 1024


def read_text_capped(path: Path, cap: int, description: str) -> str:
    """UTF-8 ``read_text`` with a stat-before-read size gate.

    Raises :class:`SetupError` when the on-disk size exceeds ``cap``; ``OSError``
    from ``stat``/``read_text`` propagates for the caller's existing wrapping.
    """
    size = path.stat().st_size
    if size > cap:
        raise SetupError(f"{description} '{path.name}' is {size:,} bytes (cap {cap:,}); refusing to load.")
    return path.read_text(encoding="utf-8")


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
    in nested components too, e.g. ``foo/__MACOSX/bar.jpg``.
    """
    return any(part in JUNK_NAMES for part in name.replace("\\", "/").split("/") if part)


# --- decoding (shared by the aozora and subtitle loaders) ------------------


def _is_jp(ch: str) -> bool:
    o = ord(ch)
    return (
        0x3040 <= o <= 0x30FF  # hiragana + katakana
        or 0x3400 <= o <= 0x9FFF  # CJK ideographs (+ ext A)
        or 0xF900 <= o <= 0xFAFF  # CJK compatibility ideographs
    )


def _jp_ratio(text: str) -> float:
    return sum(_is_jp(c) for c in text) / len(text) if text else 0.0


def _decode(raw: bytes) -> str:
    """Decode bytes: BOM sniff → strict utf-8 → cp932/euc_jp (JP-ratio tiebreak)."""
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
