"""Fixed-column word exports (WP-34, owner ruling R10).

Two sources, one shape each, no options: Manage Known Words exports the rows
it shows (word, source, added_at_utc), and a finished run's results export its
mined and not-mined words with the reason each was skipped (word, status,
reason). The reason is the stable ``NotMinedReason`` key, untranslated: this is
data for a spreadsheet, and a column a user filters on must not change with the
interface language.

CSV is UTF-8 with a byte-order mark, so a spreadsheet opens Japanese, Chinese
or Arabic as text, with RFC 4180 line ends. The plain list (one word per line,
no BOM) is what Manage Known Words always exported for round-tripping into
other tools such as jiten.moe. Every write is staged and moved into place, so
a failed export never leaves half a file.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable, Sequence
from pathlib import Path

from anki_miner.models.processing import NotMinedReason, NotMinedReport
from anki_miner.services.known_word_db import KnownWordRow
from anki_miner.utils.atomic_io import atomic_write_path

KNOWN_WORDS_COLUMNS: tuple[str, str, str] = ("word", "source", "added_at_utc")
RUN_WORDS_COLUMNS: tuple[str, str, str] = ("word", "status", "reason")


def run_word_rows(report: NotMinedReport) -> list[tuple[str, str, str]]:
    """A run's words as export rows: mined first, then each reason in pipeline order.

    ``report.forms`` keeps a mined word out of every reason and lets "known"
    outrank the rest, exactly as the Activity Log's "Not mined" block lists them.
    """
    rows: list[tuple[str, str, str]] = [(word, "mined", "") for word in sorted(report.mined)]
    for reason in NotMinedReason:
        rows.extend((word, "not_mined", reason.value) for word in sorted(report.forms(reason)))
    return rows


def write_known_words_csv(path: Path, rows: Iterable[KnownWordRow]) -> int:
    """Write Manage Known Words rows as CSV; return how many rows were written."""
    return _write_csv(path, KNOWN_WORDS_COLUMNS, [(row.lemma, row.source, row.added_at) for row in rows])


def write_run_words_csv(path: Path, report: NotMinedReport) -> int:
    """Write a run's mined and not-mined words as CSV; return how many rows were written."""
    return _write_csv(path, RUN_WORDS_COLUMNS, run_word_rows(report))


def write_word_list(path: Path, words: Iterable[str]) -> int:
    """Write one word per line (UTF-8, no BOM); return how many words were written."""
    items = list(words)
    text = "\n".join(items) + ("\n" if items else "")
    _write_bytes(path, text.encode("utf-8"))
    return len(items)


def _write_csv(path: Path, header: Sequence[str], rows: Sequence[Sequence[str]]) -> int:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(header)
    writer.writerows(rows)
    _write_bytes(path, buffer.getvalue().encode("utf-8-sig"))
    return len(rows)


def _write_bytes(path: Path, data: bytes) -> None:
    with atomic_write_path(path) as staged:
        staged.write_bytes(data)
