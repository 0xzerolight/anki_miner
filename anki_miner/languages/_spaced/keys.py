"""Dictionary key folding and the comparison fold for cased Latin languages.

Two DIFFERENT functions (R6/R7): ``CasefoldDictKeys.fold_term`` builds index
keys on both the import and the query side and never drops words;
``spaced_dedup_fold`` is the S3 comparison fold for known words, blacklists
and dedup, which additionally drops a leading article/infinitive marker so a
deck front ``to go`` meets the mined ``go``.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Callable, Mapping
from types import MappingProxyType

Fold = Callable[[str], str]

#: First tags of the wty rows that define a proper noun: a place, a surname, a given name.
NAME_ROW_TAGS = frozenset({"name", "prop-n", "surn"})

#: A token's UPOS -> the first tags of the wty rows stating that part of speech, which then lead
#: the definition. Measured class by class on ordinary subtitle lines (en de nl sv fr it es pt ru
#: pl ro tr), cards whose lead row got better/worse: VERB 43/0, NOUN 34/5, ADV 52/7 (the losses
#: are mostly tagger slips: de "aber" as ADV loses "but", en "cost" as NOUN loses the verb), ADJ
#: 1/4 (fr "neuf heures" opened on "brand new"), so ADJ is left out. A NOUN takes an ``intj`` row
#: too: spaCy tags thanks-words NOUN, and fr "merci" would open on "mercy".
ROW_TAGS_BY_UPOS: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "NOUN": frozenset({"n", "intj"}),
        "VERB": frozenset({"v"}),
        "ADV": frozenset({"adv"}),
    }
)


class CasefoldDictKeys:
    """DictKeyFolding: NFC + optional extra fold + casefold; Rule A then Rule A′.

    Symmetric by construction — the importer and the provider are handed the
    same profile's instance. ``extra_fold`` runs before ``casefold`` (ro's
    cedilla→comma-below map is the case it exists for). No Rule B: there is no
    kana script to reconcile.
    """

    def __init__(self, extra_fold: Fold | None = None) -> None:
        self._extra_fold = extra_fold

    def fold_term(self, s: str) -> str:
        text = unicodedata.normalize("NFC", s)
        if self._extra_fold is not None:
            text = self._extra_fold(text)
        return text.casefold()

    def fold_reading(self, s: str | None) -> str | None:
        return None if s is None else unicodedata.normalize("NFC", s)

    def homograph_keep_mask(self, word: str, rows: list[tuple[str, str]], lemma: str | None = None) -> list[bool]:
        """Rule A (term == word), else Rule A′ (term == lemma), each with the same-content carve-out."""
        for key in (word, lemma):
            if not key:
                continue
            exact = [term == key for term, _ in rows]
            if any(exact):
                contents = {content for (_, content), hit in zip(rows, exact, strict=True) if hit}
                return [hit or content in contents for (_, content), hit in zip(rows, exact, strict=True)]
        return [True] * len(rows)

    def sense_rank(self, content: str, tags: str, pos: str | None) -> int:
        """Where a wty row sorts among the rows sharing its term/reading priority.

        wty stores one row per part of speech and gives every row score 0 and
        sequence 0, so without this the import id orders them, and the casefolded
        key puts a word's place-name and surname rows beside it: ``airport`` opened
        on "A census-designated place", nl ``komen`` on "Comines (a city in
        Belgium)", de ``Essen`` on "to eat". The row's first tag names its part of
        speech. ``0`` for a row of the token's own part of speech
        (``ROW_TAGS_BY_UPOS``); ``2`` for a proper-name row unless the token is
        itself a proper noun; ``1`` for every other row, form-of rows included.
        Nothing is dropped, and storage keeps the index order inside each rank.
        """
        first = tags.split(" ", 1)[0]
        if first in NAME_ROW_TAGS and pos != "PROPN":
            return 2
        return 0 if first in ROW_TAGS_BY_UPOS.get(pos or "", frozenset()) else 1


def _strip_trailing_punctuation(text: str) -> str:
    end = len(text)
    while end and (text[end - 1].isspace() or unicodedata.category(text[end - 1]).startswith("P")):
        end -= 1
    return text[:end]


def _apostrophes(text: str) -> str:
    return text.replace("’", "'")


def spaced_dedup_fold(keys: CasefoldDictKeys, leading_words: frozenset[str] = frozenset()) -> Fold:
    """The S3 comparison fold: key fold, trailing punctuation off, leading table words dropped.

    Only a text of one to three whitespace tokens is touched. To a fixed point:
    a leading table word is dropped while more than one token remains (``to``
    alone stays ``to``), and a table entry ending in an apostrophe that is
    glued to the first token is stripped while some of the token remains (ca
    ``l'home`` → ``home``; ``'`` and ``’`` compare equal). Looping to a fixed
    point is what makes the fold idempotent — ``LanguageProfile.dedup_fold``
    must be, because folded keys are stored and folded again.
    """
    # Folded like the text they are compared with: casefold maps el's final sigma (ένας → ένασ).
    whole_words = frozenset(_apostrophes(keys.fold_term(word)) for word in leading_words)
    elided = tuple(sorted((w for w in whole_words if w.endswith("'")), key=lambda w: (-len(w), w)))

    def fold(text: str) -> str:
        folded = _strip_trailing_punctuation(keys.fold_term(text))
        tokens = folded.split()
        if not 1 <= len(tokens) <= 3:
            return folded.strip()
        changed = True
        while changed:
            changed = False
            if len(tokens) > 1 and _apostrophes(tokens[0]) in whole_words:
                tokens = tokens[1:]
                changed = True
                continue
            head = _apostrophes(tokens[0])
            for entry in elided:
                if head.startswith(entry) and len(head) > len(entry):
                    tokens[0] = tokens[0][len(entry) :]
                    changed = True
                    break
        return " ".join(tokens)

    return fold


#: An attested-readings probe (``DefinitionService.offline_term_readings``): terms -> readings per term.
ReadingProbe = Callable[[list[str]], dict[str, list[str]]]


def folded_reading_lookup(lookup: ReadingProbe, fold: Fold) -> ReadingProbe:
    """Ask *lookup* with folded keys and answer under the caller's own spellings (spec S24, ru plan D2).

    ``IndexedDictProvider.terms_readings`` NFC-matches a query against terms the importer stored through
    the profile's ``fold_term``; a card front the fold changes (a Russian yo spelling is stored with е)
    would miss its reading. Each distinct key is asked once; spellings sharing a key share its answer.
    """

    def probe(terms: list[str]) -> dict[str, list[str]]:
        spellings: dict[str, list[str]] = {}
        for term in dict.fromkeys(terms):
            spellings.setdefault(fold(term), []).append(term)
        found = lookup(list(spellings))
        return {term: found[key] for key, group in spellings.items() if key in found for term in group}

    return probe
