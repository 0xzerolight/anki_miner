"""Kana-written words the parser mines by name (もう, この, それ, しかし, いつも).

The script gate drops pure-hiragana tokens, and kana recovery brings back only
verbs and adjectives, because the tagger cannot tell a real kana word from a
grammar fragment by its tags. This list names the common ones that are words,
built from JMdict's common entries by ``scripts/build_kana_words.py``, which
also records what was left out and why.

A word row keys on the card front and the pos1 unidic-lite gives the word in a
sentence, so a listed homograph under another tag stays out: その the
pre-noun adjective (その日) is listed, その the filler is not.

A join row names a word the tagger cuts apart (いつ|も, どう|いう), which
``LexicalJoiner`` joins back; the merged token then matches on its front alone.
A ``start`` row joins only where it opens a line or clause, as there it is a
conjunction (でも, だから) and elsewhere the particles (学校でも).
"""

from __future__ import annotations

from functools import cache
from importlib.resources import files
from typing import NamedTuple


class KanaJoin(NamedTuple):
    """A kana word the tagger cuts apart, joined back by ``LexicalJoiner``."""

    headword: str
    pos1: str
    line_start: bool


class KanaWords(NamedTuple):
    words: frozenset[tuple[str, str]]
    joins: dict[tuple[str, ...], KanaJoin]
    joined: frozenset[str]


@cache
def kana_words() -> KanaWords:
    """The bundled list, read once."""
    text = files("anki_miner.resources.kana_words").joinpath("ja.tsv").read_text(encoding="utf-8")
    words: set[tuple[str, str]] = set()
    joins: dict[tuple[str, ...], KanaJoin] = {}
    for line in text.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        form, pos1, *join = line.split("\t")
        if join:
            joins[tuple(join[0].split("|"))] = KanaJoin(form, pos1, join[1:] == ["start"])
        else:
            words.add((form, pos1))
    return KanaWords(frozenset(words), joins, frozenset(row.headword for row in joins.values()))


def is_listed(front: str, pos1: str, *, merged: bool) -> bool:
    """Whether a kana card front is on the list: a join's headword for a merged token, else with its pos1."""
    listed = kana_words()
    return front in listed.joined if merged else (front, pos1) in listed.words
