"""Portuguese data for spaCy mining: POS gate, abbreviations, known-word fold, hyphen enclisis.

``PT_EXCLUDED_SUBTYPES`` is empty on evidence: ``pt_core_news_sm`` 3.8.0 has no
``tagger`` component and its ``attribute_ruler`` copies the UPOS tag into TAG,
so every token's fine tag equals its UPOS and the shared duck tokens carry
``pos2 == ""`` (``tests/unit/languages/test_pt_pos_corpus.py`` pins it).

``PT_ABBREVIATIONS`` is spaCy's Portuguese tokenizer-exception list (the
dotted entries it adds to the base exceptions, casefolded, final dot dropped)
minus ``dom``, an ordinary word (``Ela tem um dom.`` ends its sentence), plus
the titles spaCy leaves out (``Dra. Srta. Prof. Profa. Mrs. Ms.``). The same set
feeds the sentence splitter and the tokenizer's word-dot pruning.

Hyphen enclisis (spec §4.3): the model keeps ``Dá-me``/``levantou-se`` as one
token whose lemma is unusable (0 of 50 probed fronts were right), so the tagger
tags a same-length copy in which the hyphen before a closing clitic is a space
(``enclitic_copy``). Before ``-lo/-la/-los/-las`` Portuguese spelling drops the
verb's final ``-r``/``-s``/``-z`` and accents the stem (``fazê-lo``,
``comprá-la``, ``fá-lo``), which the model cannot lemmatise;
``infinitive_before_l_clitic`` reads the infinitive back from that spelling.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from types import MappingProxyType

from anki_miner.languages._spaced.pos import UPOS_ALLOWED
from anki_miner.languages.token import LanguageToken

#: The model package the tokenizer loads and the availability probe looks for.
PT_MODEL_PACKAGE = "pt_core_news_sm"

PT_ALLOWED_POS: tuple[str, ...] = UPOS_ALLOWED
PT_EXCLUDED_SUBTYPES: tuple[str, ...] = ()

PT_ABBREVIATIONS: frozenset[str] = frozenset(
    {
        # titles
        "sr", "sra", "srta", "dr", "dra", "prof", "profa", "jr", "adm", "gen", "gov", "rep", "rev", "sen", "mr", "ph.d",
        "mrs", "ms",
        # references and addresses
        "art", "av", "ed", "eng", "fund", "pág", "pag", "tel",
        # Latin and business
        "etc", "e.g", "i.e", "vs", "inc", "ltd", "cia", "p.m",
    }
)  # fmt: skip

#: Leading words a deck front carries that the mined lemma never does (S3): ``o livro`` meets ``livro``.
PT_LEADING_WORDS: frozenset[str] = frozenset({"o", "a", "os", "as", "um", "uma", "uns", "umas"})

#: ``noun_gender`` labels: the definite article, the form Portuguese decks print (B.5).
PT_GENDER_LABELS: Mapping[str, str] = MappingProxyType({"masc": "o", "fem": "a"})

#: Unstressed object/reflexive pronouns that attach to a verb with a hyphen,
#: including the contracted pairs (``mo`` = me + o, ``lho`` = lhe + o) and the
#: ``l``/``n`` allomorphs of ``o a os as``.
PT_CLITICS: tuple[str, ...] = (
    "me", "te", "se", "lhe", "lhes", "nos", "vos",
    "o", "a", "os", "as", "lo", "la", "los", "las", "no", "na", "nas",
    "mo", "ma", "mos", "mas", "to", "ta", "tos", "tas", "lho", "lha", "lhos", "lhas",
)  # fmt: skip

#: The allomorph that follows a verb whose final -r/-s/-z was dropped.
L_CLITICS: frozenset[str] = frozenset({"lo", "la", "los", "las"})

#: A hyphen between a letter and a clitic that ENDS the hyphen chain: the
#: middles of ``bem-te-vi``, ``louva-a-deus`` and ``dia-a-dia`` never match.
_ENCLITIC_HYPHEN = re.compile(
    r"(?<=[^\W\d_])-(?=(?:" + "|".join(sorted(PT_CLITICS, key=len, reverse=True)) + r")(?![^\W\d_]|-))",
    re.IGNORECASE,
)

#: Irregular verbs whose -lo host is not an accented infinitive stem: faz/fez/fiz,
#: diz, traz, quis, pôs/pus — and ``pô``, which the ending rule would turn into
#: the preposition ``por``.
_IRREGULAR_L_HOSTS: Mapping[str, str] = MappingProxyType(
    {
        "fá": "fazer",
        "fê": "fazer",
        "fi": "fazer",
        "di": "dizer",
        "trá": "trazer",
        "qui": "querer",
        "pô": "pôr",
        "pu": "pôr",
    }
)

#: Stem ending -> infinitive ending: comprá -> comprar, vendê -> vender, parti -> partir, compô -> compor.
_L_HOST_ENDINGS: Mapping[str, str] = MappingProxyType({"á": "ar", "ê": "er", "í": "ir", "i": "ir", "ô": "or"})

#: A single-token deck front's reflexive tail (``levantar-se``); the mined front is the bare verb.
_REFLEXIVE_TAIL = "-se"

#: The UPOS classes a card front can come from; a whitespace lemma elsewhere never mines.
_FRONT_POS = frozenset(UPOS_ALLOWED)


def enclitic_copy(text: str) -> tuple[str, frozenset[int]]:
    """The copy the model tags, and the offsets where split-off clitics start.

    Every hyphen before a closing clitic becomes a space and the host word's
    first letter is lowercased: an enclitic host is a verb by spelling, never a
    name, and the model tags a capitalised cue-initial ``Diga`` as PROPN. Same
    length as *text* (a letter whose lowercase is longer stays as it is), so
    surfaces sliced from the original line by token offset stay verbatim.
    """
    chars = list(text)
    starts: list[int] = []
    for match in _ENCLITIC_HYPHEN.finditer(text):
        hyphen = match.start()
        chars[hyphen] = " "
        starts.append(hyphen + 1)
        host = hyphen
        while host > 0 and text[host - 1].isalpha():
            host -= 1
        lowered = chars[host].lower()
        if len(lowered) == 1:
            chars[host] = lowered
    return "".join(chars), frozenset(starts)


def infinitive_before_l_clitic(host: str) -> str | None:
    """The infinitive a ``-lo/-la/-los/-las`` host spells, or None when the spelling says nothing.

    ``fazê``/``comprá``/``parti``/``compô`` -> ``fazer``/``comprar``/``partir``/``compor``;
    the irregular finite hosts come from a closed table (``fá`` -> ``fazer``,
    ``pô`` -> ``pôr``). Any other ending (``comemo``, a 1st plural) -> None.
    """
    word = host.lower()
    if word in _IRREGULAR_L_HOSTS:
        return _IRREGULAR_L_HOSTS[word]
    if len(word) >= 2 and word[-1] in _L_HOST_ENDINGS:
        return word[:-1] + _L_HOST_ENDINGS[word[-1]]
    return None


def pt_dedup_fold(base: Callable[[str], str]) -> Callable[[str], str]:
    """S3 comparison key: *base* (the spaced fold), then a single-token front's ``-se``.

    ``levantar-se`` meets the mined ``levantar``. Looped until stable and
    re-folded after each strip, so the result is idempotent like *base*.
    """

    def fold(text: str) -> str:
        folded = base(text)
        while " " not in folded and folded.endswith(_REFLEXIVE_TAIL) and len(folded) - len(_REFLEXIVE_TAIL) >= 2:
            folded = base(folded[: -len(_REFLEXIVE_TAIL)])
        return folded

    return fold


def whole_word_lemma(tokens: list[LanguageToken]) -> list[LanguageToken]:
    """``TokenPass``: a front-capable token whose lemma has a space takes its own spelling.

    The model lemmatises the fused adverbs ``daqui``/``dali``/``daí`` as
    ``de aqui``/``de ali``/``de aí`` (UD Bosque multiword-token lemmas). A card
    front is one word, and the dictionary keys the fused spelling.
    """
    for token in tokens:
        if token.feature.pos1 in _FRONT_POS and " " in token.feature.lemma:
            token.feature.lemma = token.surface.lower()
    return tokens


#: Pure, dictionary-free repairs run after the enclitic split inside every tagger call (en contract item 6).
PT_POST_PASSES = (whole_word_lemma,)
