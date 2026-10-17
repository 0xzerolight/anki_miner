"""Grammar tags as one envelope attribute: the provider writes it, the grammar hook reads it.

A dictionary's gender and aspect tags reach the card as ``data-am-grammar`` on the
provider's ``<li data-dictionary="…">`` envelope, a space-separated, sorted set of
``gender:<word>`` / ``aspect:<id>`` tokens. Every other tag contributes nothing.
``languages/_spaced`` imports this module, never the reverse.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:  # annotation-only: storage imports languages/_spaced at runtime
    from anki_miner.services.dictionary.storage import TagMeta

GRAMMAR_ATTR = "data-am-grammar"

#: Gender word (``gender-<word>`` category, ``gender:<word>`` token) → the grammar hook's gender id.
_GENDER_IDS = {"masculine": "masc", "feminine": "fem", "neuter": "neut", "common": "common"}
_GENDER_CATEGORY_PREFIX = "gender-"
#: wty files its aspect tags under ``aspect``; the tag name is the aspect.
_ASPECT_NAMES = {"impf": "impf", "impf-only": "impf", "pf": "pf", "pf-only": "pf"}
#: OpenRussian files its aspect tags (``pf``/``ipf``/``both``) with no category; the notes name the aspect.
_NOTE_ASPECTS = {"perfective": ("pf",), "imperfective": ("impf",), "perfective and imperfective": ("impf", "pf")}
_ASPECT_IDS = frozenset({"impf", "pf"})

_BLOCK_START = '<li data-dictionary="'
_ATTR_RE = re.compile(GRAMMAR_ATTR + r'="([^"]*)"')


class GrammarTags(NamedTuple):
    """The grammar a block's envelope names: gender ids (``masc fem neut common``) and aspect ids (``impf pf``)."""

    genders: frozenset[str]
    aspects: frozenset[str]


def _tokens(meta: TagMeta) -> tuple[str, ...]:
    if meta.category.startswith(_GENDER_CATEGORY_PREFIX):
        word = meta.category[len(_GENDER_CATEGORY_PREFIX) :]
        return (f"gender:{word}",) if word in _GENDER_IDS else ()
    if meta.category == "aspect":
        aspect = _ASPECT_NAMES.get(meta.name)
        return (f"aspect:{aspect}",) if aspect is not None else ()
    if meta.category == "":
        return tuple(f"aspect:{aspect}" for aspect in _NOTE_ASPECTS.get(meta.notes, ()))
    return ()


def encode_grammar_tags(metas: Iterable[TagMeta]) -> str:
    """The ``data-am-grammar`` value for one entry's tags; ``""`` when none is a gender or aspect tag."""
    return " ".join(sorted({token for meta in metas for token in _tokens(meta)}))


def first_block(definition_html: str) -> str:
    """The first ``<li data-dictionary="…">`` block up to the next one; the whole HTML when it has none."""
    start = definition_html.find(_BLOCK_START)
    if start < 0:
        return definition_html
    end = definition_html.find(_BLOCK_START, start + len(_BLOCK_START))
    return definition_html[start:] if end < 0 else definition_html[start:end]


def parse_grammar_attr(block_html: str) -> GrammarTags:
    """The genders and aspects the FIRST dictionary block's envelope names; empty sets when it names none."""
    match = _ATTR_RE.search(first_block(block_html))
    if match is None:
        return GrammarTags(frozenset(), frozenset())
    genders: set[str] = set()
    aspects: set[str] = set()
    for token in match.group(1).split():
        kind, _, value = token.partition(":")
        if kind == "gender" and value in _GENDER_IDS:
            genders.add(_GENDER_IDS[value])
        elif kind == "aspect" and value in _ASPECT_IDS:
            aspects.add(value)
    return GrammarTags(frozenset(genders), frozenset(aspects))
