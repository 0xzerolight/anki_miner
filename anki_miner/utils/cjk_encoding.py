"""Big5-versus-GB18030 disambiguation for the two decode ladders.

Stdlib only, and deliberately its own module: both the subtitle ladder
(``utils/subtitle_encoding.py``) and the reading-source ladder
(``services/reading/_util.py``) need this, and the reading path must not pull
``pysubs2`` in to get it.

The problem it solves. Every ordered first-success ladder that lists gb18030
before big5 — the zh profile's ``("utf-8-sig", "gb18030", "big5")`` — can never
reach its big5 leg: gb18030 accepts essentially every valid Big5 byte sequence
and decodes it, without raising, into private-use-area garbage. A Traditional
Chinese subtitle therefore mined as mojibake and the big5 leg was dead code.

Reordering is not the fix: GB18030 bytes also decode cleanly under big5 (into
different, PUA-clean garbage), so a big5-first ladder would silently mis-decode
the majority of Chinese content instead. What separates the two is the
signature — a Big5 file read as gb18030 lands a large share of its characters
in the BMP private use area, while a real GB18030 file lands none there.

Shape and conservatism mirror ``subtitle_encoding._is_japanese_euc_jp_bytes``:
a sniffed head, tolerance for a multi-byte sequence cut at the bound, and a
guard that only ever *adds* a leg the ladder could not otherwise reach.
"""

from __future__ import annotations

#: Share of a decode's NON-ASCII characters that must land in the BMP private
#: use area before gb18030 is treated as mojibake. The denominator excludes
#: ASCII on purpose: a subtitle head is mostly cue numbers, timestamps and
#: markup, which would otherwise dilute the signal below any useful threshold.
#: A genuine GB18030 file scores 0.0 (the codec maps nothing there for ordinary
#: text), a Big5 file read as gb18030 scores 0.2-0.6, so 5% is far outside the
#: noise a stray custom-glyph character could produce.
_PUA_MOJIBAKE_RATIO = 0.05


def _decode_tolerating_truncation(data: bytes, encoding: str) -> str | None:
    """Decode *data*, forgiving a multi-byte sequence cut at the very end.

    ``None`` means "these bytes are not this encoding". Keyed on
    ``exc.object`` rather than *data* for the same reason the subtitle ladder
    is: a BOM-stripping codec hands the delegate different bytes, so absolute
    offsets against *data* can never reach its length.
    """
    try:
        return data.decode(encoding)
    except UnicodeDecodeError as exc:
        if exc.end != len(exc.object):
            return None  # a genuine invalid byte, not a truncation artifact
        try:
            return exc.object[: exc.start].decode(encoding)
        except UnicodeDecodeError:
            return None
    except LookupError:
        return None


def _pua_share(text: str) -> float:
    """Fraction of *text*'s non-ASCII characters sitting in the BMP PUA."""
    non_ascii = [char for char in text if ord(char) > 0x7F]
    if not non_ascii:
        return 0.0
    return sum(1 for char in non_ascii if 0xE000 <= ord(char) <= 0xF8FF) / len(non_ascii)


def prefers_big5(data: bytes) -> bool:
    """True when *data* is Big5 that a gb18030 leg would swallow into mojibake.

    Both halves must hold, so the guard cannot fire on anything but the case it
    exists for: the gb18030 reading is PUA-heavy **and** the big5 reading is
    PUA-clean. Anything else — a real GB18030 file, bytes neither codec
    accepts, a head too short to judge — comes back False and leaves the
    caller's ladder exactly as it was.
    """
    gb = _decode_tolerating_truncation(data, "gb18030")
    if gb is None or _pua_share(gb) <= _PUA_MOJIBAKE_RATIO:
        return False
    big5 = _decode_tolerating_truncation(data, "big5")
    return big5 is not None and _pua_share(big5) <= _PUA_MOJIBAKE_RATIO
