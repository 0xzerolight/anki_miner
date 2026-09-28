"""Turkish folds, normaliser, POS map, SDH default and wty row passes (spec §4.4, B.1, B.2) - engine-free.

``analyzer.py`` (zeyrek), ``tokenizer.py`` and ``parser.py`` share these; nothing here imports zeyrek, so the
profile builds on a machine without the Turkish pack.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from anki_miner.languages._spaced.form_of import WTY_TAG_TO_UPOS, lemma_row_targets
from anki_miner.languages._spaced.keys import NAME_ROW_TAGS, CasefoldDictKeys, spaced_dedup_fold
from anki_miner.languages._spaced.pos import UPOS_ALLOWED
from anki_miner.languages._spaced.script import (
    BRACKETS_PATTERN,
    DIALOGUE_DASH_PATTERN,
    MUSIC_PATTERN,
    PARENS_PATTERN,
    nfc_normalize,
)

if TYPE_CHECKING:  # annotation-only: services must not load at profile build
    from anki_miner.services.morphology import AttestLookup, FormLookup

TR_ALLOWED_POS: tuple[str, ...] = UPOS_ALLOWED
TR_EXCLUDED_SUBTYPES: tuple[str, ...] = ()


def tr_upper_i(text: str) -> str:
    """Turkish's capital i pair, applied before a locale-blind casefold: ``İ`` → ``i``, ``I`` → ``ı``.

    ``str.casefold`` maps ``İ`` to ``i`` + U+0307 and ``I`` to ``i``: both wrong for Turkish (``IŞIK`` is ``ışık``).
    """
    return text.replace("İ", "i").replace("I", "ı")


_CIRCUMFLEXES = "âîûÂÎÛ"
_CIRCUMFLEX_FOLD = str.maketrans(_CIRCUMFLEXES, "aiuAIU")


def tr_key_fold(text: str) -> str:
    """The key fold before casefold: the capital i pair, then the circumflex off (``pekâlâ`` → ``pekala``).

    Subtitles mostly write ``pekala``, ``hikaye``, ``kağıt``; wty-tr-en keys those headwords with the circumflex and
    has the bare spelling at most as a ``non-lemma`` pointer. The i pair runs first, so the ``I`` that ``Î`` becomes
    casefolds to ``i``, not ``ı``. Homographs share a key (``kar``/``kâr``, ``hala``/``hâlâ``): the plain spelling's
    rows were imported first, so its sense still leads.
    """
    return tr_upper_i(text).translate(_CIRCUMFLEX_FOLD)


#: B.1 key fold: NFC, the capital i pair, the circumflex, casefold. One instance serves the importer and the provider
#: (S4).
TR_KEYS = CasefoldDictKeys(extra_fold=tr_key_fold)
#: S3 comparison fold: the key fold, trailing punctuation off; Turkish has no article to drop.
TR_DEDUP_FOLD = spaced_dedup_fold(TR_KEYS)


def tr_casefold(text: str) -> str:
    """B.1 ``tr_casefold``: NFC, the capital i pair, casefold - the fold the tokenizer applies to every non-PROPN lemma.

    Unlike ``TR_KEYS`` it keeps the circumflex, so a front follows the subtitle's spelling (``kâğıdı`` → ``kâğıt``).
    """
    return tr_upper_i(nfc_normalize(text)).casefold()


_NORMALIZE_MAP = str.maketrans({"\u00a0": " ", "\u00ad": None})


def tr_normalize(text: str) -> str:
    """S5 for Turkish: NFC; NBSP → space; soft hyphens removed. Apostrophes stay: the tokenizer reads all three."""
    return nfc_normalize(text).translate(_NORMALIZE_MAP)


@dataclass(frozen=True)
class TrAnalysis:
    """One zeyrek reading of a word: card-front lemma, UPOS, and zeyrek's secondary POS (``Pers``, ``Time``...).

    ``imperative`` and ``aorist`` say that a reading of this verb carries zeyrek's ``Imp`` or ``Aor`` morpheme
    (``at`` is ``atmak``'s imperative, ``bekler`` ``beklemek``'s aorist; the aorist participle ``AorPart`` does not
    count). The tagger reads them from the clause: a command before ``!``, a request before ``mI``.
    """

    lemma: str
    pos1: str
    pos2: str = ""
    imperative: bool = False
    aorist: bool = False


#: B.1's zeyrek primary POS → UPOS map; anything else is ``X``.
ZEYREK_TO_UPOS: Mapping[str, str] = MappingProxyType(
    {
        "Noun": "NOUN",
        "Verb": "VERB",
        "Adj": "ADJ",
        "Adv": "ADV",
        "Pron": "PRON",
        "Postp": "ADP",
        "Conj": "CCONJ",
        "Det": "DET",
        "Num": "NUM",
        "Interj": "INTJ",
        "Ques": "PART",
        "Dup": "X",
        "Punc": "PUNCT",
    }
)


def upos(primary: str, secondary: str) -> str:
    """UPOS for a zeyrek reading: a proper noun is PROPN, an abbreviation X (never vocabulary), else the map."""
    if secondary == "Prop":
        return "PROPN"
    if secondary == "Abbrv":
        return "X"
    return ZEYREK_TO_UPOS.get(primary, "X")


def front_spelling(lemma: str, surface: str) -> str:
    """zeyrek keys some lemmas in the older circumflex spelling (``ilâç``); the front follows the subtitle."""
    if any(char in surface for char in _CIRCUMFLEXES):
        return lemma
    return lemma.translate(_CIRCUMFLEX_FOLD)


def tr_row_targets(content: str, tags: str) -> list[str] | None:
    """How the form-of pass reads a wty-tr-en row: a name row is no headword of a common word, and names no lemma.

    ``evin`` holds a given name and a form row naming ``ev``. Read as a headword, the name kept ``evine``'s front
    ``evin``, whose card offered only the pointer to ``ev`` and the name.
    """
    if tags.split(" ", 1)[0] in NAME_ROW_TAGS:
        return []
    return lemma_row_targets(content, tags)


#: The picks the label pass may relabel. zeyrek's noun reading carries the zero morpheme ``A3sg``, so a same-lemma
#: Adj or Adv reading outranks it on morpheme count (``çocuk``, ``dakika``). A noun pick is left alone: it wins only
#: through a case or possessive ending, which makes it a noun whatever the bare word is (``zorunda``).
_RELABELLED_POS = frozenset({"ADJ", "ADV"})
#: The labels it may give. All three are mined, so a relabel never changes what is mined.
_LABEL_POS = frozenset({"NOUN", "ADJ", "ADV"})
#: The cache exists so a repeated word in a long corpus (count_lemmas) is read once per parser.
_CACHE_MAX = 4096


def _label_options(token: Any) -> list[str]:
    """The mined classes of the token's lemma readings, in the tokenizer's rank order (``feature.lemma_pos``)."""
    return [pos for pos in getattr(token.feature, "lemma_pos", ()) if pos in _LABEL_POS]


class HeadwordPosPass:
    """``token_post_pass``: a pick's part of speech from its lemma's own wty-tr-en headword rows.

    The Pos field called ``çocuk`` an adjective and ``dakika`` an adverb, which wty files only as nouns. When no
    headword row of the lemma (form and name rows aside) opens with an Adj or Adv pick's class, the next reading of
    the same lemma whose class one does open with gives the label, in zeyrek's order: wty's row order is its page
    order, and ``artık`` opens on the noun "remnant" before the adverb "anymore". A pick the rows support stays
    (``güzel`` has adj and n rows: ADJ). Only ``pos1`` moves: the front, and what is mined, stay as the tokenizer
    built them.
    """

    def __init__(self) -> None:
        self._cache: dict[str, frozenset[str]] = {}

    def __call__(self, tokens: list[Any], attest: AttestLookup | None, forms: FormLookup | None) -> list[Any]:
        del attest  # existence is not enough: this pass needs the rows' tags
        if forms is None:
            return tokens
        ambiguous = [t for t in tokens if t.feature.pos1 in _RELABELLED_POS and len(_label_options(t)) > 1]
        classes = self._classes(forms, [token.feature.lemma for token in ambiguous])
        for token in ambiguous:
            heads = classes[token.feature.lemma]
            if token.feature.pos1 not in heads:
                token.feature.pos1 = next((pos for pos in _label_options(token) if pos in heads), token.feature.pos1)
        return tokens

    def _classes(self, forms: FormLookup, lemmas: list[str]) -> dict[str, frozenset[str]]:
        """Each lemma's headword classes: the cached ones, and one batched read for the rest."""
        found = {lemma: self._cache[lemma] for lemma in lemmas if lemma in self._cache}
        wanted = [lemma for lemma in dict.fromkeys(lemmas) if lemma not in found]
        if wanted:
            read = forms(wanted)
            if len(self._cache) + len(wanted) > _CACHE_MAX:
                self._cache.clear()
            for lemma in wanted:
                heads = [tags for content, tags in read.get(lemma, []) if tr_row_targets(content, tags) is None]
                found[lemma] = self._cache[lemma] = frozenset(
                    WTY_TAG_TO_UPOS.get(tags.split(" ", 1)[0], "") for tags in heads
                )
        return found


#: The shared speaker label plus the three Turkish capitals outside Latin-1 (``Ğ İ Ş``): ``AYŞE:`` escaped it.
TR_SPEAKER_PATTERN = r"^[A-ZÀ-ÖØ-ÞĞİŞ][A-ZÀ-ÖØ-ÞĞİŞ0-9 .'-]*[A-ZÀ-ÖØ-ÞĞİŞ]:\s*"
TR_SUBTITLE_REGEX = "|".join(
    (BRACKETS_PATTERN, PARENS_PATTERN, MUSIC_PATTERN, TR_SPEAKER_PATTERN, DIALOGUE_DASH_PATTERN)
)
