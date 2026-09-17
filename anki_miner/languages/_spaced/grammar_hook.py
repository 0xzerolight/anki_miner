"""GrammarTagHook (spec §4.8, R17): noun gender, article and plural, and verb aspect, for the card back.

Gender, in order (en plan D8): the token's ``morph`` ``Gender=`` — unless the
first dictionary block carries gender chips that exclude it (a tagger error
the dictionary contradicts: de ``Oma`` tagged Masc); exactly one gender chip
(wty unions every row of a sequence, so ``See`` shows fem+masc+neut); the
first ``Grammar-content`` head line's gender letter (``Fuchs m (…``).
``sources`` reorders those three rules: nl reads the dictionary first, because
a diminutive ``hondje`` lemmatised to ``hond`` carries ``Gender=Neut`` and
``hond``'s chips include an obsolete neuter row. The letter is matched on a
copy with combining marks dropped (E.10 D2: ``knjȉga f``); the plural is read
from the unfolded NFC line so ``Füchse`` and ``élèves`` keep their marks,
skipping qualifiers (``plural (uncommon) Ersätze``) and never capturing
``only``/``and``/``or``. With no single gender and no ``article_rule``, the
article is the one every gender of the first gender-bearing source shares (nl
``koffie f or m`` → ``de``); an injective map never shares one. The rendered
HTML never carries ``definitionTags`` except as those chips. Pure; never raises
on odd HTML; ``{}`` for a non-noun.

Aspect (``aspect_pair``, Ruling S1) is the verb path of the same hook: the same
three rules in the same ``sources`` order — ``morph`` ``Aspect=`` unless the
first block's aspect chips exclude it; exactly one aspect chip (wty unions every
row of a term, so both chips can be two lexemes: sh ``kupiti``); the head line's
closing aspect word(s) (``čìtati impf (…``, ``impf or pf``). The partner is the
head line's opposite-aspect clause (``perfective pročìtati``), qualifiers
dropped, kept verbatim unless the language passes ``partner_fold``: the D2 mark
strip would turn ``čitati`` into ``citati``. A head line is never split on a
newline — the renderer turns one into ``<br>`` and the tag strip glues the lines.
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

GRAMMAR_FIELDS: tuple[str, ...] = ("noun_gender", "noun_article", "noun_plural", "aspect_pair")

DEFAULT_GENDER_LABELS: Mapping[str, str] = MappingProxyType(
    {"masc": "masculine", "fem": "feminine", "neut": "neuter", "common": "common"}
)
_EMPTY: Mapping[str, str] = MappingProxyType({})

_MORPH_GENDER = {"Masc": "masc", "Fem": "fem", "Neut": "neut", "Com": "common"}
_CHIP_GENDER = {"masculine": "masc", "feminine": "fem", "neuter": "neut", "common": "common"}
_HEAD_GENDER = {"m": "masc", "f": "fem", "n": "neut", "c": "common"}

#: Aspect ids: imperfective, perfective, and a verb that is both.
ASPECTS: tuple[str, ...] = ("impf", "pf", "biaspectual")
DEFAULT_ASPECT_LABELS: Mapping[str, str] = MappingProxyType(
    {"impf": "imperfective", "pf": "perfective", "biaspectual": "imperfective or perfective"}
)

_MORPH_ASPECT = {"Imp": "impf", "Perf": "pf"}
_CHIP_ASPECT = {"impf": "impf", "impf-only": "impf", "pf": "pf", "pf-only": "pf"}
_HEAD_ASPECT = {"impf": "impf", "pf": "pf"}
_OPPOSITE_ASPECT = {"impf": "pf", "pf": "impf"}
#: The English word a wty head line names an aspect with: ``(…, perfective pročìtati)``.
_HEAD_ASPECT_WORD = {"impf": "imperfective", "pf": "perfective"}
_ASPECT_CHIP_RE = re.compile(r'<span class="gloss-tag" data-category="aspect"[^>]*>([^<]+)</span>')
_QUALIFIER_RE = re.compile(r"\([^()]*\)")
#: pl motion verbs qualify the partner: ``imperfective determinate czytać``.
_PARTNER_LEAD_WORDS = frozenset({"determinate", "indeterminate"})

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


#: The gender rules a hook applies, in the default (en D8) order.
GENDER_SOURCES: tuple[str, ...] = ("morph", "chips", "head")


def _morph_values(morph: str, *, feature: str = "Gender", table: Mapping[str, str] = _MORPH_GENDER) -> frozenset[str]:
    """Every value a morph feature names: ``Gender=Com,Neut`` → {common, neut}; ``Aspect=Imp,Perf`` → {impf, pf}.

    Empty when the feature is absent or any part is outside ``table`` (lt ``Aspect=Hab`` is not a Slavic aspect).
    """
    for item in morph.split("|"):
        name, _, value = item.partition("=")
        if name == feature:
            found = [table.get(part) for part in value.split(",")]
            return frozenset() if None in found else frozenset(v for v in found if v is not None)
    return frozenset()


def _head_values(head: str, *, table: Mapping[str, str] = _HEAD_GENDER) -> frozenset[str]:
    """Every value in the head line's closing or-chain: ``boek n`` → {neut}; ``raam n or f or m`` → all three.

    The same walk reads the aspect word(s) closing the headword part (``čìtati impf (…`` → {impf};
    ``specìfikovati or spècifikovati impf or pf`` → both). Empty when there is no headword before the tokens
    or any token is outside ``table``.
    """
    tokens = _without_combining_marks(head).split("(", 1)[0].split()
    if len(tokens) < 2:
        return frozenset()
    letters = [tokens[-1]]
    index = len(tokens) - 2
    while index >= 0 and tokens[index] == "or":
        if index < 2:
            return frozenset()
        letters.append(tokens[index - 1])
        index -= 2
    found = [table.get(letter) for letter in letters]
    return frozenset() if None in found else frozenset(v for v in found if v is not None)


def _aspect_id(aspects: frozenset[str]) -> str | None:
    """One aspect, or ``biaspectual`` for both; ``None`` when nothing is named."""
    if len(aspects) == 1:
        return next(iter(aspects))
    return "biaspectual" if aspects == {"impf", "pf"} else None


def _top_level_clauses(text: str) -> list[str]:
    """Comma-separated clauses of a parenthetical up to its closing bracket; a nested ``(…)`` stays in its clause."""
    clauses: list[str] = []
    current: list[str] = []
    depth = 0
    for char in text:
        if char == "(":
            depth += 1
        elif char == ")":
            if depth == 0:
                break
            depth -= 1
        elif char == "," and depth == 0:
            clauses.append("".join(current))
            current = []
            continue
        current.append(char)
    clauses.append("".join(current))
    return clauses


def _partner(head: str, aspect: str, fold: Callable[[str], str] | None) -> tuple[str, str]:
    """The head line's partner clause, qualifiers dropped: ``(…, perfective pročìtati)`` → ``("pf", "pročìtati")``.

    A one-aspect verb reads only the opposite aspect's clause (pl ``chodzić impf (…, imperfective determinate iść,
    perfective pójść)`` → ``pójść``); a biaspectual verb reads the first clause naming either aspect. ``("", "")``
    when there is none.
    """
    wanted = ("impf", "pf") if aspect == "biaspectual" else (_OPPOSITE_ASPECT[aspect],)
    words = {_HEAD_ASPECT_WORD[name]: name for name in wanted}
    _, bracket, inner = head.partition("(")
    if not bracket:
        return "", ""
    for clause in _top_level_clauses(inner):
        tokens = clause.split()
        if not tokens or tokens[0] not in words:
            continue
        rest = _QUALIFIER_RE.sub(" ", " ".join(tokens[1:])).split()
        while rest and rest[0] in _PARTNER_LEAD_WORDS:
            rest = rest[1:]
        if rest:
            partner = " ".join(rest)
            return words[tokens[0]], (fold(partner) if fold is not None else partner)
    return "", ""


class GrammarTagHook:
    """One hook for ``noun_gender`` / ``noun_article`` / ``noun_plural`` (R17 — no per-field classes).

    ``article_map`` and ``gender_labels`` are keyed by ``masc fem neut common``.
    ``article_rule(gender, headword)`` (headword = the card front) covers
    articles that depend on the word's spelling (it ``lo``/``l'``, fr ``l'``);
    it wins over ``article_map`` and ``""`` omits the field.

    ``aspect_labels`` is keyed by ``impf pf biaspectual``; a word in
    ``verb_pos`` renders only ``aspect_pair``, a word in ``noun_pos`` only the
    noun fields. ``partner_fold`` folds the partner a language prints on the
    card (hr strips tone marks); by default it is kept verbatim.
    """

    def __init__(
        self,
        fields: Sequence[str],
        *,
        article_map: Mapping[str, str] = _EMPTY,
        article_rule: Callable[[str, str], str] | None = None,
        gender_labels: Mapping[str, str] = DEFAULT_GENDER_LABELS,
        noun_pos: frozenset[str] = frozenset({"NOUN"}),
        sources: Sequence[str] = GENDER_SOURCES,
        aspect_labels: Mapping[str, str] = DEFAULT_ASPECT_LABELS,
        verb_pos: frozenset[str] = frozenset({"VERB"}),
        partner_fold: Callable[[str], str] | None = None,
    ) -> None:
        unknown = [name for name in fields if name not in GRAMMAR_FIELDS]
        if unknown:
            raise ValueError(f"unknown grammar fields: {unknown}")
        if "noun_article" in fields and not article_map and article_rule is None:
            raise ValueError("noun_article needs an article_map or an article_rule")
        if len(set(sources)) != len(sources) or not set(sources) <= set(GENDER_SOURCES):
            raise ValueError(f"sources must be distinct names from {GENDER_SOURCES}: {tuple(sources)}")
        if "aspect_pair" in fields and not set(ASPECTS) <= set(aspect_labels):
            raise ValueError(f"aspect_labels must name every aspect in {ASPECTS}")
        self._fields = tuple(fields)
        self._article_map = article_map
        self._article_rule = article_rule
        self._gender_labels = gender_labels
        self._noun_pos = noun_pos
        self._sources = tuple(sources)
        self._aspect_labels = aspect_labels
        self._verb_pos = verb_pos
        self._partner_fold = partner_fold

    def field_names(self) -> tuple[str, ...]:
        return self._fields

    def _gender(self, word: Any, block: str, head: str) -> str | None:
        chip_genders = {_CHIP_GENDER[name] for name in _CHIP_RE.findall(block)}
        for source in self._sources:
            if source == "morph":
                morph_gender = _gender_from_morph(str(getattr(word, "morph", "") or ""))
                if morph_gender is not None and (not chip_genders or morph_gender in chip_genders):
                    return morph_gender
            elif source == "chips":
                if len(chip_genders) == 1:
                    return next(iter(chip_genders))
            else:
                head_gender = _gender_from_head(head)
                if head_gender is not None:
                    return head_gender
        return None

    def _shared_article(self, word: Any, block: str, head: str) -> str:
        """No single gender: the one article every gender of the FIRST gender-bearing source maps to, else "".

        Only a non-injective ``article_map`` can answer (nl: ``koffie f or m`` → ``de``); a source whose genders
        disagree ends the search (``raam n or f or m`` prints nothing). Morph counts only when the chips do not
        exclude it, as in ``_gender``.
        """
        chip_genders = frozenset(_CHIP_GENDER[name] for name in _CHIP_RE.findall(block))
        for source in self._sources:
            if source == "morph":
                genders = _morph_values(str(getattr(word, "morph", "") or ""))
                if chip_genders and not genders <= chip_genders:
                    continue
            elif source == "chips":
                genders = chip_genders
            else:
                genders = _head_values(head)
            if not genders:
                continue
            articles = {self._article_map.get(gender, "") for gender in genders}
            return articles.pop() if len(articles) == 1 else ""
        return ""

    def _aspect(self, word: Any, block: str, head: str) -> str | None:
        """The verb's aspect from the first source that answers, mirroring ``_gender``."""
        chips = frozenset(_CHIP_ASPECT[name] for name in _ASPECT_CHIP_RE.findall(block) if name in _CHIP_ASPECT)
        for source in self._sources:
            if source == "morph":
                found = _morph_values(str(getattr(word, "morph", "") or ""), feature="Aspect", table=_MORPH_ASPECT)
                if found and (not chips or found <= chips):
                    return _aspect_id(found)
            elif source == "chips":
                if len(chips) == 1:
                    return next(iter(chips))
            else:
                found = _head_values(head, table=_HEAD_ASPECT)
                if found:
                    return _aspect_id(found)
        return None

    def _render_aspect(self, word: Any) -> dict[str, str]:
        block = _first_block(str(getattr(word, "definition_html", "") or ""))
        head = _head_line(block)
        aspect = self._aspect(word, block, head)
        if aspect is None:
            return {}
        value = self._aspect_labels[aspect]
        partner_aspect, partner = _partner(head, aspect, self._partner_fold)
        if partner:
            value = f"{value} ({self._aspect_labels[partner_aspect]}: {partner})"
        return {"aspect_pair": value}

    def render(self, word: Any, *, config: AnkiMinerConfig) -> dict[str, str]:
        del config  # mapped field name is the switch
        pos = getattr(word, "pos", None)
        if pos in self._verb_pos and "aspect_pair" in self._fields:
            return self._render_aspect(word)
        if pos not in self._noun_pos:
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
            elif "noun_article" in self._fields and self._article_rule is None:
                article = self._shared_article(word, block, head)
                if article:
                    out["noun_article"] = article
        if "noun_plural" in self._fields:
            match = _PLURAL_RE.search(head)
            if match is not None:
                out["noun_plural"] = match.group(1)
        return out
