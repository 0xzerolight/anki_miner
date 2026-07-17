"""Parse known-word exports from external tools into a set of written forms.

Feeds the Manage Known Words "Import…" flow: users migrating from jpdb,
Migaku, AnkiMorphs (or with a plain word list, e.g. ad-hoc WaniKani exports)
bulk-load their known vocabulary as ``known_words.db`` ``source='user'`` rows.
Pure module — no Qt; runs inside a ``run_off_thread`` work callable.

Format signatures were verified against upstream sources (2026-07); if an
import misbehaves years later, check these for drift before touching the
detection order:

- jpdb review export (JSON shape + grade enum): two independent consumers,
  github.com/llvtt/jpdb_anki_import (jpdb.py) and
  github.com/daryll-ko/jpdb-stats (README schema). jpdb has no known-flag, so
  known-ness is derived from the latest review grade with an EXCLUDE-list —
  an unrecognized/future grade (e.g. never-forget markers) defaults to
  *include*, so enum drift fails safe.
- AnkiMorphs headers: exporter source constants ``"Morph-Lemma"`` /
  ``"Morph-Inflection"`` / ``"Occurrence"`` in
  ankimorphs/known_morphs_exporter.py @ github.com/mortii/anki-morphs.
- Migaku Word Exporter JSON/CSV (``Word,Reading,Language,Status``, status
  ``KNOWN``): README/source of github.com/mh-343/migaku-word-exporter
  (Migaku removed its built-in export).
- Migaku legacy add-on backup ``[[word, int], …]`` (2 = known, 1 = learning):
  raetsel migration walkthrough + Migaku-legacy-guides repo.

Stored form is the written form as exported (食べる), matching the
``mined_form`` keying convention — never re-lemmatized here.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Stable keys the dialog maps to translated display labels; keep the dialog's
# mapping in lockstep (a unit test asserts completeness on both sides).
FORMAT_KEYS = ("jpdb", "migaku_json", "migaku_legacy", "ankimorphs", "migaku_csv", "generic")

# jpdb grades whose *latest* occurrence disqualifies a card. Everything else
# (okay/easy/hard/pass/known + future positive states) counts as known.
_JPDB_EXCLUDED_GRADES = frozenset({"nothing", "something", "fail", "unknown"})

_MIGAKU_CSV_HEADER = ("word", "reading", "language", "status")
_ANKIMORPHS_LEMMA_HEADER = "morph-lemma"

# Known-word exports are tiny (a jpdb/AnkiMorphs dump is well under a MB); the
# "All Files (*)" dialog filter lets a user mis-pick a large file, so cap the
# read rather than buffer+decode an arbitrary blob off-thread.
_MAX_IMPORT_BYTES = 50 * 1024 * 1024


class KnownWordsImportError(Exception):
    """Raised when a file yields no importable known words.

    ``reason`` distinguishes the failure class for user messaging:
    ``"unreadable"`` (missing/undecodable file), ``"unrecognized"`` (format
    not matched — including valid JSON matching no known signature), or
    ``"no_known_words"`` (format matched but zero entries qualified;
    ``format_key`` carries the detected format in that case).
    """

    def __init__(self, reason: str, format_key: str | None = None):
        super().__init__(reason)
        self.reason = reason
        self.format_key = format_key


@dataclass(frozen=True)
class KnownWordsImportResult:
    """Outcome of parsing one export file."""

    format_key: str
    words: frozenset[str]
    total_entries: int


def parse_known_words_file(path: Path) -> KnownWordsImportResult:
    """Detect the export format of ``path`` and extract its known words.

    Content is tried as JSON first; valid JSON that matches no known JSON
    signature raises ``unrecognized`` rather than falling through to the line
    reader (which would ingest syntax fragments as words). ``.json`` files
    are never read as generic word lists for the same reason.

    Bytes are decoded utf-8-sig first (BOM-stripping), then cp932 — the
    Japanese Windows/Excel default for hand-made lists — before giving up.
    """
    try:
        if path.stat().st_size > _MAX_IMPORT_BYTES:
            raise KnownWordsImportError("unreadable")
        raw = path.read_bytes()
    except OSError as exc:
        raise KnownWordsImportError("unreadable") from exc
    text = _decode(raw)

    try:
        data = json.loads(text)
    except ValueError:
        if path.suffix.lower() == ".json":
            raise KnownWordsImportError("unrecognized") from None
        return _parse_delimited_or_text(text)
    return _parse_json(data)


def _result(format_key: str, words: set[str], total: int) -> KnownWordsImportResult:
    if not words:
        raise KnownWordsImportError("no_known_words", format_key=format_key)
    return KnownWordsImportResult(
        format_key=format_key,
        words=frozenset(words),
        total_entries=total,
    )


# ----------------------------------------------------------------------
# JSON formats
# ----------------------------------------------------------------------


def _parse_json(data: Any) -> KnownWordsImportResult:
    if isinstance(data, dict) and isinstance(data.get("cards_vocabulary_jp_en"), list):
        return _parse_jpdb(data["cards_vocabulary_jp_en"])
    if isinstance(data, dict) and isinstance(data.get("words"), list):
        entries = [w for w in data["words"] if isinstance(w, dict) and "word" in w and "status" in w]
        if entries:
            words = {_clean(w["word"]) for w in entries if w["status"] == "KNOWN" and _clean(w["word"])}
            return _result("migaku_json", words, len(entries))
    if (
        isinstance(data, list)
        and data
        and all(
            isinstance(pair, list) and len(pair) == 2 and isinstance(pair[0], str) and isinstance(pair[1], int)
            for pair in data
        )
    ):
        words = {word for word, status in data if status == 2 and _clean(word)}
        return _result("migaku_legacy", words, len(data))
    raise KnownWordsImportError("unrecognized")


def _parse_jpdb(cards: list[Any]) -> KnownWordsImportResult:
    words: set[str] = set()
    total = 0
    for card in cards:
        if not isinstance(card, dict) or not _clean(card.get("spelling")):
            continue
        total += 1
        reviews = [r for r in card.get("reviews") or [] if isinstance(r, dict) and "grade" in r]
        if not reviews:
            continue
        latest = max(reviews, key=lambda r: r.get("timestamp", 0))
        if latest["grade"] not in _JPDB_EXCLUDED_GRADES:
            words.add(_clean(card["spelling"]))
    return _result("jpdb", words, total)


# ----------------------------------------------------------------------
# Delimited / plain-text formats
# ----------------------------------------------------------------------


def _parse_delimited_or_text(text: str) -> KnownWordsImportResult:
    rows = list(csv.reader(text.splitlines()))
    header = tuple(cell.strip().lower() for cell in rows[0]) if rows else ()

    if header[:4] == _MIGAKU_CSV_HEADER:
        entries = [row for row in rows[1:] if row and _clean(row[0])]
        words = {row[0].strip() for row in entries if len(row) >= 4 and row[3].strip().upper() == "KNOWN"}
        return _result("migaku_csv", words, len(entries))

    if header[:1] == (_ANKIMORPHS_LEMMA_HEADER,):
        entries = [row for row in rows[1:] if row and _clean(row[0])]
        # Lemma column only: verbs/adjectives mine as lemma and nouns mine as
        # surface == lemma, so inflected surfaces could never match a card front.
        words = {row[0].strip() for row in entries}
        return _result("ankimorphs", words, len(entries))

    return _parse_generic(text)


def _parse_generic(text: str) -> KnownWordsImportResult:
    """One word per line; first csv/tab cell when the line is delimited."""
    words: set[str] = set()
    total = 0
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "," in stripped or "\t" in stripped:
            delimiter = "," if "," in stripped else "\t"
            stripped = next(csv.reader([stripped], delimiter=delimiter))[0].strip()
            if not stripped:
                continue
        total += 1
        words.add(stripped)
    return _result("generic", words, total)


def _decode(raw: bytes) -> str:
    # utf-8-sig strips a Windows/Excel BOM that would otherwise break json.loads
    # and the exact first-cell header matches; cp932 covers Shift-JIS lists
    # exported from Japanese Notepad/Excel. Both failing ⇒ truly unreadable.
    for encoding in ("utf-8-sig", "cp932"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise KnownWordsImportError("unreadable")


def _clean(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""
