"""German data for the shared spaCy substrate (spec A.1 de column, §4.3 items 1–2).

``DE_EXCLUDED_SUBTYPES`` comes from real ``de_core_news_sm`` 3.8.0 output, not
from the STTS documentation. German ``pos_`` is the morphologizer's and ``tag_``
the tagger's, two independent predictions, so a closed-class fine tag can sit
under an allowed UPOS. Over 16,055 German example sentences (wty-de-en
2026.08.29, statistics only) the tags that carried NOUN/VERB/ADJ/ADV and were
never vocabulary are: NE (names and mis-tagged caps words: ``HUND``,
``Kommst``), PTKVZ (unattached separable particles ``zurück``, ``ein``), PTKANT
(``ja``, ``Bitte``), CARD, ITJ, TRUNC (``Ein-``) and XY. Left mined on the same
evidence: FM (its VERB witnesses are mostly German verbs the tagger called
foreign: ``Siehst``, ``geh``), PROAV/PWAV (``deshalb``, ``warum``: UD maps both
to ADV) and the pronoun/determiner tags (about 70 of 65k allowed tokens, and the
tagger also hangs them on unknown open-class words such as ``dalli``).

``DE_ABBREVIATIONS`` is every key of spaCy's German tokenizer exceptions that
ends in ``.`` and holds a letter, casefolded with the final dot dropped (S8),
minus three ordinary words: the weekday ``So.`` would keep the very common
sentence-final ``so.`` from ending a sentence, and ``Max.``/``Jan.`` are first
names. The spaced forms ``z. B.``/``d. h.`` enter through the single-letter keys
already present. The same set is the tokenizer's ``abbreviations`` argument, so
the exception keys it does not list (``Jan.``, ``So.``, ``max.``) are pruned there too.

``particle_less_verb`` is the German lookup rung (A §4.6 rung 3): a particle
verb the dictionary lacks (a lemmatiser-built ``herumfahren``) falls back to
the base verb. It reads the card front only: the surface ``anzusehen`` would
give ``zusehen``, a different verb. Its prefixes are the particles seen at least
three times as ``svp`` dependents in the same sample.

``adjd_as_adjective``: the model puts predicative and adverbial adjectives
(STTS ADJD: ``leer``, ``schön``, ``spät``) under UPOS ADV, so the part-of-speech
card field said "adverb" and unticking ADV dropped them. The fine tag is the
reliable signal; the pass lifts them to ADJ.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from anki_miner.languages._spaced.pos import UPOS_ALLOWED
from anki_miner.languages.token import LanguageToken

DE_MODEL_PACKAGE = "de_core_news_sm"

DE_ALLOWED_POS: tuple[str, ...] = UPOS_ALLOWED
DE_EXCLUDED_SUBTYPES: tuple[str, ...] = ("CARD", "ITJ", "NE", "PTKANT", "PTKVZ", "TRUNC", "XY")

DE_ABBREVIATIONS: frozenset[str] = frozenset(
    {
        "a", "a.c", "a.d", "a.g", "a.m", "a.z", "abb", "abk", "abs", "abt", "abzgl", "adv", "al", "allg",
        "apr", "aug", "b", "b.a", "b.sc", "bd", "betr", "bf", "bhf", "biol", "bsp", "bspw", "bzgl", "bzw", "c", "ca",
        "chr", "cie", "co", "d", "d.c", "d.h", "dez", "dgl", "di", "dipl", "dipl.-ing", "do", "dr", "e", "e.g",
        "e.v", "ebd", "ehem", "eigtl", "engl", "entspr", "erm", "etc", "ev", "evtl", "f", "fa", "fam", "feb", "fr",
        "frl", "frz", "g", "g.m.b.h", "geb", "gebr", "gegr", "gem", "ggf", "ggfs", "ggü", "h", "h.c", "hbf", "hg",
        "hr", "hrn", "hrsg", "i", "i.a", "i.d.r", "i.e", "i.g", "i.o", "i.tr", "i.v", "ii", "iii", "inc", "incl",
        "ing", "inkl", "insb", "iv", "j", "jh", "jhd", "jr", "jul", "jun", "jur", "k", "k.o", "kath", "l",
        "l.a", "lat", "lt", "m", "m.a", "m.e", "m.m", "m.sc", "mi", "min", "mind", "mio", "mo", "mr", "mrd",
        "mrz", "mtl", "mwst", "mär", "n", "n.chr", "n.y", "n.y.c", "nat", "nov", "nr", "o", "o.a", "o.g", "o.k",
        "o.ä", "okt", "orig", "p", "p.a", "p.s", "pers", "phil", "pkt", "prof", "q", "q.e.d", "r", "r.i.p", "red",
        "rer", "röm", "s", "s.o", "sa", "sen", "sep", "sept", "sog", "st", "std", "stellv", "str", "t", "tel", "tsd",
        "tägl", "u", "u.a", "u.s", "u.s.a", "u.s.s", "u.s.w", "u.u", "u.v.m", "univ", "usf", "usw", "uvm", "v",
        "v.a", "v.chr", "v.l.n.r", "vgl", "vllt", "vlt", "vol", "vs", "w", "wiss", "x", "y", "z", "z.b", "z.bsp",
        "z.t", "z.z", "z.zt", "zzgl", "°c", "°f", "°k", "ä", "ö", "österr", "ü",
    }
)  # fmt: skip

#: Words a German deck front carries that the mined lemma never does (S3): ``der Hund`` meets ``Hund``.
DE_LEADING_WORDS: frozenset[str] = frozenset({"der", "die", "das", "sich"})

#: The dependency label de_core_news_sm puts on a separable particle (TIGER ``svp``).
SEPARABLE_VERB_DEPS: frozenset[str] = frozenset({"svp"})

_PREFIXES = (
    "ab", "an", "auf", "aus", "auseinander", "bei", "da", "dar", "davon", "dran", "drauf", "durch", "ein",
    "entgegen", "entlang", "fern", "fest", "fort", "frei", "gegenüber", "her", "heran", "heraus", "herum",
    "herunter", "hervor", "hin", "hinein", "hinweg", "hinzu", "hoch", "los", "mit", "nach", "nieder", "raus",
    "rein", "runter", "schwer", "statt", "über", "übrig", "um", "unter", "vor", "vorbei", "weg", "weh", "weiter",
    "wieder", "zu", "zurück", "zusammen",
)  # fmt: skip
DE_SEPARABLE_PREFIXES: tuple[str, ...] = tuple(sorted(_PREFIXES, key=lambda prefix: (-len(prefix), prefix)))

_MIN_VERB_STEM = 4


def particle_less_verb(word: str, surface: str) -> list[str]:
    """Lookup rung over ``(mined_form, surface)``: ``ansehen`` → ``sehen``.

    Reads the mined form only; lowercase infinitive shapes; longest prefix first.
    """
    del surface  # anzusehen would strip to zusehen, another verb
    if not word.endswith("n") or not word[:1].islower():
        return []
    return [
        word[len(prefix) :]
        for prefix in DE_SEPARABLE_PREFIXES
        if word.startswith(prefix) and len(word) - len(prefix) >= _MIN_VERB_STEM
    ]


def adjd_as_adjective(tokens: list[LanguageToken]) -> list[LanguageToken]:
    """Tokenizer post-pass: ADV tokens the tagger marks ADJD (predicative/adverbial adjectives) become ADJ."""
    for token in tokens:
        if token.feature.pos1 == "ADV" and token.feature.pos2 == "ADJD":
            token.feature.pos1 = "ADJ"
    return tokens


#: German quote pairs for the sentence splitter: „…“ ‚…‘ »…« ›…‹ plus brackets.
#: The shared Latin set treats “ as an OPENER, which German uses to close.
DE_OPENERS: frozenset[str] = frozenset("([{„‚»›")
DE_CLOSERS: frozenset[str] = frozenset(")]}“‘«‹")

#: The article in the gender field (A.3: gender is the first German card field).
DE_GENDER_LABELS: Mapping[str, str] = MappingProxyType({"masc": "der", "fem": "die", "neut": "das"})
