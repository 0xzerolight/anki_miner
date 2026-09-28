"""Dictionary key folding and the comparison fold for cased Latin languages.

Two DIFFERENT functions (R6/R7): ``CasefoldDictKeys.fold_term`` builds index
keys on both the import and the query side and never drops words;
``spaced_dedup_fold`` is the S3 comparison fold for known words, blacklists
and dedup, which additionally drops a leading article/infinitive marker so a
deck front ``to go`` meets the mined ``go``.
"""

from __future__ import annotations

import re
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


def wty_row_rank(tags: str, pos: str | None) -> int:
    """Where a wty row sorts among the rows sharing its term/reading priority.

    wty stores one row per part of speech and gives every row score 0 and
    sequence 0, so without this the import id orders them, and the casefolded
    key puts a word's place-name and surname rows beside it: ``airport`` opened
    on "A census-designated place", nl ``komen`` on "Comines (a city in
    Belgium)", de ``Essen`` on "to eat", he zakhar (a verb) on "man, male". The
    row's first tag names its part of speech. ``0`` for a row of the token's
    own part of speech (``ROW_TAGS_BY_UPOS``); ``2`` for a proper-name row
    unless the token is itself a proper noun; ``1`` for every other row,
    form-of rows included. Nothing is dropped, and storage keeps the index order
    inside each rank. The profiles whose dictionary is a wty dump and whose
    token POS is UPOS rank with this (``CasefoldDictKeys``, ``HebrewDictKeys``).
    """
    first = tags.split(" ", 1)[0]
    if first in NAME_ROW_TAGS and pos != "PROPN":
        return 2
    return 0 if first in ROW_TAGS_BY_UPOS.get(pos or "", frozenset()) else 1


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
        """The shared wty row rank, :func:`wty_row_rank`; ``content`` is not read."""
        return wty_row_rank(tags, pos)


def _strip_trailing_punctuation(text: str) -> str:
    end = len(text)
    while end and (text[end - 1].isspace() or unicodedata.category(text[end - 1]).startswith("P")):
        end -= 1
    return text[:end]


def _apostrophes(text: str) -> str:
    return text.replace("’", "'")


#: Bracketed groups closing a deck front: ``Hund (m)``, nl ``hond (de)``, ``freuen (sich)``.
_TRAILING_GROUPS = re.compile(r"(?:\s*\([^()]*\))+\s*$")

#: How a German plural note opens after the comma: ``-e``, ``¨-er``, ``¨er``.
_PLURAL_NOTE_MARKS = ("-", "¨")

#: A front ending in one of these is a sentence, whose comma no note follows: ``Das Auto, bitte.``
_SENTENCE_ENDS = (".", "!", "?", "…")


def spaced_dedup_fold(keys: CasefoldDictKeys, leading_words: frozenset[str] = frozenset()) -> Fold:
    """The S3 comparison fold: key fold, notes and trailing punctuation off, leading table words dropped.

    Vocab decks put a plural or gender note after the word, so first, while
    something remains, the bracketed groups closing the text are dropped
    (``Hund (m)``, nl ``hond (de)``), and a front of one to three tokens before
    its first comma is cut there (brackets closing the cut front go too) when
    the rest is a note: the front is article-led, the whole text is at most
    three tokens and ends no sentence (``das Haus, Häuser``, nl ``de hond,
    honden``, not ``Das Auto, bitte.``), or the rest is a table word (``Hund,
    der``) or opens with ``-``/``¨`` (``Haus, ¨-er``). Any other comma stays:
    ``Gut, danke.`` must not meet ``gut``, because the known-word scan covers
    the whole collection. The three-token bound is the one the article drop
    below uses: a comma kept for its sentence end loses the article there, so
    the next pass, with the end mark gone, finds no article-led front to cut.

    Then only a text of one to three whitespace tokens is touched. To a fixed point:
    a leading table word is dropped while more than one token remains (``to``
    alone stays ``to``), and a table entry ending in an apostrophe that is
    glued to the first token is stripped while some of the token remains (ca
    ``l'home`` → ``home``; ``'`` and ``’`` compare equal). Looping to a fixed
    point is what makes the fold idempotent — ``LanguageProfile.dedup_fold``
    must be, because folded keys are stored and folded again. The note cuts
    keep it: a folded text ends in no ``)``, and a comma it keeps never
    qualifies for the cut on the next pass.
    """
    # Folded like the text they are compared with: casefold maps el's final sigma (ένας → ένασ).
    whole_words = frozenset(_apostrophes(keys.fold_term(word)) for word in leading_words)
    elided = tuple(sorted((w for w in whole_words if w.endswith("'")), key=lambda w: (-len(w), w)))

    def article_led(head_tokens: list[str]) -> bool:
        """An article glued to the word (``l'home``) or standing before a word that is not one (``der Hund``)."""
        first = _apostrophes(head_tokens[0])
        if first in whole_words:
            return len(head_tokens) > 1 and _apostrophes(head_tokens[1]) not in whole_words
        return any(first.startswith(entry) and len(first) > len(entry) for entry in elided)

    def fold(text: str) -> str:
        folded = keys.fold_term(text)
        sentence = folded.rstrip().endswith(_SENTENCE_ENDS)
        folded = _strip_trailing_punctuation(_TRAILING_GROUPS.sub("", folded) or folded)
        head, comma, rest = folded.partition(",")
        head_tokens = head.split()
        rest = _apostrophes(rest.strip())
        if (
            comma
            and 1 <= len(head_tokens) <= 3
            and (
                (article_led(head_tokens) and len(folded.split()) <= 3 and not sentence)
                or rest in whole_words
                or rest.startswith(_PLURAL_NOTE_MARKS)
            )
        ):
            folded = _strip_trailing_punctuation(_TRAILING_GROUPS.sub("", head) or head)
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
