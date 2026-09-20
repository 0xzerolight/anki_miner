"""Indonesian folds, lookup ladder and POS tables (spec C.5).

No engine: Indonesian inflects only by affixation, so the card front is the surface as met,
casefolded and stripped of combining marks (``bukunya``, ``buku-buku``, ``nggak``), and the
lookup-miss ladder is the rule table in :mod:`anki_miner.languages.id.rules`, validated by the
installed dictionary (first hit wins).
"""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping
from types import MappingProxyType

from anki_miner.languages._spaced.keys import CasefoldDictKeys
from anki_miner.languages.id.colloquial import ID_COLLOQUIAL, ID_COLLOQUIAL_CORE
from anki_miner.languages.id.rules import deinflection_candidates
from anki_miner.languages.id.stopwords import ID_STOPWORDS

#: The one class a learner mines; function words carry ``pos2="stopword"`` and are excluded by subtype.
ID_ALLOWED_POS: tuple[str, ...] = ("WORD",)
ID_EXCLUDED_SUBTYPES: tuple[str, ...] = ("stopword",)
#: ``PosDefaults.labels`` maps ``pos1`` only (``_spaced/pos.py`` UPOS_LABELS): ``stopword`` is a pos2 subtype.
ID_POS_LABELS: Mapping[str, str] = MappingProxyType(
    {
        "WORD": "Word",
        "PROPN": "Proper noun",
        "NUM": "Number",
        "PUNCT": "Punctuation",
        "LATIN_OTHER": "Foreign word",
        "X": "Abbreviation",
    }
)

#: English words common in Indonesian subtitles and vlogs (C.5): tagged ``LATIN_OTHER``, never mined.
LATIN_OTHER: frozenset[str] = frozenset({"okay", "guys", "sorry", "yes", "no", "thanks"})

#: Casefolded abbreviations without their final dot: not sentence ends (reading tab), not mined (tokenizer ``X``).
ID_ABBREVIATIONS: frozenset[str] = frozenset(
    {"bpk", "dkk", "dll", "dr", "drs", "dsb", "dst", "hlm", "ir", "jl", "no", "prof", "sdr", "tsb", "yth"}
)


def id_fold(text: str) -> str:
    """NFC -> casefold -> NFD -> drop combining marks -> NFC (C.5): ``Mengérti`` -> ``mengerti``. Idempotent."""
    decomposed = unicodedata.normalize("NFD", unicodedata.normalize("NFC", text).casefold())
    return unicodedata.normalize("NFC", "".join(c for c in decomposed if unicodedata.category(c) != "Mn"))


def is_stopword(lemma: str) -> bool:
    """A folded word in the stopword tier, directly or through the curated core's formal spelling (``yg`` -> ``yang``).

    Only :data:`ID_COLLOQUIAL_CORE` expands the tier: IndoCollex's own formal side is crowd-derived, and
    reading the whole table through would build a second, unreviewed stopword list (plan D6).
    """
    return lemma in ID_STOPWORDS or ID_COLLOQUIAL_CORE.get(lemma, "") in ID_STOPWORDS


class IndonesianDictKeys(CasefoldDictKeys):
    """DictKeyFolding: ``fold_term`` is :func:`id_fold` on both the import and the query side.

    Wiktionary headwords are unaccented; ``é`` appears only as a pronunciation aid, so a subtitle's
    ``mengérti`` must meet the ``mengerti`` row. Readings pass through (NFC). The homograph mask is
    the inherited Rule A / Rule A′: the Indonesian lemma is the folded surface, so both rules key on
    the same text.
    """

    def fold_term(self, s: str) -> str:
        return id_fold(s)


class IndonesianLookupStrategy:
    """LookupStrategy: :func:`rules.deinflection_candidates` over the folded front, conditions 0.

    ``orth_base`` (the surface on the mining path) adds nothing: the provider folds every query
    with :func:`id_fold`, so the surface and the front are the same key.
    """

    def candidates(self, word: str, orth_base: str, ctype: str | None) -> list[tuple[str, int]]:
        del orth_base, ctype
        folded = id_fold(word)
        return [(text, 0) for text in deinflection_candidates(folded, ID_COLLOQUIAL.get) if text != word]
