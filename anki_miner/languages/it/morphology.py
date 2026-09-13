"""Italian data for the shared spaCy substrate (spec A.1–A.3, §4.3 items 1, 3, 4).

Evidence is real ``it_core_news_sm`` 3.8.0 output over
``tests/fixtures/it/pos_corpus.jsonl`` and ``enclitic_forms.jsonl``, and the
wty-it-en 2026.08.29 rows in ``wty_row.json``.

``IT_EXCLUDED_SUBTYPES``: the fine tags the model puts under ADJ/ADV/NOUN/VERB
over the corpus are ``A NO B BN S V V_PC V_PC_PC``. ``BN`` is the ISDT negation
adverb (``non``, ``mica``): without it ``non`` is a card in every negative
line. Ordinals (``NO``: ``primo``) are vocabulary and stay.

``IT_ABBREVIATIONS``: every alphabetic key of spaCy's Italian tokenizer
exceptions (final dot dropped, casefolded; ``c.so s.n.c s.r.l`` carry no final
dot upstream), plus the titles spaCy leaves out (``sig sig.ra sig.na sigg dott
dott.ssa prof.ssa ing``) and ``es``/``p``/``p.es`` for ``p. es.``. No other
bare letter: ``a e i o`` are words that can precede a full stop. The same set
drives the sentence splitter and the tokenizer's rules surgery.

``IT_LEADING_WORDS``: what an Italian deck front carries and a mined lemma
never does — an article word (``il gatto``) or an elided article glued to the
noun (``l'acqua``, ``un'amica``).

``IT_ENCLITIC_CLUSTERS`` / ``it_relemmatize`` feed the shared enclitic rung,
which strips the token SURFACE (lookup only, miss-only). Infinitive + clitic
already fronts the infinitive (``keep_lemma_head``) and imperative/gerund +
clitic fronts the surface, which wty-it-en carries as a headword, so the rung
matters for dictionaries without those combined forms.

``italian_article`` is a spelling rule, because no dictionary row carries the
article: wty-it-en noun rows are tagged ``n`` + ``masc``/``fem`` only. The rule
is the one the wty ``lo`` article row states — ``lo`` before s+consonant, gn,
pn, ps, x, y, z, i+vowel, bd, cn, ct, dm, ft, mn, pt, tm and ts; ``l'`` before a
vowel — plus the silent ``h`` of loanwords (``l'hotel``).
"""

from __future__ import annotations

import logging
import unicodedata
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from anki_miner.languages._spaced.pos import UPOS_ALLOWED
from anki_miner.languages.token import LanguageToken

if TYPE_CHECKING:  # annotation-only
    from anki_miner.services.morphology import AttestLookup, FormLookup

logger = logging.getLogger(__name__)

#: The model package the tokenizer loads and the availability probe looks for.
IT_MODEL_PACKAGE = "it_core_news_sm"

IT_ALLOWED_POS: tuple[str, ...] = UPOS_ALLOWED
IT_EXCLUDED_SUBTYPES: tuple[str, ...] = ("BN",)

IT_ABBREVIATIONS: frozenset[str] = frozenset(
    {
        # spaCy it tokenizer exceptions
        "a.c", "al", "art", "artt", "att", "avv", "c.d", "civ", "cm", "cod", "col", "cost", "d.c", "distr",
        "ecc", "etc", "jr", "pag", "proc", "prof", "s.p.a", "sett", "ss", "st", "tel",
        "l'art", "all'art", "dall'art", "dell'art", "nell'art", "l’art", "all’art", "dall’art", "dell’art", "nell’art",
        "c.so", "s.n.c", "s.r.l",
        # titles spaCy leaves out, and both halves of "p. es."
        "sig", "sig.ra", "sig.na", "sigg", "dott", "dott.ssa", "prof.ssa", "ing", "es", "p", "p.es",
    }
)  # fmt: skip

IT_LEADING_WORDS: frozenset[str] = frozenset(
    {"il", "lo", "la", "i", "gli", "le", "un", "uno", "una", "l'", "l’", "un'", "un’"}
)

IT_ENCLITIC_CLUSTERS: tuple[str, ...] = ("mi", "ti", "si", "ci", "vi", "lo", "la", "li", "le", "gli", "ne") + tuple(
    head + tail for head in ("me", "te", "se", "ce", "ve", "glie") for tail in ("lo", "la", "li", "le", "ne")
)

#: Monosyllabic imperatives that double the clitic's consonant (``dammi``, ``dimmi``, ``vattene``).
_IMPERATIVE_INFINITIVES = MappingProxyType({"da": "dare", "di": "dire", "fa": "fare", "sta": "stare", "va": "andare"})
_VOWELS = frozenset("aeiouàèéìíòóùú")


def it_relemmatize(stem: str) -> list[str]:
    """Infinitive candidates for a stem left by the enclitic rung (``EncliticRung.relemmatize``)."""
    out: list[str] = []
    if stem.endswith("r"):  # an infinitive drops its -e before a clitic: lavar-si, por-lo
        out += [stem + "e", stem + "re"]
    if len(stem) >= 3 and stem[-1] not in _VOWELS and stem[:-1] in _IMPERATIVE_INFINITIVES:
        out.append(_IMPERATIVE_INFINITIVES[stem[:-1]])
    return list(dict.fromkeys(out))


_LO_ONSETS: tuple[str, ...] = ("gn", "pn", "ps", "bd", "cn", "ct", "dm", "ft", "mn", "pt", "tm", "ts")


def italian_article(gender: str, headword: str) -> str:
    """``GrammarTagHook`` ``article_rule``: ``il``/``lo``/``la``/``l'`` for a singular noun front; ``""`` omits."""
    word = unicodedata.normalize("NFC", headword).casefold()
    if not word or gender not in ("masc", "fem"):
        return ""
    first, second = word[0], word[1:2]
    semivowel = first == "i" and second in _VOWELS
    vowel = (first in _VOWELS and not semivowel) or (first == "h" and second in _VOWELS)
    if gender == "fem":
        return "l'" if vowel else "la"
    if vowel:
        return "l'"
    impure_s = first == "s" and second.isalpha() and second not in _VOWELS
    if semivowel or impure_s or first in "xyz" or word.startswith(_LO_ONSETS):
        return "lo"
    return "il"


def keep_lemma_head(tokens: list[LanguageToken]) -> list[LanguageToken]:
    """Tokenizer post-pass: a multi-word model lemma keeps its first word.

    ``it_core_news_sm`` lemmatises a verb with attached clitics the UD way —
    ``lavarsi`` -> ``lavare si``, ``andarmene`` -> ``andare me ne`` — and the
    verb comes first. A card front never carries a space.
    """
    for token in tokens:
        words = token.feature.lemma.split()
        if len(words) > 1:
            token.feature.lemma = words[0]
    return tokens


#: Classes a card can front (the shared UPOS gate); other tokens keep the model's lemma untouched.
_CONTENT_POS = frozenset(UPOS_ALLOWED)


class AttestedLemmaPass:
    """A ``token_post_pass``: an unattested lemma yields to an attested lowercased surface.

    ``it_core_news_sm``'s edit-tree lemmatizer fabricates lemmas for some
    inflected forms (``fammi`` -> ``fammare``, ``passo`` -> ``pasdere``). The
    lookup ladder still finds the definition through the surface, but the card
    front — and so known-word and duplicate matching — would keep the invented
    word. One attestation call per line over the distinct candidates; a lemma
    the dictionary knows is never touched, and with no offline dictionary wired
    (``attest is None``) nothing changes. The third argument (R36's form lookup)
    is unused. Accepted trade-off: a verb the dictionary lists only in its
    ``-rsi`` form (``accorgersi``) keeps the inflected surface (``accorto``).
    """

    def __call__(self, tokens: list[Any], attest: AttestLookup | None, forms: FormLookup | None) -> list[Any]:
        del forms
        if attest is None:
            return tokens
        suspects = [
            token
            for token in tokens
            if token.feature.pos1 in _CONTENT_POS
            and token.feature.lemma
            and token.feature.lemma != token.surface.lower()
        ]
        if not suspects:
            return tokens
        candidates = [word for token in suspects for word in (token.feature.lemma, token.surface.lower())]
        attested = attest(list(dict.fromkeys(candidates)))
        for token in suspects:
            surface = token.surface.lower()
            if token.feature.lemma not in attested and surface in attested:
                logger.debug("Lemma %r not attested; fronting the surface %r", token.feature.lemma, surface)
                token.feature.lemma = surface
        return tokens
