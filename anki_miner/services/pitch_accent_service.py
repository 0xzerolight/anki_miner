"""Service for loading and looking up pitch accent data."""

import csv
import logging
import re
import unicodedata
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from itertools import chain, islice
from pathlib import Path
from typing import Literal

from anki_miner.exceptions import SetupError
from anki_miner.utils.csv_utils import detect_delimiter, is_header_row
from anki_miner.utils.logging_ext import log_summary
from anki_miner.utils.text_utils import katakana_to_hiragana

logger = logging.getLogger(__name__)

# Small kana that combine with the previous kana (not separate mora)
_COMBINING_KANA = set("ゃゅょぁぃぅぇぉゎャュョァィゥェォヮ")

# MeCab pos1 markers that make any downstep 起伏 (kifuku), per the NHK convention.
VERBAL_POS = {"動詞", "形容詞"}

# A pitch position encoded as an H/L mora-level string (e.g. "LHHH"), per the
# Yomitan term-meta-bank v3 schema ("^[HL]+$"). Distinguished from an integer
# downstep position so lookups accept either encoding.
_HL_PATTERN_RE = re.compile(r"^[HL]+$")
_INTEGER_PATTERN_RE = re.compile(r"^[0-9]+$")
_LEADING_POS_ANNOTATION_RE = re.compile(r"^\([^)]*\)\s*")

_ColumnOrder = Literal["reading-term", "term-reading"]
_COLUMN_ORDER_SAMPLE_SIZE = 256
_READING_HEADERS = frozenset({"kana", "reading", "yomi"})
_TERM_HEADERS = frozenset({"expression", "kanji", "term", "word"})

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
    the canonical drop-position ``pattern`` (integer positions like ``"0,2"`` or an
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
        position = int(token)
        return position if position >= 0 else None
    except ValueError:
        return None


def normalize_pitch_pattern(pattern: str) -> str | None:
    """Return canonical comma-joined pitch positions, or None when unusable.

    Kanjium prefixes some positions with parenthesized part-of-speech labels
    (for example ``"(副)1,(形動)0"``). Lapis fields accept positions, not those
    labels. Strip one label per token, retain only nonnegative integer or H/L
    patterns, and deduplicate positions without changing source order.
    """
    positions: list[str] = []
    seen: set[str] = set()
    for raw in pattern.split(","):
        token = _LEADING_POS_ANNOTATION_RE.sub("", raw.strip(), count=1)
        if _INTEGER_PATTERN_RE.fullmatch(token):
            token = str(int(token))
        elif not _HL_PATTERN_RE.fullmatch(token):
            continue
        if token not in seen:
            seen.add(token)
            positions.append(token)
    return ",".join(positions) if positions else None


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
    normalized = normalize_pitch_pattern(pattern)
    if normalized is None:
        return None

    mora = count_mora(reading)
    out: list[str] = []
    seen: set[str] = set()
    for token in normalized.split(","):
        position = _token_to_position(token)
        if position is None:
            continue
        jp = classify_pitch(position, mora, pos)
        category = ROMAJI_CATEGORY[jp] if fmt == "romaji" else jp
        if category not in seen:
            seen.add(category)
            out.append(category)
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


def _parse_pitch_row(row: list[str], column_order: _ColumnOrder = "reading-term") -> _ParsedRow | None:
    """Parse one CSV row into a :class:`_ParsedRow`, or None if unusable.

    Column-count driven after file-level column-order detection: exactly 5
    fields → the enriched format; exactly 3 → the legacy format. The first two
    fields follow ``column_order``. Any other count >= 4 is treated as a
    hand-edited legacy comma file whose pattern held an intra-field comma
    (``0,2`` splits into 4 raw fields) — the pattern tail is rejoined so it
    round-trips, and nasal/devoice stay empty (never misread the tail into
    them). Files written by :func:`atomic_write_csv` always round-trip to
    exactly 3 or 5 columns, confining the ambiguity to hand-edited legacy files.
    """
    n = len(row)
    if n < 3:
        return None
    first = row[0].strip()
    second = row[1].strip()
    if column_order == "reading-term":
        reading, kanji = first, second
    else:
        kanji, reading = first, second
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
    normalized_pattern = normalize_pitch_pattern(pattern)
    if normalized_pattern is None:
        return None
    return _ParsedRow(
        reading=reading,
        kanji=kanji,
        entry=PitchEntry(normalized_pattern, nasal, devoice),
    )


def _header_column_order(row: list[str]) -> _ColumnOrder | None:
    """Resolve first-two-column roles from an explicit header."""
    first = row[0].strip().lstrip("\ufeff").lower()
    second = row[1].strip().lower()
    if first in _READING_HEADERS and second in _TERM_HEADERS:
        return "reading-term"
    if first in _TERM_HEADERS and second in _READING_HEADERS:
        return "term-reading"
    return None


def _infer_column_order(rows: Sequence[list[str]]) -> _ColumnOrder | None:
    """Infer headerless column roles from kana/non-kana evidence."""
    evidence: set[_ColumnOrder] = set()
    for row in rows:
        first = row[0].strip()
        second = row[1].strip()
        if not first or not second:
            continue
        first_is_kana = _is_all_kana(first)
        second_is_kana = _is_all_kana(second)
        if first_is_kana and not second_is_kana:
            evidence.add("reading-term")
        elif second_is_kana and not first_is_kana:
            evidence.add("term-reading")
    return next(iter(evidence)) if len(evidence) == 1 else None


def iter_pitch_csv_rows(path: Path) -> Iterator[_ParsedRow]:
    """Stream parsed rows from a pitch CSV/TSV file.

    Owns the file-format concerns the old single-file service's ``load()``
    owned: delimiter auto-detection, optional-header skip, and per-row parsing
    via :func:`_parse_pitch_row` (5-col enriched, 3-col legacy, anomalous
    tail-rejoin). Consumers dedupe/first-wins themselves (see
    :func:`build_pitch_maps` and the pitch source importer).

    Raises:
        SetupError: If the file is missing or unreadable.
    """
    logger.info("Pitch CSV parse: source=%s", path.name)
    if not path.exists():
        raise SetupError(
            f"Pitch accent file not found at: {path}. Download pitch accent data and place it in ~/.anki_miner/"
        )
    malformed_rows = 0
    malformed_exemplar = "-"
    parsed_rows = 0
    try:
        with open(path, encoding="utf-8") as f:
            sample = f.read(4096)
            f.seek(0)
            delimiter = detect_delimiter(sample, prefer_tab=True)

            reader = csv.reader(f, delimiter=delimiter)

            def rows_with_valid_shape() -> Iterator[list[str]]:
                nonlocal malformed_rows, malformed_exemplar
                for row in reader:
                    if len(row) >= 3:
                        yield row
                        continue
                    malformed_rows += 1
                    if malformed_exemplar == "-":
                        malformed_exemplar = f"cols-{len(row)}"

            valid_rows = rows_with_valid_shape()
            first_row = next(valid_rows, None)
            if first_row is None:
                if malformed_rows:
                    log_summary(
                        logger,
                        "Pitch CSV rows dropped",
                        level=logging.WARNING,
                        count=malformed_rows,
                        exemplar=malformed_exemplar,
                    )
                log_summary(
                    logger,
                    "Pitch CSV parse done",
                    source=path,
                    entries=parsed_rows,
                    malformed=malformed_rows,
                )
                return

            header_order = _header_column_order(first_row)
            if header_order is not None:
                column_order = header_order
                buffered_rows: list[list[str]] = []
            elif is_header_row(first_row):
                raise SetupError(
                    f"Ambiguous pitch column order in {path.name}: header must identify term and reading columns"
                )
            else:
                buffered_rows = [first_row, *islice(valid_rows, _COLUMN_ORDER_SAMPLE_SIZE - 1)]
                inferred_order = _infer_column_order(buffered_rows)
                if inferred_order is None:
                    raise SetupError(
                        f"Ambiguous pitch column order in {path.name}: add a term,reading or reading,term header"
                    )
                column_order = inferred_order

            for row in chain(buffered_rows, valid_rows):
                parsed = _parse_pitch_row(row, column_order)
                if parsed is not None:
                    parsed_rows += 1
                    yield parsed
                else:
                    malformed_rows += 1
                    if malformed_exemplar == "-":
                        malformed_exemplar = f"cols-{len(row)}"
            if malformed_rows:
                log_summary(
                    logger,
                    "Pitch CSV rows dropped",
                    level=logging.WARNING,
                    count=malformed_rows,
                    exemplar=malformed_exemplar,
                )
            log_summary(
                logger,
                "Pitch CSV parse done",
                source=path,
                entries=parsed_rows,
                malformed=malformed_rows,
            )
    except SetupError:
        raise
    except Exception as e:
        logger.warning(
            "Pitch CSV parse failed: stage=parse exc=%s",
            type(e).__name__,
        )
        raise SetupError(f"Error loading pitch accent data: {e}") from e


PitchMaps = tuple[
    dict[tuple[str, str], PitchEntry],
    dict[str, list[PitchEntry]],
    dict[str, PitchEntry],
]


def build_pitch_maps(parsed_rows: Iterable[_ParsedRow]) -> PitchMaps:
    """Build the three lookup maps from parsed rows, first occurrence wins.

    Returns ``(by_pair, by_word, by_unambiguous_reading)``:
    * ``by_pair``: ``(surface, reading) -> PitchEntry`` where surface = kanji,
      or the reading when the term is kana-only,
    * ``by_word``: ``surface -> [PitchEntry, ...]`` (homographs keep every
      reading),
    * ``by_unambiguous_reading``: ``reading -> PitchEntry``, present ONLY for
      readings where every headword agrees on one pattern.

    Surface and reading keys fold katakana to hiragana.

    That third map is deliberately narrow. A blanket reading-only alias is what
    let 腹の中/はらのなか inherit はらのうち's pattern, so it cannot come back --
    but deleting it outright cost 47 of this deck's 275 pitch fields, because a
    kana-spelled card (すごい, ひどい, たくさん) has no kanji key to match its
    headword (凄い, 酷い, 沢山) with. Keeping only the readings whose headwords
    all agree means borrowing cannot produce a wrong pattern: there is exactly
    one pattern to borrow. ``lookup_entry`` additionally restricts the tier to
    kana-only surfaces -- see the reasoning there.
    """
    by_pair: dict[tuple[str, str], PitchEntry] = {}
    by_word: dict[str, list[PitchEntry]] = {}
    reading_patterns: dict[str, set[str]] = {}
    reading_first: dict[str, PitchEntry] = {}
    for parsed in parsed_rows:
        reading, kanji, entry = parsed.reading, parsed.kanji, parsed.entry
        normalized_pattern = normalize_pitch_pattern(entry.pattern)
        if normalized_pattern is None:
            continue
        entry = PitchEntry(normalized_pattern, entry.nasal, entry.devoice)
        reading = katakana_to_hiragana(unicodedata.normalize("NFC", reading))
        surface = katakana_to_hiragana(unicodedata.normalize("NFC", kanji or reading))
        if not surface:
            continue
        key = (surface, reading)
        if key in by_pair:
            continue  # first occurrence wins
        by_pair[key] = entry
        by_word.setdefault(surface, []).append(entry)
        if reading:
            reading_patterns.setdefault(reading, set()).add(normalized_pattern)
            reading_first.setdefault(reading, entry)
    by_unambiguous_reading = {r: reading_first[r] for r, pats in reading_patterns.items() if len(pats) == 1}
    return by_pair, by_word, by_unambiguous_reading


class PitchMapsStore:
    """Shared maps-holder base for pitch lookup stores.

    Owns the two in-memory maps (see :func:`build_pitch_maps`), the concrete
    term-scoped ``lookup_entry`` resolution, and the derived
    lookup API (``lookup`` / ``lookup_detailed`` / ``lookup_batch_detailed``).
    Subclasses differ only in where ``load()`` reads rows from (the indexed
    per-source provider reads SQLite; tests may feed rows directly via
    :meth:`_set_maps`).

    Lookups are reading-scoped: entries are keyed on ``(surface, reading)`` so a
    homograph (弾く ひく[0] vs はじく[2]) resolves by the reading passed in rather
    than whichever row loaded first.
    """

    def __init__(self) -> None:
        # (surface, reading) -> PitchEntry ; surface = kanji, or reading when
        # the term is kana-only.
        self._by_pair: dict[tuple[str, str], PitchEntry] | None = None
        # surface -> all entries for that surface (homographs keep every reading)
        self._by_word: dict[str, list[PitchEntry]] = {}
        # reading -> entry, ONLY for readings whose headwords all agree on one
        # pattern (see build_pitch_maps); consumed by the kana-surface tier.
        self._by_unambiguous_reading: dict[str, PitchEntry] = {}
        self._entry_count: int = 0

    def _set_maps(self, maps: PitchMaps) -> None:
        """Install built maps (see :func:`build_pitch_maps`); marks the store loaded."""
        self._by_pair, self._by_word, self._by_unambiguous_reading = maps
        self._entry_count = len(self._by_pair)

    @property
    def entry_count(self) -> int:
        """Number of distinct (surface, reading) pitch entries loaded."""
        return self._entry_count

    def is_available(self) -> bool:
        """Check if pitch accent data has been loaded."""
        return self._by_pair is not None

    def lookup_entry(self, word: str, reading: str = "") -> PitchEntry | None:
        """Resolve the full :class:`PitchEntry` for a word, reading-scoped.

        Three-tier resolution:
        1. exact ``(word, reading)`` pair,
        2. ``(word, *)`` when the surface has exactly one entry (kana-variant
           mismatch fallback — safe because there's nothing to disambiguate),
        3. reading-only, but ONLY when the surface is kana-only AND every
           headword sharing that reading agrees on one pattern.

        When a surface has multiple readings and none matches exactly, no entry
        is guessed (the homograph fix): returning the wrong reading's pattern is
        worse than returning nothing.

        Tier 3 is narrow on purpose, and both halves of the condition carry
        weight. The pattern-agreement half means there is nothing to guess
        between. The kana-only half is what separates すごい from 解呪: a kana
        card is a spelling of whatever word is read that way, so borrowing 凄い's
        pattern is the same word's pattern; a KANJI surface that missed tiers 1
        and 2 is asserting an orthography the source does not have for that
        reading, and 解呪 borrowing 槐樹/かいじゅ would be a different word. Kanji
        surfaces therefore stay unresolved, which matches Yomitan keeping
        pronunciation attached to its term headword.

        Args:
            word: Word to look up (kanji or kana form).
            reading: Optional kana reading.

        Returns:
            The matching :class:`PitchEntry`, or None.
        """
        if self._by_pair is None:
            return None

        word = katakana_to_hiragana(unicodedata.normalize("NFC", word))
        reading = katakana_to_hiragana(unicodedata.normalize("NFC", reading))
        exact = self._by_pair.get((word, reading))
        if exact is not None:
            return exact

        candidates = self._by_word.get(word)
        if candidates and len(candidates) == 1:
            return candidates[0]

        if _is_all_kana(word):
            return self._by_unambiguous_reading.get(reading or word)
        # Kanji surface, no pair and no unique-surface match: refuse. 解呪/かいじゅ
        # must not inherit 槐樹/かいじゅ merely because the readings fold alike.
        return None

    def lookup(self, word: str, reading: str = "") -> str | None:
        """Look up the pitch accent pattern string for a word.

        Args:
            word: Word to look up (kanji or kana form).
            reading: Optional kana reading for reading-scoped resolution.

        Returns:
            Pitch accent pattern string, or None if not found.
        """
        entry = self.lookup_entry(word, reading)
        return normalize_pitch_pattern(entry.pattern) if entry is not None else None

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
