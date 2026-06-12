"""Audio pack format detection and per-format parsers.

Three physical formats for local-audio-yomichan packs:
  ajt       — index.json + media/ directory
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
    """Detect only the index-file-driven formats (ajt/nhk16).

    Unlike the forvo/jpod_legacy heuristics, these formats are identified by a
    specific index file and cannot be triggered by audio files belonging to
    nested child packs — safe to apply to a parent directory whose children
    are themselves packs.
    """
    # ajt: index.json + media/ directory
    if (pack_dir / "index.json").is_file() and (pack_dir / "media").is_dir():
        return "ajt"

    # nhk16: entries.json + audio/ directory
    if (pack_dir / "entries.json").is_file() and (pack_dir / "audio").is_dir():
        return "nhk16"

    return None


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


def parse_nhk16(pack_dir: Path, source: str) -> Iterator[AudioPackRow]:
    """Parse an NHK16-format audio pack.

    Reads ``entries.json`` (JSON array). Yields one row per (headword,
    accent soundFile) pair.

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

    for entry in data:
        if not isinstance(entry, dict):
            continue
        kana: str = entry.get("kana", "") or ""
        kanji_raw = entry.get("kanji", [])
        if not isinstance(kanji_raw, list):
            kanji_raw = []

        # Sub-split each headword on fullwidth comma ，
        expressions: list[str] = []
        for k in kanji_raw:
            if not isinstance(k, str):
                continue
            parts = k.split("，")
            expressions.extend(p.strip() for p in parts if p.strip())

        # If no kanji headwords, fall back to kana as expression
        if not expressions:
            if kana:
                expressions = [kana]
            else:
                continue

        accents = entry.get("accents", [])
        if not isinstance(accents, list):
            accents = []

        for accent in accents:
            if not isinstance(accent, dict):
                continue
            sound_file = accent.get("soundFile")
            if not sound_file:
                continue
            audio_path = audio_dir / sound_file
            if not audio_path.is_file():
                logger.debug("nhk16: skipping missing audio file %s", audio_path)
                continue

            for expr in expressions:
                yield AudioPackRow(
                    expression=expr,
                    reading=kana if kana else None,
                    source=source,
                    speaker=None,
                    display=None,
                    file=f"audio/{sound_file}",
                )

        # Subentries
        subentries = entry.get("subentries", [])
        if not isinstance(subentries, list):
            continue
        for sub in subentries:
            if not isinstance(sub, dict):
                continue
            if "head" not in sub:
                # Numeric counter entry — skip
                continue
            head: str = sub.get("head", "") or ""
            if not head:
                continue

            sub_accents = sub.get("accents", [])
            if not isinstance(sub_accents, list):
                continue

            # reading rule: kana heads get reading=head, kanji heads get reading=None
            if _is_kana(head):
                sub_reading: str | None = head
            else:
                sub_reading = None

            for accent in sub_accents:
                if not isinstance(accent, dict):
                    continue
                sound_file = accent.get("soundFile")
                if not sound_file:
                    continue
                audio_path = audio_dir / sound_file
                if not audio_path.is_file():
                    logger.debug("nhk16: skipping missing subentry audio %s", audio_path)
                    continue

                yield AudioPackRow(
                    expression=head,
                    reading=sub_reading,
                    source=source,
                    speaker=None,
                    display=None,
                    file=f"audio/{sound_file}",
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
# Parser dispatch table
# ---------------------------------------------------------------------------

ParserFn = Callable[[Path, str], Iterator[AudioPackRow]]

PARSERS: dict[str, ParserFn] = {
    "ajt": parse_ajt,
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
