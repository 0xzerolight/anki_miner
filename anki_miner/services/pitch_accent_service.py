"""Service for loading and looking up pitch accent data."""

import csv
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from anki_miner.exceptions import SetupError
from anki_miner.utils.csv_utils import detect_delimiter, is_header_row

logger = logging.getLogger(__name__)

# Small kana that combine with the previous kana (not separate mora)
_COMBINING_KANA = set("ゃゅょぁぃぅぇぉゎャュョァィゥェォヮ")

# MeCab pos1 markers that make any downstep 起伏 (kifuku), per the NHK convention.
VERBAL_POS = {"動詞", "形容詞"}

# A pitch position encoded as an H/L mora-level string (e.g. "LHHH"), per the
# Yomitan term-meta-bank v3 schema ("^[HL]+$"). Distinguished from an integer
# downstep position so lookups accept either encoding.
_HL_PATTERN_RE = re.compile(r"^[HL]+$")

ROMAJI_CATEGORY = {
    "平板": "heiban",
    "頭高": "atamadaka",
    "中高": "nakadaka",
    "尾高": "odaka",
    "起伏": "kifuku",
}


@dataclass(frozen=True)
class PitchEntry:
    """A single (kanji, reading) pitch-accent record.

    Carries the fidelity later stages (e.g. the SVG pitch renderer) need:
    the raw drop-position ``pattern`` (integer positions like ``"0,2"`` or an
    ``[HL]+`` mora string like ``"LHHH"``), plus the nasal and devoiced mora
    positions parsed from the enriched 5-column CSV. ``nasal``/``devoice`` are
    empty for legacy 3-column data that predates the enrichment.
    """

    pattern: str
    nasal: tuple[int, ...] = ()
    devoice: tuple[int, ...] = ()


# Hiragana + katakana codepoint blocks (matches furigana_distribute's KANA_RANGES).
_KANA_RANGES = ((0x3040, 0x309F), (0x30A0, 0x30FF))


def _is_all_kana(text: str) -> bool:
    """True iff ``text`` is non-empty and every character is hiragana/katakana.

    Used to decide whether a missing reading may safely fall back to the surface
    form for mora counting (a kanji surface would mis-count mora).
    """
    return bool(text) and all(any(lo <= ord(ch) <= hi for lo, hi in _KANA_RANGES) for ch in text)


def count_mora(reading: str) -> int:
    """Count the number of mora in a Japanese kana reading.

    Each kana = 1 mora, except small combining kana (ゃゅょ etc.)
    which merge with the previous kana. Long vowel ー and small っ/ッ
    each count as 1 mora.

    Args:
        reading: Kana string.

    Returns:
        Number of mora.
    """
    return sum(1 for ch in reading if ch not in _COMBINING_KANA)


def downstep_positions(pitch_string: str) -> list[int]:
    """Convert an ``[HL]+`` mora string to its downstep position(s).

    Ported from Yomitan getDownstepPositions
    (ext/js/language/ja/japanese.js, upstream commit e2ed450). A downstep is any
    H→L transition; the position is the index of the L mora. When the string has
    no H→L transition, the sole "position" is 0 if it starts Low (heiban) else
    -1 (no resolvable downstep — the caller treats this as no category).

    Args:
        pitch_string: Mora-level pitch string, H high / L low (e.g. "LHHL").

    Returns:
        List of downstep positions (mora indices); ``[0]`` for heiban, ``[-1]``
        when unresolvable.
    """
    downsteps: list[int] = []
    for i in range(len(pitch_string)):
        if i > 0 and pitch_string[i - 1] == "H" and pitch_string[i] == "L":
            downsteps.append(i)
    if not downsteps:
        downsteps.append(0 if pitch_string.startswith("L") else -1)
    return downsteps


def _token_to_position(token: str) -> int | None:
    """Resolve one pattern token (int string or ``[HL]+``) to a downstep position.

    Mirrors Yomitan getPitchCategory's ``typeof pitchAccentValue === 'string'``
    branch: an H/L string is reduced to its first downstep position. Returns None
    for an unparseable token or an unresolvable H/L string (position ``-1``).
    """
    if _HL_PATTERN_RE.match(token):
        position = downstep_positions(token)[0]
        return position if position >= 0 else None
    try:
        return int(token)
    except ValueError:
        return None


def classify_pitch(position: int, mora_count: int, pos: str | None = None) -> str:
    """Classify pitch accent pattern into a category.

    Ported from Yomitan getPitchCategory
    (ext/js/language/ja/japanese.js, upstream commit e2ed450): after heiban,
    any downstep on a verb/adjective is 起伏 (kifuku) — the standard NHK
    convention — while 頭高/中高/尾高 apply to nominals only. MeCab pos1 is a
    stronger POS signal than Yomitan's JMdict-wordclass inference, so the
    verbal branch is one condition here.

    Args:
        position: Pitch drop position (0 = heiban).
        mora_count: Number of mora in the word.
        pos: Optional MeCab pos1 marker. Any downstep on a verbal POS
            (動詞/形容詞) yields 起伏; nominals follow the positional rules.

    Returns:
        Category string: 平板, 頭高, 中高, 尾高, or 起伏.
    """
    if position == 0:
        return "平板"
    if pos in VERBAL_POS and position > 0:
        return "起伏"
    if position == 1:
        return "頭高"
    if position == mora_count:
        return "尾高"
    return "中高"


def format_categories(pattern: str, reading: str, pos: str | None, fmt: str) -> str | None:
    """Map a raw pattern string (e.g. "0,2" or "LHHH") to a category list.

    Each comma-separated token is either an integer drop position or an
    ``[HL]+`` mora string; the H/L form is reduced to its downstep position
    before classification (following Yomitan getPitchCategory).

    Args:
        pattern: Comma-separated drop positions / H-L strings from the pitch CSV.
        reading: Kana reading used to derive mora count.
        pos: Optional MeCab pos1 marker (for kifuku/odaka split).
        fmt: "jp" for 平板/頭高/中高/尾高/起伏, "romaji" for heiban/atamadaka/...

    Returns:
        Comma-joined categories, or None if no parseable positions.
    """
    mora = count_mora(reading)
    out: list[str] = []
    for raw in pattern.split(","):
        token = raw.strip()
        if not token:
            continue
        position = _token_to_position(token)
        if position is None:
            continue
        jp = classify_pitch(position, mora, pos)
        out.append(ROMAJI_CATEGORY[jp] if fmt == "romaji" else jp)
    return ",".join(out) if out else None


@dataclass
class _ParsedRow:
    reading: str
    kanji: str
    entry: PitchEntry


def _parse_int_field(field_value: str) -> tuple[int, ...]:
    """Parse a comma-joined integer field (nasal/devoice) into a tuple.

    Non-integer tokens are dropped; an empty field yields ``()``.
    """
    out: list[int] = []
    for tok in field_value.split(","):
        tok = tok.strip()
        if not tok:
            continue
        try:
            out.append(int(tok))
        except ValueError:
            continue
    return tuple(out)


def _parse_pitch_row(row: list[str]) -> _ParsedRow | None:
    """Parse one CSV row into a :class:`_ParsedRow`, or None if unusable.

    Column-count driven (the header is optional, so a header sniff can't be
    trusted for the schema): exactly 5 fields → the enriched
    ``reading,kanji,pattern,nasal,devoice`` format; exactly 3 → the legacy
    ``reading,kanji,pattern`` format. Any other count >= 4 is treated as a
    hand-edited legacy comma file whose pattern held an intra-field comma
    (``0,2`` splits into 4 raw fields) — the pattern tail is rejoined so it
    round-trips, and nasal/devoice stay empty (never misread the tail into
    them). Files written by :func:`atomic_write_csv` always round-trip to
    exactly 3 or 5 columns, confining the ambiguity to hand-edited legacy files.
    """
    n = len(row)
    if n < 3:
        return None
    reading = row[0].strip()
    kanji = row[1].strip()
    if n == 5:
        pattern = row[2].strip()
        nasal = _parse_int_field(row[3])
        devoice = _parse_int_field(row[4])
    elif n == 3:
        pattern = row[2].strip()
        nasal = ()
        devoice = ()
    else:
        # Anomalous (n == 4 or n >= 6): hand-edited legacy comma file. Rejoin the
        # pattern tail so a bare "0,2" reads back as "0,2"; keep nasal/devoice
        # empty rather than misclassify the tail into them.
        pattern = ",".join(row[2:])
        nasal = ()
        devoice = ()
    return _ParsedRow(reading=reading, kanji=kanji, entry=PitchEntry(pattern, nasal, devoice))


class PitchAccentService:
    """Load and look up pitch accent patterns from CSV/TSV.

    Supports Kanjium-format and similar pitch accent files:
    - Legacy: ``reading, kanji, pitch_pattern`` (3 columns)
    - Enriched: ``reading, kanji, pattern, nasal, devoice`` (5 columns)
    - Tab or comma separated; header rows auto-skipped.

    Lookups are reading-scoped: entries are keyed on ``(surface, reading)`` so a
    homograph (弾く ひく[0] vs はじく[2]) resolves by the reading passed in rather
    than whichever row loaded first.
    """

    def __init__(self, pitch_accent_path: Path):
        """Initialize with path to pitch accent file.

        Args:
            pitch_accent_path: Path to the pitch accent CSV/TSV file.
        """
        self._path = pitch_accent_path
        # (surface, reading) -> PitchEntry ; surface = kanji, or reading when
        # the term is kana-only.
        self._by_pair: dict[tuple[str, str], PitchEntry] | None = None
        # surface -> all entries for that surface (homographs keep every reading)
        self._by_word: dict[str, list[PitchEntry]] = {}
        # reading -> first-wins entry (reading-only fallback + kana lookups)
        self._by_reading: dict[str, PitchEntry] = {}
        self._entry_count: int = 0

    @property
    def entry_count(self) -> int:
        """Number of distinct (surface, reading) pitch entries loaded."""
        return self._entry_count

    def load(self) -> bool:
        """Load pitch accent data from file.

        Returns:
            True if loaded successfully.

        Raises:
            SetupError: If the file is missing or unparseable.
        """
        if not self._path.exists():
            raise SetupError(
                f"Pitch accent file not found at: {self._path}. "
                f"Download pitch accent data and place it in ~/.anki_miner/"
            )

        by_pair: dict[tuple[str, str], PitchEntry] = {}
        by_word: dict[str, list[PitchEntry]] = {}
        by_reading: dict[str, PitchEntry] = {}
        try:
            with open(self._path, encoding="utf-8") as f:
                sample = f.read(4096)
                f.seek(0)
                delimiter = detect_delimiter(sample)

                reader = csv.reader(f, delimiter=delimiter)
                first_row = True
                for row in reader:
                    if len(row) < 3:
                        continue
                    if first_row:
                        first_row = False
                        if is_header_row(row):
                            continue
                    parsed = _parse_pitch_row(row)
                    if parsed is None:
                        continue
                    reading, kanji, entry = parsed.reading, parsed.kanji, parsed.entry
                    surface = kanji or reading
                    if not surface:
                        continue
                    key = (surface, reading)
                    if key in by_pair:
                        continue  # first occurrence wins
                    by_pair[key] = entry
                    by_word.setdefault(surface, []).append(entry)
                    if reading and reading not in by_reading:
                        by_reading[reading] = entry

            self._by_pair = by_pair
            self._by_word = by_word
            self._by_reading = by_reading
            self._entry_count = len(by_pair)
            logger.info(f"Loaded {len(by_pair)} pitch accent entries from {self._path.name}")

            if not by_pair:
                logger.warning(
                    f"Pitch accent file {self._path.name} loaded but contained 0 valid entries. "
                    f"Expected format: reading,kanji,pattern[,nasal,devoice] (CSV or TSV, 3 or 5 columns)."
                )

            return True

        except Exception as e:
            raise SetupError(f"Error loading pitch accent data: {e}") from e

    def is_available(self) -> bool:
        """Check if pitch accent data has been loaded."""
        return self._by_pair is not None

    def lookup_entry(self, word: str, reading: str = "") -> PitchEntry | None:
        """Resolve the full :class:`PitchEntry` for a word, reading-scoped.

        Three-tier resolution (reading-strict, with a pragmatic fallback):
        1. exact ``(word, reading)`` pair,
        2. ``(word, *)`` when the surface has exactly one entry (kana-variant
           mismatch fallback — safe because there's nothing to disambiguate),
        3. reading-only.

        When a surface has multiple readings and none matches exactly, no entry
        is guessed (the homograph fix): returning the wrong reading's pattern is
        worse than returning nothing.

        Args:
            word: Word to look up (kanji or kana form).
            reading: Optional kana reading.

        Returns:
            The matching :class:`PitchEntry`, or None.
        """
        if self._by_pair is None:
            return None

        if reading:
            exact = self._by_pair.get((word, reading))
            if exact is not None:
                return exact

        candidates = self._by_word.get(word)
        if candidates:
            if len(candidates) == 1:
                return candidates[0]
            if not reading:
                # Nothing to disambiguate by — legacy first-wins behavior.
                return candidates[0]
            # Multiple readings + a reading that matched none exactly: don't guess.

        if reading:
            by_reading = self._by_reading.get(reading)
            if by_reading is not None:
                return by_reading

        # The word itself may be a reading (kana lookup, or reading fallback).
        return self._by_reading.get(word)

    def lookup(self, word: str, reading: str = "") -> str | None:
        """Look up the pitch accent pattern string for a word.

        Args:
            word: Word to look up (kanji or kana form).
            reading: Optional kana reading for reading-scoped resolution.

        Returns:
            Pitch accent pattern string, or None if not found.
        """
        entry = self.lookup_entry(word, reading)
        return entry.pattern if entry is not None else None

    def lookup_detailed(
        self,
        word: str,
        reading: str = "",
        pos: str | None = None,
        fmt: str = "jp",
    ) -> tuple[str | None, str | None]:
        """Look up pitch position and derived category for a word.

        Args:
            word: Word to look up (kanji or kana form).
            reading: Kana reading (used for category derivation and fallback lookup).
            pos: Optional MeCab pos1 marker for kifuku/odaka distinction.
            fmt: "jp" (default) for 平板/頭高/... or "romaji" for heiban/atamadaka/...

        Returns:
            Tuple of (position_str, category) or (None, None) if not found.
            Multi-pattern entries (e.g. "0,2") emit comma-joined categories.
        """
        pattern = self.lookup(word, reading)
        if pattern is None:
            return None, None

        # Category derivation needs a KANA reading for an accurate mora count. When
        # the reading is missing we can only fall back to the surface form if it is
        # itself all-kana; a kanji surface (e.g. 学校) would mis-count mora
        # (count_mora("学校")=2, not 4) and mislabel the category. In that case emit
        # the pattern with no category rather than a wrong one.
        if reading:
            lookup_reading = reading
        elif _is_all_kana(word):
            lookup_reading = word
        else:
            return pattern, None
        category = format_categories(pattern, lookup_reading, pos, fmt)
        return pattern, category

    def lookup_batch_detailed(
        self,
        words: Sequence[tuple[str, str] | tuple[str, str, str | None]],
        fmt: str = "jp",
    ) -> list[tuple[str | None, str | None]]:
        """Look up pitch position and category for multiple word entries.

        Accepts either ``(word, reading)`` tuples (legacy) or
        ``(word, reading, pos)`` tuples. Missing pos defaults to None.

        Args:
            words: List of word entries; 2 or 3 element tuples.
            fmt: "jp" or "romaji" — passed through to ``lookup_detailed``.

        Returns:
            List of (position_str, category) tuples (same order as input).
        """
        results: list[tuple[str | None, str | None]] = []
        for entry in words:
            word = entry[0]
            reading = entry[1] if len(entry) > 1 else ""
            pos = entry[2] if len(entry) > 2 else None
            results.append(self.lookup_detailed(word, reading, pos, fmt))
        return results
