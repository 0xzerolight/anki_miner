"""Arabic analysis pick, mined form, lookup ladder, reading and word-audio ladder (spec C.1).

The analyzer (``_calima``) returns every analysis the database licenses; ``pick_analysis`` chooses one:
the highest ``pos_lex_logprob`` among ``lex``/``spvar`` analyses (what CAMeL's MLE disambiguator does for
a word it has no statistics for), ties to a ``lex`` source and then to a clitic-free reading. One
exception: the database lexicalises 173 tanween adverbs and interjections (``\u0634\u064f\u0643\u0652\u0631\u0627\u064b``, ``\u062c\u0650\u062f\u0651\u0627\u064b``,
``\u0623\u064e\u0628\u064e\u062f\u0627\u064b``); such a lexeme wins when the surface carries fathatan or when the plain winner is a
case-bearing noun/verb/adjective reading (the raw argmax sends ``\u0634\u0643\u0631\u0627\u064b`` to the verb ``\u0634\u064e\u0643\u064e\u0631``). A
function-word winner on an unmarked surface keeps its reading (``\u0625\u0630\u0627`` "if", not ``\u0625\u0650\u0630\u0627\u064b``). No
dictionary probe is involved: wty-ar-en has non-lemma rows for plain accusatives (``\u0643\u062a\u0627\u0628\u0627``), so an
existence test would front ``\u0643\u062a\u0627\u0628\u0627\u064b`` as ``\u0643\u062a\u0627\u0628\u0627``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from anki_miner.languages.ar._calima.charsets import dediac_ar

AR_UNKNOWN_POS = "unknown"
AR_CLITIC_SUBTYPE = "clitic"
FATHATAN = "\N{ARABIC FATHATAN}"
#: The three tanween marks a surface can carry; the database lexicalises adverbs with fathatan only.
AR_TANWEEN = (FATHATAN, "\N{ARABIC DAMMATAN}", "\N{ARABIC KASRATAN}")
_ALEF = "\N{ARABIC LETTER ALEF}"
_ALEF_WASLA = "\N{ARABIC LETTER ALEF WASLA}"
_CONJUNCTIONS = ("\N{ARABIC LETTER WAW}", "\N{ARABIC LETTER FEH}")  # wa, fa
_HAMZA_SEATS = str.maketrans({"\u0625": "\u0627", "\u0623": "\u0627", "\u0622": "\u0627", "\u0671": "\u0627"})


def has_tanween(text: str) -> bool:
    """Does this surface carry a written tanween mark? (all three, not fathatan alone)."""
    return any(mark in text for mark in AR_TANWEEN)


#: CAMeL POS tags mined by default (spec C.1). Proper nouns are one POS-editor tick away.
AR_ALLOWED_POS: tuple[str, ...] = ("noun", "adj", "adj_comp", "verb", "adv", "noun_quant", "interj")
#: ``clitic`` (pos2 of a clitic-bearing token) is available but off: a clitic chain mines its lemma.
AR_EXCLUDED_SUBTYPES: tuple[str, ...] = ()
AR_POS_LABELS: Mapping[str, str] = MappingProxyType(
    {
        "noun": "Noun",
        "noun_prop": "Proper noun",
        "noun_num": "Number word",
        "noun_quant": "Quantifier",
        "adj": "Adjective",
        "adj_comp": "Comparative adjective",
        "adj_num": "Ordinal adjective",
        "adv": "Adverb",
        "adv_interrog": "Interrogative adverb",
        "adv_rel": "Relative adverb",
        "pron": "Pronoun",
        "pron_dem": "Demonstrative pronoun",
        "pron_exclam": "Exclamative pronoun",
        "pron_interrog": "Interrogative pronoun",
        "pron_rel": "Relative pronoun",
        "verb": "Verb",
        "verb_pseudo": "Pseudo-verb",
        "part": "Particle",
        "part_dem": "Demonstrative particle",
        "part_det": "Determiner",
        "part_focus": "Focus particle",
        "part_fut": "Future particle",
        "part_interrog": "Interrogative particle",
        "part_neg": "Negative particle",
        "part_restrict": "Restrictive particle",
        "part_verb": "Verbal particle",
        "part_voc": "Vocative particle",
        "prep": "Preposition",
        "conj": "Conjunction",
        "conj_sub": "Subordinating conjunction",
        "interj": "Interjection",
        "abbrev": "Abbreviation",
        "punc": "Punctuation",
        "digit": "Digit",
        "latin": "Latin",
        "foreign": "Foreign word",
        AR_UNKNOWN_POS: "Unknown (unanalysed: dialect, typos)",
        AR_CLITIC_SUBTYPE: "Clitic-bearing form",
    }
)

#: Dialect words the MSA database misreads into a mineable wrong lemma (probe 2026-09-19: mish ->
#: mashsh "maceration", biddi -> budd, illi -> layy, kamaan -> "violin" ...). They are ``unknown``
#: before analysis, and an unknown word from this list keeps its whole spelling as its front.
AR_DIALECT_WORDS: frozenset[str] = frozenset(
    (
        "\u0645\u0634",
        "\u0628\u062f\u064a",
        "\u0628\u062f\u0643",
        "\u0643\u062f\u0647",
        "\u0627\u0644\u0644\u064a",
        "\u0647\u0644\u0623",
        "\u0639\u0634\u0627\u0646",
        "\u0628\u0631\u0636\u0647",
        "\u0628\u0633",
        "\u0643\u0645\u0627\u0646",
        "\u0627\u0648\u064a",
        "\u0634\u064a",
        "\u0644\u064a\u0647",
        "\u0627\u0645\u062a\u0649",
        "\u0627\u0646\u062a\u064a",
        "\u0627\u062d\u0646\u0627",
        "\u0647\u0648\u0646",
    )
)

_ANALYSED_SOURCES = frozenset({"lex", "spvar"})
_CASE_BEARING_POS = frozenset({"noun", "verb", "adj"})


def _rank(analysis: Mapping[str, Any]) -> tuple[float, bool, bool]:
    return (
        float(analysis.get("pos_lex_logprob", -99.0)),
        analysis.get("source") == "lex",
        "+" not in str(analysis.get("d3tok", "")),
    )


def _spells_the_token(lex: str, key: str) -> bool:
    """Does this lexeme spell the token itself, allowing a conjunction and a dropped hamza seat?

    The gate on the unmarked tanween rung. Without it the rung overrides correct verb readings whose
    consonants happen to match a tanween adverb: ``\u0623\u0642\u0641`` (I stand) fronted as ``\u0642\u0641\u0627`` and ``\u0623\u0628\u062f\u0623``
    (I begin) as ``\u0623\u0628\u062f\u0627``. The comparison is against the token's folded key and never against the
    analysis's own ``d3tok`` base - ``\u0623\u0628\u062f\u0623``'s spvar analysis bases on ``\u0623\u064e\u0628\u064e\u062f\u0627\u064b``, which a base test
    would accept. Flattening runs on the lexeme only, so a surface that drops a hamza (``\u0627\u0628\u062f\u0627``, which
    the database serves as ``spvar``) still reaches its adverb while one that adds a hamza the lexeme
    has not got (``\u0623\u0628\u062f\u0623``) does not.
    """
    lemma = dediac_ar(lex)
    bare = key[1:] if key[:1] in _CONJUNCTIONS and len(key) > 1 else key
    return lemma in (key, bare) or lemma.translate(_HAMZA_SEATS) in (key, bare)


def pick_analysis(analyses: list[dict[str, Any]], key: str, *, surface_has_tanween: bool) -> dict[str, Any] | None:
    """The analysis a token takes, or None when the database licenses no lex/spvar reading.

    ``key`` is the token's folded spelling (what the analyzer was asked about).
    """
    analysed = [analysis for analysis in analyses if analysis.get("source") in _ANALYSED_SOURCES]
    if not analysed:
        return None
    best = max(analysed, key=_rank)
    lexicalised = [
        analysis
        for analysis in analysed
        if FATHATAN in str(analysis.get("lex", "")) and _spells_the_token(str(analysis.get("lex", "")), key)
    ]
    if lexicalised and (surface_has_tanween or best.get("pos") in _CASE_BEARING_POS):
        return max(lexicalised, key=_rank)
    return best


@dataclass(frozen=True)
class AnalysisSummary:
    """What a token keeps of its analysis (the tokenizer caches these per key, never whole analyses)."""

    pos: str
    clitic: bool
    lemma: str  # unvocalised lex: the card front
    orth_base: str  # unvocalised d3tok base: the surface minus its clitics
    reading: str  # vocalised lex: expression_reading and the TTS text
    morph: str  # "Root=\u0643.\u062a.\u0628|Segmentation=\u0648\u064e+ \u0633\u064e+ \u064a\u064e\u0643\u0652\u062a\u064f\u0628\u064f\u0648\u0646\u064e +\u0647\u0627"


def summarise(analysis: Mapping[str, Any]) -> AnalysisSummary:
    """Reduce one analysis to the token fields (hamzat wasl reads as a plain alef, like wty headwords)."""
    reading = str(analysis.get("lex", "")).replace(_ALEF_WASLA, _ALEF)
    d3tok = str(analysis.get("d3tok", ""))
    base = next((part for part in d3tok.split("_") if part and not part.startswith("+") and not part.endswith("+")), "")
    clitic = "+" in d3tok
    root = str(analysis.get("root", "") or "")
    segmentation = d3tok.replace("_", " ") if clitic else ""
    morph = "|".join(f"{name}={value}" for name, value in (("Root", root), ("Segmentation", segmentation)) if value)
    return AnalysisSummary(
        pos=str(analysis.get("pos", "")),
        clitic=clitic,
        lemma=dediac_ar(reading),
        orth_base=dediac_ar(base).replace(_ALEF_WASLA, _ALEF),
        reading=reading,
        morph=morph,
    )


_PREPOSITIONS = ("\u0628", "\u0643", "\u0644", "\u0633")  # bi, ka, li, sa
_ARTICLE = "\u0627\u0644"  # al
_LIL = "\u0644\u0644"  # li + al


def strip_proclitics(text: str) -> str:
    """An unanalysed word minus its proclitics: conjunction, then ``\u0644\u0644`` or one preposition, then ``\u0627\u0644``.

    Each strip leaves at least three letters, so a short stem is never cut (``\u0628\u062f\u064a`` stays).
    """
    rest = text
    if rest[:1] in _CONJUNCTIONS and len(rest) - 1 >= 3:
        rest = rest[1:]
    if rest.startswith(_LIL) and len(rest) - 2 >= 3:
        return rest[2:]
    if rest[:1] in _PREPOSITIONS and len(rest) - 1 >= 3:
        rest = rest[1:]
    if rest.startswith(_ARTICLE) and len(rest) - 2 >= 3:
        rest = rest[2:]
    return rest


def dialect_word(text: str) -> str:
    """The stop-list word this spelling holds, or ``""`` - the token's front when it holds one.

    The list is bare forms, but running text prefixes them with a conjunction, and the MSA database
    reads every one of those into a mineable wrong lemma (``\u0648\u0645\u0634`` -> the verb ``\u0645\u0634``, ``\u0648\u0627\u0644\u0644\u064a`` -> the
    noun ``\u0644\u064a``). ``strip_proclitics`` cannot do this alone: its three-letter floor leaves ``\u0648\u0645\u0634``
    whole. The word returned is the front, so ``\u0648\u0645\u0634`` mines as ``\u0645\u0634`` and never as a cut ``\u0645\u0627\u0646``.
    """
    if text in AR_DIALECT_WORDS:
        return text
    if text[:1] in _CONJUNCTIONS and text[1:] in AR_DIALECT_WORDS:
        return text[1:]
    stripped = strip_proclitics(text)
    return stripped if stripped in AR_DIALECT_WORDS else ""


class ArabicMinedForm:
    """The unvocalised lemma for every POS; an unanalysed word keys on its proclitic-stripped spelling."""

    def mined_form(
        self, pos: str | None, orth_base: str, lemma: str, surface: str, pronunciation: str | None = None
    ) -> str:
        del orth_base, pronunciation
        front = lemma or surface
        if pos == AR_UNKNOWN_POS and front not in AR_DIALECT_WORDS:
            return strip_proclitics(front)
        return front

    def expression_tracks_surface(self, word: Any) -> bool:
        """A lemma front never follows an i+1 swap's new surface (S12)."""
        return False

    def lookup_alternate(self, word: Any) -> str:
        """The ladder's ``orth_base``: the clitic-free d3tok base, or ``""`` for an unanalysed word.

        ``""`` is what switches the ladder's clitic strips on (``ArabicLookupStrategy``): stripping
        a letter off an analysed lemma (``\u0633\u0627\u0641\u0631``, ``\u0628\u0643\u0649``) would invent a word.
        """
        if getattr(word, "pos", None) == AR_UNKNOWN_POS:
            return ""
        return str(getattr(word, "orth_base", "") or "")


_FINAL_SWAPS = {"\u0649": "\u064a", "\u064a": "\u0649", "\u0647": "\u0629"}  # alef maqsura <-> ya; ha -> ta marbuta
_PROCLITIC_STRIPS = (
    "\u0648",
    "\u0641",
    "\u0628",
    "\u0643",
    "\u0644",
    "\u0627\u0644",
    "\u0628\u0627\u0644",
    "\u0643\u0627\u0644",
    "\u0644\u0644",
    "\u0648\u0627\u0644",
    "\u0641\u0627\u0644",
    "\u0633",
)
_ENCLITIC_STRIPS = (
    "\u0647\u0645\u0627",
    "\u0643\u0645\u0627",
    "\u0647\u0645",
    "\u0647\u0646",
    "\u0647\u0627",
    "\u0643\u0645",
    "\u0643\u0646",
    "\u0646\u0627",
    "\u0647",
    "\u0643",
    "\u064a",
)
MAX_LOOKUP_CANDIDATES = 12


def _spelling_variants(text: str) -> list[str]:
    if not text:
        return []
    variants = [text.translate(_HAMZA_SEATS)]
    if text.startswith(_ALEF):
        variants += ["\u0623" + text[1:], "\u0625" + text[1:]]
    swap = _FINAL_SWAPS.get(text[-1])
    if swap is not None:
        variants.append(text[:-1] + swap)
    return variants


def _clitic_strips(text: str) -> list[str]:
    out = [text[len(p) :] for p in _PROCLITIC_STRIPS if text.startswith(p) and len(text) - len(p) >= 2]
    out += [text[: -len(e)] for e in _ENCLITIC_STRIPS if text.endswith(e) and len(text) - len(e) >= 2]
    return out


class ArabicLookupStrategy:
    """LookupStrategy (spec C.1 ladder), every rung miss-only with conditions 0, at most 12 candidates.

    1. ``orth_base`` (the surface minus clitics: a broken plural's non-lemma wty row);
    2. hamza seats to bare alef, a bare initial alef to ``\u0623``/``\u0625`` (Yomitan's addHamzaTop/Bottom), final
       ``\u0649``/``\u064a`` swapped, final ``\u0647`` to ``\u0629`` — of the word, then of ``orth_base``;
    3. only when ``orth_base`` is ``""`` (an unanalysed token or a typed word): proclitic strips in
       Yomitan's arabic-transforms order, then enclitic pronoun strips.
    """

    def candidates(self, word: str, orth_base: str, ctype: str | None) -> list[tuple[str, int]]:
        del ctype  # duck tokens carry no cType
        out: list[tuple[str, int]] = []
        seen = {word}

        def add(text: str) -> None:
            if text and text not in seen and len(out) < MAX_LOOKUP_CANDIDATES:
                seen.add(text)
                out.append((text, 0))

        add(orth_base)
        for text in (*_spelling_variants(word), *_spelling_variants(orth_base)):
            add(text)
        if not orth_base:
            for text in _clitic_strips(word):
                add(text)
        return out


class ArabicReadingSupport:
    """``expression_reading`` = the vocalised lemma the tokenizer stored on the token (blank when unknown)."""

    def word_reading(self, token: Any) -> str:
        return str(getattr(token.feature, "reading", "") or "")


def ar_audio_candidates(word: Any) -> list[tuple[str, str]]:
    """Word-audio ladder: ``(front, vocalised lemma)`` first (P1 speaks the reading), then the bare front."""
    term = str(getattr(word, "mined_form", "") or "")
    if not term:
        return []
    reading = str(getattr(word, "expression_reading", "") or "")
    return [(term, reading), (term, term)] if reading and reading != term else [(term, term)]
