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
- Kindle Vocabulary Builder ``vocab.db``: ``WORDS(word, stem, lang, category)``,
  category 100 = mastered; schema per WordDumb's docs/contributing/
  vocabulary_builder.rst (github.com/xxyzz/WordDumb).
- Lute term export: header ``term,parent,translation,language,tags,added,
  status,link_status,pronunciation``, status 1-5 / 98 ignored / 99 well known,
  ``W``/``I`` accepted on import (lute/term/routes.py, lute/termimport/
  service.py @ github.com/LuteOrg/lute-v3). LWT's term columns are WoText /
  WoStatus / LgName.

Stored form is the written form as exported (食べる), matching the
``mined_form`` keying convention — never re-lemmatized here.
"""

from __future__ import annotations

import csv
import json
import logging
import sqlite3
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from anki_miner.exceptions import SetupError
from anki_miner.services.reading._util import decode_with_ladder
from anki_miner.utils.logging_ext import log_summary

logger = logging.getLogger(__name__)

# Stable keys the dialog maps to translated display labels; keep the dialog's
# mapping in lockstep (a unit test asserts completeness on both sides).
FORMAT_KEYS = (
    "jpdb",
    "migaku_json",
    "migaku_legacy",
    "ankimorphs",
    "migaku_csv",
    "kindle_vocab",
    "lute_csv",
    "generic",
)

# jpdb grades whose *latest* occurrence disqualifies a card. Everything else
# (okay/easy/hard/pass/known + future positive states) counts as known.
_JPDB_EXCLUDED_GRADES = frozenset({"nothing", "something", "fail", "unknown"})

_MIGAKU_CSV_HEADER = ("word", "reading", "language", "status")
_ANKIMORPHS_LEMMA_HEADER = "morph-lemma"

# SK-09 (owner ruling R24). A Kindle vocab.db is SQLite: read as a database,
# never decoded as text.
_SQLITE_MAGIC = b"SQLite format 3\x00"
_KINDLE_MASTERED = 100

# Lute / LWT term tables (R24): status 5 (learned) and 99 / W (well known)
# import; 1-4 (learning) and 98 / I (ignored) do not.
_TERM_HEADERS = ("term", "wotext")
_STATUS_HEADERS = ("status", "wostatus")
_LANGUAGE_HEADERS = ("language", "lgname")
_TERM_KNOWN_STATUSES = frozenset({"5", "99", "w"})

# Known-word exports are tiny (a jpdb/AnkiMorphs dump is well under a MB); the
# "All Files (*)" dialog filter lets a user mis-pick a large file, so cap the
# read rather than buffer+decode an arbitrary blob off-thread.
_MAX_IMPORT_BYTES = 50 * 1024 * 1024

#: Ladder for a caller that hands over no profile (tests, ad-hoc callers): the
#: pre-profile Japanese pair. utf-8-sig strips a Windows/Excel BOM that would
#: otherwise break json.loads and the exact first-cell header matches, and
#: cp932 is what a Japanese Notepad/Excel export is written in.
_DEFAULT_ENCODINGS = ("utf-8-sig", "cp932")


class KnownWordsImportError(Exception):
    """Raised when a file yields no importable known words.

    ``reason`` distinguishes the failure class for user messaging, and the
    three read-side reasons are deliberately distinct because the dialog
    states what actually happened: ``"too_large"`` (over the size cap — the
    file is stat-ed, never opened), ``"unreadable"`` (an ``OSError`` opening
    or reading it), ``"undecodable"`` (bytes read fine, every encoding in the
    ladder failed). Then ``"unrecognized"`` (format not matched — including
    valid JSON matching no known signature), ``"no_known_words"`` (format
    matched but zero entries qualified; ``format_key`` carries the detected
    format in that case) and ``"other_language"`` (a Lute/LWT file whose
    language column never names the mining language; ``languages`` lists the
    names it does hold, so the dialog can say why nothing came in).
    """

    def __init__(self, reason: str, format_key: str | None = None, *, languages: tuple[str, ...] = ()):
        super().__init__(reason)
        self.reason = reason
        self.format_key = format_key
        self.languages = languages


@dataclass(frozen=True)
class KnownWordsImportResult:
    """Outcome of parsing one export file."""

    format_key: str
    words: frozenset[str]
    total_entries: int
    skipped_malformed: int = 0


def parse_known_words_file(
    path: Path,
    *,
    encodings: tuple[str, ...] | None = None,
    script_check: Callable[[str], bool] | None = None,
    language: str | None = None,
) -> KnownWordsImportResult:
    """Detect the export format of ``path`` and extract its known words.

    A SQLite file is a Kindle Vocabulary Builder database or nothing. Other
    content is tried as JSON first; valid JSON that matches no known JSON
    signature raises ``unrecognized`` rather than falling through to the line
    reader (which would ingest syntax fragments as words). ``.json`` files
    are never read as generic word lists for the same reason.

    *encodings* is the caller's decode ladder — the mining language's
    ``get_profile(...).import_encodings``. ``None`` keeps the historical
    two-leg Japanese default; ``()`` would be an EMPTY ladder, so never pass
    it as the "use the default" sentinel. *script_check* validates a
    single-byte leg (``utils.subtitle_encoding.script_check_kwarg``).
    *language* is the mining-language code: Kindle and Lute/LWT files hold
    every language the user studies, and only this one's rows import.
    ``None`` keeps them all.
    """
    try:
        if path.stat().st_size > _MAX_IMPORT_BYTES:
            raise KnownWordsImportError("too_large")
        raw = path.read_bytes()
    except OSError as exc:
        raise KnownWordsImportError("unreadable") from exc

    encoding = "-"
    if raw.startswith(_SQLITE_MAGIC):
        result = _parse_kindle_vocab(path, language)
    else:
        text, encoding = _decode(raw, encodings, script_check)
        try:
            data = json.loads(text)
        except ValueError:
            if path.suffix.lower() == ".json":
                raise KnownWordsImportError("unrecognized") from None
            result = _parse_delimited_or_text(text, language)
        else:
            result = _parse_json(data)
    # Only the completed import gets a receipt. Every failure leaves through
    # KnownWordsImportError, whose ``reason`` the caller already reports; a
    # second line here would double-report the same refusal.
    log_summary(
        logger,
        "Known words import done",
        file=path,
        format=result.format_key,
        rows=result.total_entries,
        imported=len(result.words),
        skipped_malformed=result.skipped_malformed,
        encoding=encoding,
    )
    return result


def _result(format_key: str, words: set[str], total: int, skipped_malformed: int = 0) -> KnownWordsImportResult:
    if not words:
        raise KnownWordsImportError("no_known_words", format_key=format_key)
    return KnownWordsImportResult(
        format_key=format_key,
        words=frozenset(words),
        total_entries=total,
        skipped_malformed=skipped_malformed,
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
        words = {_clean(word) for word, status in data if status == 2 and _clean(word)}
        return _result("migaku_legacy", words, len(data))
    raise KnownWordsImportError("unrecognized")


def _parse_jpdb(cards: list[Any]) -> KnownWordsImportResult:
    words: set[str] = set()
    total = 0
    skipped_malformed = 0
    for card in cards:
        if not isinstance(card, dict) or not _clean(card.get("spelling")):
            skipped_malformed += 1
            continue
        total += 1
        raw_reviews = card.get("reviews")
        if raw_reviews is None:
            raw_reviews = []
        if not isinstance(raw_reviews, list):
            skipped_malformed += 1
            continue
        reviews = []
        for review in raw_reviews:
            if not isinstance(review, dict) or "grade" not in review:
                skipped_malformed += 1
                continue
            grade = review["grade"]
            if not isinstance(grade, str) or not grade:
                skipped_malformed += 1
                continue
            reviews.append(review)
        if not reviews:
            continue
        latest = max(reviews, key=_review_timestamp)
        if latest["grade"] not in _JPDB_EXCLUDED_GRADES:
            words.add(_clean(card["spelling"]))
    return _result("jpdb", words, total, skipped_malformed)


# ----------------------------------------------------------------------
# Kindle Vocabulary Builder (SQLite)
# ----------------------------------------------------------------------


def _parse_kindle_vocab(path: Path, language: str | None) -> KnownWordsImportResult:
    """Kindle ``vocab.db``: words marked mastered (category 100) are known (R24).

    The form is Kindle's ``stem`` (its dictionary form, the card-front
    convention) and else the looked-up ``word``. Opened read-only.
    """
    try:
        with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)) as conn:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            if "WORDS" not in tables:
                raise KnownWordsImportError("unrecognized")
            rows = conn.execute("SELECT word, stem, lang, category FROM WORDS").fetchall()
    except sqlite3.Error as exc:
        raise KnownWordsImportError("unreadable") from exc
    words: set[str] = set()
    total = 0
    skipped_malformed = 0
    for word, stem, lang, category in rows:
        form = _clean(stem) or _clean(word)
        if not form:
            skipped_malformed += 1
            continue
        if language is not None and _kindle_language(lang) != language:
            continue
        total += 1
        if category == _KINDLE_MASTERED:
            words.add(form)
    return _result("kindle_vocab", words, total, skipped_malformed)


def _kindle_language(value: Any) -> str:
    """Kindle's ``lang`` (``ja``, ``ja-JP``, ``pt_BR``) as a bare lowercase code."""
    return _clean(value).replace("_", "-").split("-")[0].lower()


# ----------------------------------------------------------------------
# Delimited / plain-text formats
# ----------------------------------------------------------------------


def _parse_delimited_or_text(text: str, language: str | None = None) -> KnownWordsImportResult:
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

    terms = _parse_term_status_table(text, language)
    if terms is not None:
        return terms
    return _parse_generic(text)


def _parse_term_status_table(text: str, language: str | None) -> KnownWordsImportResult | None:
    """A Lute or LWT term export, or ``None`` when the header is not one (R24).

    Lute's header names ``term`` and ``status``; LWT's columns are ``WoText``
    and ``WoStatus``. A language column (``language`` / ``LgName``) narrows
    the rows to the mining language when one is given; a file that never
    names it imports nothing rather than another language's words, and says
    which languages it holds (ruling C3). Lute joins the words of a
    multi-word term with zero-width spaces; they are removed.
    """
    lines = text.splitlines()
    if not lines:
        return None
    delimiter = "\t" if "\t" in lines[0] else ","
    rows = list(csv.reader(lines, delimiter=delimiter))
    header = [cell.strip().lower() for cell in rows[0]]
    term_at = _first_index(header, _TERM_HEADERS)
    status_at = _first_index(header, _STATUS_HEADERS)
    if term_at is None or status_at is None:
        return None
    language_at = _first_index(header, _LANGUAGE_HEADERS)
    needed = max(term_at, status_at)
    entries: list[list[str]] = []
    skipped_malformed = 0
    for row in rows[1:]:
        if not row:
            continue
        if len(row) <= needed or not _clean(row[term_at]):
            skipped_malformed += 1
            continue
        entries.append(row)
    if language is not None and language_at is not None:
        names = _language_names(language)
        named = [row[language_at].strip() if len(row) > language_at else "" for row in entries]
        in_language = [row for row, name in zip(entries, named, strict=True) if name.casefold() in names]
        found = sorted({name for name in named if name})
        if not in_language and found:
            raise KnownWordsImportError("other_language", format_key="lute_csv", languages=tuple(found))
        entries = in_language
    words = {
        row[term_at].replace("\u200b", "").strip()
        for row in entries
        if row[status_at].strip().casefold() in _TERM_KNOWN_STATUSES
    }
    return _result("lute_csv", words, len(entries), skipped_malformed)


def _first_index(header: list[str], names: tuple[str, ...]) -> int | None:
    """Position of the first column whose name is one of ``names``."""
    for name in names:
        if name in header:
            return header.index(name)
    return None


def _language_names(code: str) -> frozenset[str]:
    """What a Lute/LWT language column may call the mining language: code, English and native name."""
    # Function-local: languages.profile imports services (the subtitle_parser note).
    from anki_miner.languages.registry import get_profile

    profile = get_profile(code)
    return frozenset(name.strip().casefold() for name in (code, profile.english_name, profile.display_name) if name)


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


def _decode(
    raw: bytes,
    encodings: tuple[str, ...] | None = None,
    script_check: Callable[[str], bool] | None = None,
) -> tuple[str, str]:
    # The shared ladder decoder, not a second copy: a Taiwanese export is Big5,
    # which gb18030 swallows into PUA mojibake without raising, and only that
    # one has the guard for it.
    #
    # `is None`, not truthiness: `()` is an EMPTY ladder the caller asked for,
    # not a request for the Japanese default. Truthiness would decode a
    # profile's deliberately-empty ladder as Japanese.
    try:
        return decode_with_ladder(
            raw,
            encodings=_DEFAULT_ENCODINGS if encodings is None else encodings,
            script_check=script_check,
        )
    except SetupError as exc:
        # Every candidate failing => "undecodable", not "unreadable": the bytes
        # were read, so only the encoding is at fault.
        raise KnownWordsImportError("undecodable") from exc


def _clean(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _review_timestamp(review: dict[str, Any]) -> float:
    """Sort key for jpdb reviews: the numeric ``timestamp``, else 0.

    A non-numeric ``timestamp`` (a drifted/hand-edited export) is coerced to 0
    rather than fed to ``max()`` — otherwise comparing a str against the int-0
    default would raise TypeError and escape the KnownWordsImportError contract
    as a generic "unexpected error". ``bool`` is an ``int`` subclass and floats
    fine.
    """
    ts = review.get("timestamp", 0)
    return float(ts) if isinstance(ts, (int, float)) else 0.0
