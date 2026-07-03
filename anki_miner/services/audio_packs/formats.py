"""Audio pack format detection and per-format parsers.

Five physical formats for local-audio-yomichan packs:
  ajt       — index.json (``headwords`` map) + media/ directory
  ozk5      — index.json (``entries`` array + ``kana_index``/``kanji_index``)
  nhk16     — entries.json + audio/ directory
  forvo     — speaker subdirectories containing audio files
  jpod_legacy — flat/nested audio files with "{reading} - {expression}" stems

Parsers yield :class:`~anki_miner.services.audio_packs.storage.AudioPackRow`
entries; ``file`` fields use posix-style separators relative to ``pack_dir``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Callable, Iterator

from anki_miner.services.audio_packs.storage import AudioPackRow

logger = logging.getLogger(__name__)

AUDIO_EXTENSIONS: set[str] = {".mp3", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".flac", ".wav"}

# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------


def detect_pack_format(pack_dir: Path) -> str | None:
    """Return the format string for *pack_dir*, or None if unrecognised.

    Detection order: ajt → nhk16 → forvo → jpod_legacy.
    """
    if not pack_dir.is_dir():
        return None

    fmt = _detect_index_driven_format(pack_dir)
    if fmt is not None:
        return fmt

    # forvo: immediate subdirectories that contain audio-extension files (no index files)
    if _looks_like_forvo(pack_dir):
        return "forvo"

    # jpod_legacy: audio files (possibly nested) with "{reading} - {expression}" stems
    if _looks_like_jpod_legacy(pack_dir):
        return "jpod_legacy"

    return None


def _detect_index_driven_format(pack_dir: Path) -> str | None:
    """Detect only the index-file-driven formats (ozk5/ajt/nhk16).

    Unlike the forvo/jpod_legacy heuristics, these formats are identified by a
    specific index file and cannot be triggered by audio files belonging to
    nested child packs — safe to apply to a parent directory whose children
    are themselves packs.

    ozk5 and ajt both use ``index.json``; they are told apart by content —
    ozk5 has an ``entries`` array plus a ``kana_index``/``kanji_index`` map,
    ajt has a ``headwords`` map — so ozk5 is checked first.
    """
    index_path = pack_dir / "index.json"
    if index_path.is_file():
        if _looks_like_ozk5(_peek_json(index_path)):
            return "ozk5"
        # ajt: index.json + media/ directory
        if (pack_dir / "media").is_dir():
            return "ajt"

    # nhk16: entries.json + audio/ directory
    if (pack_dir / "entries.json").is_file() and (pack_dir / "audio").is_dir():
        return "nhk16"

    return None


def _peek_json(path: Path) -> object | None:
    """Best-effort parse of *path* for format detection; None if unreadable.

    Detection must never raise on a malformed index — parsing is where the
    ValueError surfaces — so read errors and bad JSON fall through to None.
    ``ValueError`` covers both ``json.JSONDecodeError`` and the
    ``UnicodeDecodeError`` that ``read_text`` raises on non-UTF-8 bytes; letting
    the latter escape would slip past the import flow's ``except OSError`` guard.
    """
    try:
        data: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data


def _looks_like_ozk5(data: object | None) -> bool:
    """True if parsed index.json carries the ozk5 signature.

    Signature: a JSON object with an ``entries`` array and at least one of the
    ``kana_index`` / ``kanji_index`` maps. This is disjoint from ajt, whose
    index.json is keyed by ``headwords`` and has no ``entries`` array.
    """
    if not isinstance(data, dict):
        return False
    if not isinstance(data.get("entries"), list):
        return False
    return isinstance(data.get("kana_index"), dict) or isinstance(data.get("kanji_index"), dict)


def _looks_like_forvo(pack_dir: Path) -> bool:
    """True if pack_dir has immediate subdirs that contain audio-ext files.

    Assumption: speaker-dir files use plain expression stems (e.g. "食べる.mp3"),
    not "{reading} - {expression}" stems.  A jpod_legacy pack whose audio files
    happen to live one level deep inside a subdirectory would be mis-detected as
    forvo here; real JPod101 legacy packs are flat (files directly in pack_dir).
    """
    for entry in pack_dir.iterdir():
        if entry.is_dir() and not entry.name.startswith("."):
            for child in entry.iterdir():
                if child.is_file() and child.suffix.lower() in AUDIO_EXTENSIONS:
                    return True
    return False


def _looks_like_jpod_legacy(pack_dir: Path) -> bool:
    """True if any audio file (recursive) has a stem with exactly one ' - ' separator.

    Uses the same full split (no maxsplit) and len-2 check as the parser so that
    detection and parsing agree: stems like "a - b - c" (3 parts) are ignored by
    both the detector and the parser.
    """
    for audio_file in _iter_audio_files(pack_dir):
        parts = audio_file.stem.split(" - ")
        if len(parts) == 2:
            return True
    return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _iter_audio_files(directory: Path) -> Iterator[Path]:
    """Yield all audio-extension files recursively under *directory*."""
    for path in directory.rglob("*"):
        if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS:
            yield path


def _is_kana(text: str) -> bool:
    """True if *text* is non-empty and consists solely of hiragana or katakana."""
    if not text:
        return False
    return all(("぀" <= ch <= "ゟ") or ("゠" <= ch <= "ヿ") or ("ｦ" <= ch <= "ﾟ") for ch in text)


def _rel_posix(pack_dir: Path, absolute: Path) -> str:
    """Return posix-style relative path from pack_dir to absolute."""
    return absolute.relative_to(pack_dir).as_posix()


# ---------------------------------------------------------------------------
# AJT parser
# ---------------------------------------------------------------------------


def parse_ajt(pack_dir: Path, source: str) -> Iterator[AudioPackRow]:
    """Parse an AJT-format audio pack.

    Reads ``index.json``; yields one :class:`AudioPackRow` per (headword, file)
    pair where ``media/<file>`` exists on disk.

    Raises :exc:`ValueError` on malformed top-level JSON structure.
    """
    index_path = pack_dir / "index.json"
    try:
        data = json.loads(index_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Malformed index.json in {pack_dir}: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError(f"index.json in {pack_dir} must be a JSON object")

    headwords: dict = data.get("headwords", {})
    files_meta: dict = data.get("files", {})

    if not isinstance(headwords, dict):
        raise ValueError(f"index.json 'headwords' must be an object in {pack_dir}")

    media_dir = pack_dir / "media"

    for expression, file_list in headwords.items():
        if not isinstance(file_list, list):
            logger.debug("ajt: skipping headword %r — file list is not an array", expression)
            continue
        for fname in file_list:
            if not isinstance(fname, str):
                continue
            media_path = media_dir / fname
            if not media_path.is_file():
                logger.debug("ajt: skipping missing media file %s", media_path)
                continue

            file_entry: dict = files_meta.get(fname, {}) if isinstance(files_meta, dict) else {}
            reading: str | None = file_entry.get("kana_reading") if isinstance(file_entry, dict) else None
            if not reading:
                reading = None

            # display: pitch_number if meaningful, else pitch_pattern, else None
            display: str | None = None
            if isinstance(file_entry, dict):
                pitch_number = file_entry.get("pitch_number")
                pitch_pattern = file_entry.get("pitch_pattern")
                if pitch_number is not None and str(pitch_number).isdigit():
                    display = str(pitch_number)
                elif pitch_pattern:
                    display = str(pitch_pattern)

            yield AudioPackRow(
                expression=expression,
                reading=reading,
                source=source,
                speaker=None,
                display=display,
                file=f"media/{fname}",
            )


# ---------------------------------------------------------------------------
# NHK16 parser
# ---------------------------------------------------------------------------

# Kanji-numeral map for expanding NHK16 counter subentries.
# Ported from local-audio-yomichan plugin/source/nhk16.py (`num_map`),
# upstream commit 2cbabbc.
_NHK16_NUM_MAP: dict[int, str] = {
    0: "零",
    1: "一",
    2: "二",
    3: "三",
    4: "四",
    5: "五",
    6: "六",
    7: "七",
    8: "八",
    9: "九",
    10: "十",
    11: "十一",
    12: "十二",
    13: "十三",
    14: "十四",
    15: "十五",
    16: "十六",
    17: "十七",
    18: "十八",
    19: "十九",
    20: "二十",
    21: "二十一",
    22: "二十二",
    23: "二十三",
    24: "二十四",
    25: "二十五",
    26: "二十六",
    27: "二十七",
    28: "二十八",
    29: "二十九",
    30: "三十",
    31: "三十一",
    32: "三十二",
    33: "三十三",
    34: "三十四",
    35: "三十五",
    36: "三十六",
    37: "三十七",
    38: "三十八",
    39: "三十九",
    40: "四十",
    41: "四十一",
    42: "四十二",
    43: "四十三",
    44: "四十四",
    45: "四十五",
    46: "四十六",
    47: "四十七",
    48: "四十八",
    49: "四十九",
    50: "五十",
    51: "五十一",
    52: "五十二",
    53: "五十三",
    54: "五十四",
    55: "五十五",
    56: "五十六",
    57: "五十七",
    58: "五十八",
    59: "五十九",
    60: "六十",
    61: "六十一",
    62: "六十二",
    63: "六十三",
    64: "六十四",
    65: "六十五",
    66: "六十六",
    67: "六十七",
    68: "六十八",
    69: "六十九",
    70: "七十",
    71: "七十一",
    72: "七十二",
    73: "七十三",
    74: "七十四",
    75: "七十五",
    76: "七十六",
    77: "七十七",
    78: "七十八",
    79: "七十九",
    80: "八十",
    81: "八十一",
    82: "八十二",
    83: "八十三",
    84: "八十四",
    85: "八十五",
    86: "八十六",
    87: "八十七",
    88: "八十八",
    89: "八十九",
    90: "九十",
    91: "九十一",
    92: "九十二",
    93: "九十三",
    94: "九十四",
    95: "九十五",
    96: "九十六",
    97: "九十七",
    98: "九十八",
    99: "九十九",
    100: "百",
    1000: "千",
    10000: "一万",
}

_NUM2FULLWIDTH = str.maketrans("0123456789", "０１２３４５６７８９")


def _split_headwords(headword_list: object, delimiter: str) -> list[str]:
    """Split each string in *headword_list* on *delimiter*, stripped, no empties.

    Ported from local-audio-yomichan plugin/source/nhk16.py
    (`NHK16AudioSource.parse_headwords`), upstream commit 2cbabbc. Non-list
    inputs and non-string members are tolerated (yield nothing) so a malformed
    ``kanji``/``kanjiNotUsed`` field never aborts the parse.
    """
    if not isinstance(headword_list, list):
        return []
    out: list[str] = []
    for headword in headword_list:
        if not isinstance(headword, str):
            continue
        for part in headword.split(delimiter):
            stripped = part.strip()
            if stripped:
                out.append(stripped)
    return out


def _nhk16_numbers(number: object) -> list[str]:
    """Expand an NHK16 counter ``number`` to its written headword forms.

    Ported from local-audio-yomichan plugin/source/nhk16.py
    (`NHK16AudioSource.get_numbers`), upstream commit 2cbabbc. Returns the
    fullwidth-digit form plus the kanji-numeral form (kanji-only above 100);
    the 何［ナン］ sentinel maps to 何. Unparseable / out-of-table numbers
    degrade to whatever forms are available rather than raising (the upstream
    KeyErrors on numbers >100 outside {1000, 10000}).
    """
    if number == "何［ナン］":
        return ["何"]
    if not isinstance(number, (str, int)):
        return []
    try:
        n = int(number)
    except ValueError:
        return []
    fullwidth = str(number).translate(_NUM2FULLWIDTH)
    kanji_num = _NHK16_NUM_MAP.get(n)
    if n > 100:
        return [kanji_num] if kanji_num is not None else [fullwidth]
    forms = [fullwidth]
    if kanji_num is not None:
        forms.append(kanji_num)
    return forms


def parse_nhk16(pack_dir: Path, source: str) -> Iterator[AudioPackRow]:
    """Parse an NHK16-format audio pack.

    Reads ``entries.json`` (JSON array). Yields one row per (headword,
    accent soundFile) pair.

    The ``kanjiNotUsed`` filter (drop spellings NHK marks as unused) and the
    numeric-headword expansion (counter subentries → fullwidth + kanji numeral
    forms) are ported from local-audio-yomichan plugin/source/nhk16.py
    (`NHK16AudioSource.add_entries`), upstream commit 2cbabbc.

    Raises :exc:`ValueError` on malformed top-level JSON structure.
    """
    entries_path = pack_dir / "entries.json"
    try:
        data = json.loads(entries_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Malformed entries.json in {pack_dir}: {exc}") from exc

    if not isinstance(data, list):
        raise ValueError(f"entries.json in {pack_dir} must be a JSON array")

    audio_dir = pack_dir / "audio"

    def _sound_rel(accent: object) -> str | None:
        """Return posix ``audio/<file>`` for a present soundFile, else None."""
        if not isinstance(accent, dict):
            return None
        sound_file = accent.get("soundFile")
        if not sound_file:
            return None
        if not (audio_dir / sound_file).is_file():
            logger.debug("nhk16: skipping missing audio file %s", audio_dir / sound_file)
            return None
        return f"audio/{sound_file}"

    for entry in data:
        if not isinstance(entry, dict):
            continue
        kana: str = entry.get("kana", "") or ""
        kanji_raw = entry.get("kanji", [])

        # Sub-split each headword on fullwidth comma ，
        expressions = _split_headwords(kanji_raw, "，")

        # Drop spellings NHK explicitly marks unused (substring match, mirroring
        # upstream): an expression containing any kanjiNotUsed token is removed.
        kanji_not_used = _split_headwords(entry.get("kanjiNotUsed", []), "，")
        if kanji_not_used:
            expressions = [e for e in expressions if not any(nu in e for nu in kanji_not_used)]

        accents = entry.get("accents", [])
        if not isinstance(accents, list):
            accents = []

        for accent in accents:
            rel = _sound_rel(accent)
            if rel is None:
                continue
            if expressions:
                for expr in expressions:
                    yield AudioPackRow(
                        expression=expr,
                        reading=kana if kana else None,
                        source=source,
                        speaker=None,
                        display=None,
                        file=rel,
                    )
            elif kana:
                # No (retained) kanji headwords → fall back to the kana form.
                yield AudioPackRow(
                    expression=kana,
                    reading=kana,
                    source=source,
                    speaker=None,
                    display=None,
                    file=rel,
                )

        # Subentries
        subentries = entry.get("subentries", [])
        if not isinstance(subentries, list):
            continue
        for sub in subentries:
            if not isinstance(sub, dict):
                continue
            sub_accents = sub.get("accents", [])
            if not isinstance(sub_accents, list):
                continue

            if "head" in sub:
                head: str = sub.get("head", "") or ""
                if not head:
                    continue
                # kana heads get reading=head, kanji heads get reading=None
                sub_reading: str | None = head if _is_kana(head) else None
                for accent in sub_accents:
                    rel = _sound_rel(accent)
                    if rel is None:
                        continue
                    yield AudioPackRow(
                        expression=head,
                        reading=sub_reading,
                        source=source,
                        speaker=None,
                        display=None,
                        file=rel,
                    )
            else:
                # Number (+counter) section: expand the number to its written
                # forms and prepend it to each ・-split counter kanji (or, with
                # no kanji, to the reading — blanked when it is the 整数 marker).
                counter_exprs = _split_headwords(kanji_raw, "・")
                numbers = _nhk16_numbers(sub.get("number"))
                counter_reading = "" if kana == "整数" else kana
                for accent in sub_accents:
                    rel = _sound_rel(accent)
                    if rel is None:
                        continue
                    if counter_exprs:
                        for expr in counter_exprs:
                            for number in numbers:
                                yield AudioPackRow(
                                    expression=f"{number}{expr}",
                                    reading=None,
                                    source=source,
                                    speaker=None,
                                    display=None,
                                    file=rel,
                                )
                    else:
                        for number in numbers:
                            yield AudioPackRow(
                                expression=f"{number}{counter_reading}",
                                reading=None,
                                source=source,
                                speaker=None,
                                display=None,
                                file=rel,
                            )


# ---------------------------------------------------------------------------
# Forvo parser
# ---------------------------------------------------------------------------


def parse_forvo(pack_dir: Path, source: str) -> Iterator[AudioPackRow]:
    """Parse a Forvo-format audio pack.

    Speaker = immediate parent directory name of each audio file.
    Expression = file stem. Recursive scan.
    """
    for audio_file in _iter_audio_files(pack_dir):
        speaker = audio_file.parent.name
        expression = audio_file.stem
        rel = _rel_posix(pack_dir, audio_file)
        yield AudioPackRow(
            expression=expression,
            reading=None,
            source=source,
            speaker=speaker,
            display=speaker,
            file=rel,
        )


# ---------------------------------------------------------------------------
# JPod legacy parser
# ---------------------------------------------------------------------------


def parse_jpod_legacy(pack_dir: Path, source: str) -> Iterator[AudioPackRow]:
    """Parse a JPod-legacy-format audio pack.

    File stem must be ``"{reading} - {expression}"``.  Stems that don't match
    are skipped.  If reading == expression: kana → (expression=reading,
    reading=reading); otherwise → (expression=reading, reading=None).
    """
    for audio_file in _iter_audio_files(pack_dir):
        stem = audio_file.stem
        parts = stem.split(" - ")
        if len(parts) != 2:
            logger.debug("jpod_legacy: skipping %s — stem has %d parts", audio_file.name, len(parts))
            continue
        reading_part, expression_part = parts[0], parts[1]
        if not reading_part or not expression_part:
            continue

        if reading_part == expression_part:
            if _is_kana(reading_part):
                expression = reading_part
                reading: str | None = reading_part
            else:
                expression = reading_part
                reading = None
        else:
            expression = expression_part
            reading = reading_part

        rel = _rel_posix(pack_dir, audio_file)
        yield AudioPackRow(
            expression=expression,
            reading=reading,
            source=source,
            speaker=None,
            display=None,
            file=rel,
        )


# ---------------------------------------------------------------------------
# OZK5 parser
# ---------------------------------------------------------------------------


def parse_ozk5(pack_dir: Path, source: str) -> Iterator[AudioPackRow]:
    """Parse an OZK5-format audio pack.

    Ported from local-audio-yomichan plugin/source/ozk5.py
    (`OZK5AudioSource.add_entries`), upstream commit 2cbabbc.

    Reads ``index.json`` — a JSON object with a ``meta`` block (``media_dir``
    defaults to ``"media"``) and an ``entries`` array of
    ``{kanji, kana, audio_file}``. Yields one row per entry keyed to its
    ``kanji`` (or ``kana`` when there is no kanji); a kanji entry additionally
    yields a kana-keyed row so the audio is findable by reading, matching
    upstream. Entries whose audio file is absent are skipped.

    Raises :exc:`ValueError` on malformed top-level JSON structure. ``display``
    is left unset: it is cosmetic (never read back by the fetcher), so the
    upstream katakana-mora rendering is intentionally not reproduced.
    """
    index_path = pack_dir / "index.json"
    try:
        data = json.loads(index_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Malformed index.json in {pack_dir}: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError(f"index.json in {pack_dir} must be a JSON object")

    entries = data.get("entries", [])
    if not isinstance(entries, list):
        raise ValueError(f"index.json 'entries' must be an array in {pack_dir}")

    meta = data.get("meta")
    media_dir = meta.get("media_dir", "media") if isinstance(meta, dict) else "media"
    if not isinstance(media_dir, str) or not media_dir:
        media_dir = "media"

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        kanji = entry.get("kanji") or ""
        kana = entry.get("kana") or ""
        audio_file = entry.get("audio_file") or ""
        if not audio_file:
            continue

        expression = kanji or kana  # use kana if no kanji
        if not expression:
            continue

        full_path = pack_dir / media_dir / audio_file
        if not full_path.is_file():
            logger.debug("ozk5: skipping missing audio file %s", full_path)
            continue
        rel = (Path(media_dir) / audio_file).as_posix()

        yield AudioPackRow(
            expression=expression,
            reading=kana if kana else None,
            source=source,
            speaker=None,
            display=None,
            file=rel,
        )

        # A kanji entry also yields a kana-keyed row (unless kanji == kana,
        # which would duplicate) so lookups by reading find the same audio.
        if kanji and kanji != kana and kana:
            yield AudioPackRow(
                expression=kana,
                reading=kana,
                source=source,
                speaker=None,
                display=None,
                file=rel,
            )


# ---------------------------------------------------------------------------
# Parser dispatch table
# ---------------------------------------------------------------------------

ParserFn = Callable[[Path, str], Iterator[AudioPackRow]]

PARSERS: dict[str, ParserFn] = {
    "ajt": parse_ajt,
    "ozk5": parse_ozk5,
    "nhk16": parse_nhk16,
    "forvo": parse_forvo,
    "jpod_legacy": parse_jpod_legacy,
}


# ---------------------------------------------------------------------------
# Pack scanner
# ---------------------------------------------------------------------------


def scan_importable_packs(directory: Path) -> list[tuple[Path, str]]:
    """Return (pack_dir, format) for every detectable pack under *directory*.

    Checks each immediate non-hidden child directory first, then *directory*
    itself.  Skips hidden directories (names starting with ``'.'``).  Packs
    nested more than one level deep are not detected.

    When one or more children were detected as packs, the directory itself is
    only checked against the index-driven formats (ajt/nhk16): the heuristic
    formats (forvo/jpod_legacy) match on audio files anywhere below the
    directory, so a canonical ``user_files/`` parent holding jpod/forvo/nhk16
    children would otherwise also be misreported as a junk parent pack.
    A directory that is itself a pack with no pack children gets the full
    detection as before.
    """
    seen: set[Path] = set()
    child_results: list[tuple[Path, str]] = []

    if directory.is_dir():
        for child in sorted(directory.iterdir()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            resolved = child.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            fmt = detect_pack_format(child)
            if fmt is not None:
                child_results.append((child, fmt))

    results: list[tuple[Path, str]] = []
    if directory.resolve() not in seen:
        if child_results:
            dir_fmt = _detect_index_driven_format(directory) if directory.is_dir() else None
        else:
            dir_fmt = detect_pack_format(directory)
        if dir_fmt is not None:
            results.append((directory, dir_fmt))

    results.extend(child_results)
    return results
