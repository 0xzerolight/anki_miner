"""The analysis indexes of a CAMeL morphology database (ported, see ``__init__``).

Upstream ``MorphologyDB`` with the ``"a"`` flag: it reads the same file layout
section by section and fills the same hashes, but builds no generation or
reinflection tables and has no builtin-database catalogue (the catalogue is the
network path this port exists to avoid). The caller passes the path of the
``morphology.db`` the language pack extracted.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

from .errors import DatabaseParseError
from .morph_utils import strip_lex

__all__ = ["MorphologyDB"]

#: One ``(category, features)`` entry of a prefix, stem or suffix hash.
Entry = tuple[str, dict[str, Any]]


def _parse_analysis_toks(toks: list[str]) -> dict[str, Any]:
    res: dict[str, Any] = {}
    for tok in toks:
        if len(tok) == 0:
            continue
        subtoks = tok.split(":")
        if len(subtoks) < 2:
            raise DatabaseParseError(f"invalid key value pair {tok!r}")
        res[subtoks[0]] = ":".join(subtoks[1:])
    return res


def _parse_defaults_toks(toks: list[str]) -> dict[str, Any]:
    res: dict[str, Any] = {}
    for tok in toks:
        subtoks = tok.split(":")
        if len(subtoks) < 2:
            raise DatabaseParseError(f"invalid key value pair {tok!r} in DEFAULTS")
        val = ":".join(subtoks[1:])
        res[subtoks[0]] = None if val == "*" else val
    return res


class MorphologyDB:
    """Prefix/stem/suffix hashes and compatibility tables for analysis."""

    def __init__(self, fpath: str | Path) -> None:
        self.defines: dict[str, list[str] | None] = {}
        self.defaults: dict[str, dict[str, Any]] = {}
        self.order: list[str] | None = None
        self.compute_feats: frozenset[str] = frozenset()

        self.prefix_hash: dict[str, list[Entry]] = {}
        self.suffix_hash: dict[str, list[Entry]] = {}
        self.stem_hash: dict[str, list[Entry]] = {}

        self.prefix_stem_compat: dict[str, set[str]] = {}
        self.stem_suffix_compat: dict[str, set[str]] = {}
        self.prefix_suffix_compat: dict[str, set[str]] = {}
        self.max_prefix_size = 0
        self.max_suffix_size = 0

        with open(fpath, encoding="utf-8") as dbfile:
            self._parse(iter(dbfile))

    def _parse(self, lines: Iterator[str]) -> None:
        self._parse_defines(lines)
        self._parse_defaults(lines)
        self._parse_order(lines)
        # TOKENIZATIONS and STEMBACKOFF feed tokenisation-scheme listings and
        # the backoff modes; analysis with backoff NONE reads neither.
        self._skip_to(lines, "###STEMBACKOFF###")
        self._skip_to(lines, "###PREFIXES###")
        self._parse_affixes(lines, self.prefix_hash, "###SUFFIXES###", "PREFIXES")
        self._parse_affixes(lines, self.suffix_hash, "###STEMS###", "SUFFIXES")
        self._parse_stems(lines)
        self._parse_table(lines, self.prefix_stem_compat, "###TABLE BC###", "TABLE AB")
        self._parse_table(lines, self.stem_suffix_compat, "###TABLE AC###", "TABLE BC")
        self._parse_table(lines, self.prefix_suffix_compat, None, "TABLE AC")
        self.max_prefix_size = max((len(prefix) for prefix in self.prefix_hash), default=0)
        self.max_suffix_size = max((len(suffix) for suffix in self.suffix_hash), default=0)

    def _parse_defines(self, lines: Iterator[str]) -> None:
        for raw in lines:
            line = raw.strip()
            if line == "###DEFINES###":
                continue
            if line == "###DEFAULTS###":
                return
            toks = line.split(" ")
            if len(toks) < 3 or toks[0] != "DEFINE":
                raise DatabaseParseError(f"invalid DEFINES line {line!r}")
            open_class = False
            values: set[str] = set()
            for tok in toks[2:]:
                subtoks = tok.split(":")
                if len(subtoks) != 2 and subtoks[0] != toks[1]:
                    raise DatabaseParseError(f"invalid key value pair {tok!r} in DEFINES")
                if len(toks) == 3 and subtoks[1] == "*open*":
                    open_class = True
                    break
                values.add(subtoks[1])
            self.defines[toks[1]] = None if open_class else list(values)

    def _parse_defaults(self, lines: Iterator[str]) -> None:
        for raw in lines:
            line = raw.strip()
            if line == "###ORDER###":
                return
            toks = line.split(" ")
            if len(toks) < 2 or toks[0] != "DEFAULT":
                raise DatabaseParseError(f"invalid DEFAULTS line {line!r}")
            parsed = _parse_defaults_toks(toks[1:])
            if "pos" not in parsed:
                raise DatabaseParseError(f"DEFAULTS line {line!r} missing pos value")
            self.defaults[parsed["pos"]] = parsed

    def _parse_order(self, lines: Iterator[str]) -> None:
        for raw in lines:
            line = raw.strip()
            if line == "###TOKENIZATIONS###":
                self.compute_feats = frozenset(self.order or ())
                return
            toks = line.split(" ")
            if len(toks) < 2 or toks[0] != "ORDER":
                raise DatabaseParseError(f"invalid ORDER line {line!r}")
            if toks[1] not in self.defines:
                raise DatabaseParseError(f"invalid feature {toks[1]!r} in ORDER line.")
            self.order = toks[1:]

    @staticmethod
    def _skip_to(lines: Iterator[str], marker: str) -> None:
        for raw in lines:
            if raw.strip() == marker:
                return

    @staticmethod
    def _parse_affixes(lines: Iterator[str], table: dict[str, list[Entry]], end: str, section: str) -> None:
        for line in lines:
            parts = line.split("\t")
            if len(parts) != 3:
                if line.strip() == end:
                    return
                raise DatabaseParseError(f"invalid {section} line {line!r}")
            affix = parts[0].strip()
            analysis = _parse_analysis_toks(parts[2].strip().split(" "))
            table.setdefault(affix, []).append((parts[1], analysis))

    def _parse_stems(self, lines: Iterator[str]) -> None:
        for raw in lines:
            line = raw.strip()
            if line == "###TABLE AB###":
                return
            parts = line.split("\t")
            if len(parts) != 3:
                raise DatabaseParseError(f"invalid STEMS line {line!r}")
            analysis = _parse_analysis_toks(parts[2].split(" "))
            analysis["lex"] = strip_lex(analysis["lex"])
            self.stem_hash.setdefault(parts[0], []).append((parts[1], analysis))

    @staticmethod
    def _parse_table(lines: Iterator[str], table: dict[str, set[str]], end: str | None, section: str) -> None:
        for raw in lines:
            line = raw.strip()
            if end is not None and line == end:
                return
            toks = line.split()
            if len(toks) != 2:
                raise DatabaseParseError(f"invalid {section} line {line!r}")
            table.setdefault(toks[0], set()).add(toks[1])
