"""The five hazm ``.dat`` tables, read from one directory.

Standard library only: this loads on a machine with no pack, and the caller
decides what to do when the directory is missing (``FileNotFoundError`` names
the file it could not read).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

#: The tag hazm writes for an attested word carrying no part of speech. 158,034
#: of the 193,350 rows have it (plan probe P-2), so it becomes an empty tuple
#: and a caller writes ``if tags:`` instead of comparing to a sentinel.
UNTAGGED = "0"

WORDS_FILE = "words.dat"
VERBS_FILE = "verbs.dat"
IVERBS_FILE = "iverbs.dat"
IWORDS_FILE = "iwords.dat"
STOPWORDS_FILE = "stopwords.dat"


@dataclass(frozen=True)
class HazmData:
    """One loaded copy of the Persian tables.

    ``words`` maps a word to its POS tags (empty for an attested-only row),
    ``verb_lines`` are the raw ``past#present`` lines in file order (first line
    wins on a homograph), ``iverb_rows`` pair a verb line with its informal
    present stem, and ``iwords`` maps an informal spelling to its formal one.
    """

    words: dict[str, tuple[str, ...]]
    stopwords: frozenset[str]
    verb_lines: tuple[str, ...]
    iverb_rows: tuple[tuple[str, str], ...]
    iwords: dict[str, str]


def _lines(root: Path, name: str) -> list[str]:
    return [line for line in (root / name).read_text(encoding="utf-8").splitlines() if line.strip()]


def _parse_words(lines: list[str]) -> dict[str, tuple[str, ...]]:
    words: dict[str, tuple[str, ...]] = {}
    for line in lines:
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        word, _count, tags = parts
        words[word] = tuple(tag for tag in tags.split(",") if tag and tag != UNTAGGED)
    return words


def _parse_iverbs(lines: list[str]) -> tuple[tuple[str, str], ...]:
    rows: list[tuple[str, str]] = []
    for line in lines:
        parts = line.split(" ")
        if len(parts) < 2 or "#" not in parts[0]:
            continue
        rows.append((parts[0], parts[1]))
    return tuple(rows)


def _parse_iwords(lines: list[str]) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for line in lines:
        parts = line.split(" ")
        if len(parts) != 2:
            continue
        informal, formal = parts
        pairs.setdefault(informal, formal)
    return pairs


def load(root: Path) -> HazmData:
    """Read the five tables from *root*, upstream formats unchanged."""
    return HazmData(
        words=_parse_words(_lines(root, WORDS_FILE)),
        stopwords=frozenset(line.strip() for line in _lines(root, STOPWORDS_FILE)),
        verb_lines=tuple(line.strip() for line in _lines(root, VERBS_FILE)),
        iverb_rows=_parse_iverbs(_lines(root, IVERBS_FILE)),
        iwords=_parse_iwords(_lines(root, IWORDS_FILE)),
    )
