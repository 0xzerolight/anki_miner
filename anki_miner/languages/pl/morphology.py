"""Polish data for the shared spaCy substrate (spec Appendix B pl column, §4.3).

``PL_EXCLUDED_SUBTYPES`` comes from real ``pl_core_news_sm`` 3.8.0 output, not
from the NKJP tagset documentation. ``pos_`` is the morphologizer's and ``tag_``
the tagger's, two independent predictions, so a closed-class NKJP tag can sit
under an allowed UPOS. Over 7,933 wty-pl-en 2026.08.29 example sentences
(statistics only) the tags under NOUN/VERB/ADJ/ADV whose lemma is almost never
an n/v/adj/adv headword are excluded: INTERP (``+``; 3 of 239), XXX (foreign
words: ``For``, ``using``, ``cum``; 13 of 198), ADJA (compound-adjective stems
``gastro``, ``roz``; 13 of 64), CONJ (``tym``, ``jak``; 4 of 22), PREP
(``gwoli``, ``wokół``), COMP (``zeby``, ``coby``), INTERJ (``precz``, ``kurde``),
PPRON12/PPRON3/SIEBIE (pronouns: ``mię``, ``je``, ``se``), NUMCOL (``dwojga``),
BURK (``trosze``, ``zamian``). AGLT (the ``-(e)m/-(e)ś/-(e)śmy`` agglutinate) is
the one tag excluded on tagset grounds rather than by that measured rule: its
single observed token (``byliśmy`` → ``być``) is a headword, but the tag marks a
clitic that is never a word to mine, and that token's lemma tail is what
``drop_agglutinate_tail`` repairs. Kept on the same evidence: QUB (``właśnie``,
``zbyt``: 91 of 149), NUM (``dużo``, ``mało``, ``trochę``), PRED (``można``,
``trzeba``), BREV and ``_SP`` (real words the tagger mislabels: ``chcesz``,
``Chcę``), WINIEN, BEDZIE, GER, IMPS, PCON, PANT.

``PL_ABBREVIATIONS``: spaCy ships no Polish tokenizer exceptions, so the set is
hand-written from the PWN spelling rules (sjp.pwn.pl/zasady, fetched
2026-09-17: [205] initial letters of a word, [207] a multi-word name with one
dot, [208] a dot after each word, [337] a truncated word), every dotted example
those rules print, plus eight common abbreviations of the same shapes (``np tj
tzn tzw str pok św ks``). Left out on purpose, because the undotted stem is an
ordinary word that ends sentences: ``ok`` (``OK.``), ``im`` (``Powiedz im.``),
``min`` (genitive plural of ``mina``). The same set feeds the sentence splitter
and ``build_spacy_tagger(abbreviations=...)``; ``abbreviation_cases`` lists the
dotted spellings ``pl/tokenizer.py`` keeps whole, because the tokenizer splits
the dot off (``str.`` → ``str`` tagged NOUN, lemma ``stręp``) and only a dotted
token is the shared ``X`` abbreviation.

``drop_agglutinate_tail``: PDB splits a past or conditional verb from its person
and conditional clitics (``widziałem`` = ``widział`` + ``em``), and the trained
lemmatiser joins their lemmas with spaces: ``widziałem`` → ``widzieć być``,
``mógłbyś`` → ``móc by być`` (202 of 4,447 VERB/AUX tokens on the UD Polish PDB
test split). The card front keeps the verb.

``drop_plurale_tantum_gender``: PDB tags pluralia tantum ``Gender=Neut|Number=Ptan``
(``drzwi``, ``spodnie``, ``okulary``). That is a treebank convention, not a
gender, and the gender field would print ``n``.

``pl_dedup_fold``: Polish decks write a reflexive verb with its particle after
it (``bać się``) while the mined front is the verb (``bać``; ``się`` is a
pronoun, never on the front, spec §2). The shared fold only drops LEADING words,
so the trailing ``się`` is dropped here.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from types import MappingProxyType

from anki_miner.languages._spaced.keys import CasefoldDictKeys, spaced_dedup_fold
from anki_miner.languages._spaced.pos import UPOS_ALLOWED
from anki_miner.languages._spaced.script import BRACKETS_PATTERN, DIALOGUE_DASH_PATTERN, MUSIC_PATTERN, PARENS_PATTERN
from anki_miner.languages._spaced.sentence import sentence_rules
from anki_miner.languages.token import LanguageToken

PL_MODEL_PACKAGE = "pl_core_news_sm"

PL_ALLOWED_POS: tuple[str, ...] = UPOS_ALLOWED
PL_EXCLUDED_SUBTYPES: tuple[str, ...] = (
    "ADJA", "AGLT", "BURK", "COMP", "CONJ", "INTERJ", "INTERP", "NUMCOL", "PPRON12", "PPRON3", "PREP", "SIEBIE", "XXX",
)  # fmt: skip

PL_ABBREVIATIONS: frozenset[str] = frozenset(
    {
        # [205] initial letter(s) of one word
        "a", "g", "n", "o", "p", "r", "s", "t", "v", "al", "bł", "dyr", "godz", "hr", "ib", "ibid", "jun", "lic",
        "mies", "ob", "os", "pl", "por", "prof", "ryc", "ul", "zob", "żeń",
        # [207] a multi-word name, one dot
        "bm", "br", "cdn", "dn", "ds", "itd", "itp", "jw",
        # [207]/[208] a dot after each word
        "b.r", "b.u", "c.o", "d.n", "m.in", "n.e", "o.o", "p.o",
        # [337] a truncated word
        "dr",
        # the same shapes, not printed as rule examples
        "ks", "np", "pok", "str", "św", "tj", "tzn", "tzw",
    }
)  # fmt: skip

_LATIN_RULES = sentence_rules(PL_ABBREVIATIONS)
#: Polish opens a quotation with „ (its closer ” is already a shared closer). Additive, so a
#: shared opener added later reaches Polish too (the nl shape, ``nl/__init__.py``).
PL_SENTENCE_RULES = dataclasses.replace(_LATIN_RULES, openers=_LATIN_RULES.openers | frozenset("„"))

#: ``ŁUKASZ:``, ``ŚWIADEK:`` — the shared Latin speaker rule with the Polish capitals. Every Polish
#: capital but Ó lies outside the shared class ``A-ZÀ-ÖØ-Þ``, so the shared preset leaves the label
#: in the cue and the parser mines it (probed: ``MAREK:`` strips, ``ŁUKASZ:`` does not).
PL_SPEAKER_PATTERN = r"^[A-ZÀ-ÖØ-ÞĄĆĘŁŃŚŹŻ][A-ZÀ-ÖØ-ÞĄĆĘŁŃŚŹŻ0-9 .'-]*[A-ZÀ-ÖØ-ÞĄĆĘŁŃŚŹŻ]:\s*"
#: The S10 default for Polish: the shared parts with the Polish speaker rule. No inline flags.
PL_SUBTITLE_REGEX = "|".join(
    (BRACKETS_PATTERN, PARENS_PATTERN, MUSIC_PATTERN, PL_SPEAKER_PATTERN, DIALOGUE_DASH_PATTERN)
)

#: noun_gender labels: the spec's pl m/f/n (§4.8).
PL_GENDER_LABELS: Mapping[str, str] = MappingProxyType({"masc": "m", "fem": "f", "neut": "n"})

#: Masculine animacy sub-genders (Addendum A). Polish masculine agrees in three classes, and a dictionary
#: prints them as the head-line qualifier the seam parses: ``stół m inan``, ``student m pers``, ``pies m animal``.
PL_ANIMACY_LABELS: Mapping[str, str] = MappingProxyType({"pers": "m pers", "anim": "m anim", "inan": "m inan"})

#: Words a Polish deck front carries AFTER the verb that the mined front never has (S3).
PL_TRAILING_WORDS: frozenset[str] = frozenset({"się"})

PL_KEYS = CasefoldDictKeys()

#: Clitic lemmas PDB's lemmatiser appends to a verb lemma.
_AGGLUTINATE_LEMMAS: frozenset[str] = frozenset({"by", "być"})


def abbreviation_cases(abbreviations: frozenset[str]) -> list[str]:
    """Every dotted spelling kept whole by the tokenizer: each key and its first-letter capital, with the dot."""
    spellings = set()
    for key in abbreviations:
        spellings.add(f"{key}.")
        spellings.add(f"{key[:1].upper()}{key[1:]}.")
    return sorted(spellings)


def drop_agglutinate_tail(tokens: list[LanguageToken]) -> list[LanguageToken]:
    """Tokenizer post-pass: ``widzieć być`` → ``widzieć`` when every word after the first is ``by``/``być``."""
    for token in tokens:
        words = token.feature.lemma.split()
        if len(words) > 1 and all(word in _AGGLUTINATE_LEMMAS for word in words[1:]):
            token.feature.lemma = words[0]
    return tokens


def drop_plurale_tantum_gender(tokens: list[LanguageToken]) -> list[LanguageToken]:
    """Tokenizer post-pass: a ``Number=Ptan`` token loses its conventional ``Gender=`` feature."""
    for token in tokens:
        features = token.morph.split("|") if token.morph else []
        if "Number=Ptan" in features:
            token.morph = "|".join(feature for feature in features if not feature.startswith("Gender="))
    return tokens


#: The language's post-passes, in order (``build_spacy_tagger(post_passes=...)``).
PL_POST_PASSES = (drop_agglutinate_tail, drop_plurale_tantum_gender)

_SHARED_FOLD = spaced_dedup_fold(PL_KEYS)


def pl_dedup_fold(text: str) -> str:
    """The S3 comparison fold, then a trailing ``się`` dropped while another word remains (idempotent)."""
    tokens = _SHARED_FOLD(text).split()
    while len(tokens) > 1 and tokens[-1] in PL_TRAILING_WORDS:
        tokens = tokens[:-1]
    return " ".join(tokens)
