"""GrammarTagHook (spec §4.8, R17): noun gender, article and plural for the card back.

Gender, in order (en plan D8): the token's ``morph`` ``Gender=`` — unless the
first dictionary block carries gender chips that exclude it (a tagger error
the dictionary contradicts: de ``Oma`` tagged Masc); exactly one gender chip
(wty unions every row of a sequence, so ``See`` shows fem+masc+neut); the
first ``Grammar-content`` head line's gender letter (``Fuchs m (…``). The
letter is matched on a copy with combining marks dropped (E.10 D2:
``knjȉga f``); the plural is read from the unfolded NFC line so ``Füchse`` and
``élèves`` keep their marks, skipping qualifiers (``plural (uncommon)
Ersätze``) and never capturing ``only``/``and``/``or``. The rendered HTML never
carries ``definitionTags`` except as those chips. Pure; never raises on odd
HTML; ``{}`` for a non-noun.
"""

from __future__ import annotations

import html as html_lib
import re
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # annotation-only
    from anki_miner.config.config import AnkiMinerConfig

GRAMMAR_FIELDS: tuple[str, ...] = ("noun_gender", "noun_article", "noun_plural")

DEFAULT_GENDER_LABELS: Mapping[str, str] = MappingProxyType(
    {"masc": "masculine", "fem": "feminine", "neut": "neuter", "common": "common"}
)
_EMPTY: Mapping[str, str] = MappingProxyType({})

_MORPH_GENDER = {"Masc": "masc", "Fem": "fem", "Neut": "neut", "Com": "common"}
_CHIP_GENDER = {"masculine": "masc", "feminine": "fem", "neuter": "neut", "common": "common"}
_HEAD_GENDER = {"m": "masc", "f": "fem", "n": "neut", "c": "common"}

_BLOCK_START = '<li data-dictionary="'
_CHIP_RE = re.compile(r'<span class="gloss-tag" data-category="gender-(masculine|feminine|neuter|common)"')
_HEAD_RE = re.compile(r'data-sc-content="Grammar-content"[^>]*>(.*?)</div>', re.S)
_TAG_RE = re.compile(r"<[^>]+>")
#: de A2 + fr: qualifiers in parentheses are skipped; "plural only/and/or" is not a form.
_PLURAL_RE = re.compile(r"\bplural\s+(?:\([^()]*\)\s+)*(?!(?:only|and|or)\b)([^\s,()]+)")


def _first_block(definition_html: str) -> str:
    start = definition_html.find(_BLOCK_START)
    if start < 0:
        return definition_html
    end = definition_html.find(_BLOCK_START, start + len(_BLOCK_START))
    return definition_html[start:] if end < 0 else definition_html[start:end]


def _head_line(block: str) -> str:
    """The first head line, tags removed, entities unescaped, NFC — marks INTACT."""
    match = _HEAD_RE.search(block)
    if match is None:
        return ""
    return unicodedata.normalize("NFC", html_lib.unescape(_TAG_RE.sub("", match.group(1))))


def _without_combining_marks(text: str) -> str:
    stripped = "".join(ch for ch in unicodedata.normalize("NFD", text) if not 0x0300 <= ord(ch) <= 0x036F)
    return unicodedata.normalize("NFC", stripped)


def _gender_from_morph(morph: str) -> str | None:
    for feature in morph.split("|"):
        name, _, value = feature.partition("=")
        if name == "Gender":
            return _MORPH_GENDER.get(value)  # "Fem,Masc" maps to None
    return None


def _gender_from_head(head: str) -> str | None:
    tokens = _without_combining_marks(head).split("(", 1)[0].split()
    if len(tokens) < 2 or (len(tokens) >= 3 and tokens[-2] == "or"):
        return None
    return _HEAD_GENDER.get(tokens[-1])


class GrammarTagHook:
    """One hook for ``noun_gender`` / ``noun_article`` / ``noun_plural`` (R17 — no per-field classes).

    ``article_map`` and ``gender_labels`` are keyed by ``masc fem neut common``.
    ``article_rule(gender, headword)`` (headword = the card front) covers
    articles that depend on the word's spelling (it ``lo``/``l'``, fr ``l'``);
    it wins over ``article_map`` and ``""`` omits the field.
    """

    def __init__(
        self,
        fields: Sequence[str],
        *,
        article_map: Mapping[str, str] = _EMPTY,
        article_rule: Callable[[str, str], str] | None = None,
        gender_labels: Mapping[str, str] = DEFAULT_GENDER_LABELS,
        noun_pos: frozenset[str] = frozenset({"NOUN"}),
    ) -> None:
        unknown = [name for name in fields if name not in GRAMMAR_FIELDS]
        if unknown:
            raise ValueError(f"unknown grammar fields: {unknown}")
        if "noun_article" in fields and not article_map and article_rule is None:
            raise ValueError("noun_article needs an article_map or an article_rule")
        self._fields = tuple(fields)
        self._article_map = article_map
        self._article_rule = article_rule
        self._gender_labels = gender_labels
        self._noun_pos = noun_pos

    def field_names(self) -> tuple[str, ...]:
        return self._fields

    def _gender(self, word: Any, block: str, head: str) -> str | None:
        chip_genders = {_CHIP_GENDER[name] for name in _CHIP_RE.findall(block)}
        morph_gender = _gender_from_morph(str(getattr(word, "morph", "") or ""))
        if morph_gender is not None and (not chip_genders or morph_gender in chip_genders):
            return morph_gender
        if len(chip_genders) == 1:
            return next(iter(chip_genders))
        return _gender_from_head(head)

    def render(self, word: Any, *, config: AnkiMinerConfig) -> dict[str, str]:
        del config  # mapped field name is the switch
        if getattr(word, "pos", None) not in self._noun_pos:
            return {}
        block = _first_block(str(getattr(word, "definition_html", "") or ""))
        head = _head_line(block)
        out: dict[str, str] = {}
        if "noun_gender" in self._fields or "noun_article" in self._fields:
            gender = self._gender(word, block, head)
            if gender is not None:
                if "noun_gender" in self._fields and gender in self._gender_labels:
                    out["noun_gender"] = self._gender_labels[gender]
                if "noun_article" in self._fields:
                    if self._article_rule is not None:
                        article = self._article_rule(gender, str(getattr(word, "mined_form", "") or ""))
                    else:
                        article = self._article_map.get(gender, "")
                    if article:
                        out["noun_article"] = article
        if "noun_plural" in self._fields:
            match = _PLURAL_RE.search(head)
            if match is not None:
                out["noun_plural"] = match.group(1)
        return out
