"""Hebrew card fields (spec F.2): Transliteration, Root, Binyan, Gender, Plural and POS.

Four of the six come from ONE line -- the dictionary's own Grammar head line, which
``wty-he-en`` renders as ``<vocalised> (bullet) (<romanisation>) <gender> (plural indefinite
<form>, ...)`` and, for a verb, ``(<binyan> construction, infinitive ...)``. One hook parses it
once and emits all four rather than four hooks parsing it four times.

The head-line and bracket-reading helpers are COPIED from ``fa/render.py``, not imported: language
packages do not import each other, and the item brief orders the copy. Their shape is fa's -- count
brackets rather than match ``[^)]+``, because a real head line can carry parentheses of its own.

Why this language owns them instead of reusing ``_spaced.grammar_hook.GrammarTagHook``: that hook's
plural rule is ``\\bplural\\s+...([^\\s,()]+)``, which against the real Hebrew head line matches
``plural indefinite`` and captures **indefinite**. Hebrew also carries no ``morph`` string and no
spaCy part of speech, so two of that hook's three gender sources are structurally dead here. Adding
a knob to shared code to buy one ``if`` was the worse trade.

Measured over the 15,308 lemma rows of revision 2026.09.19: 15,244 carry a head line, 15,243 a
romanisation, 3,962 a ``plural indefinite`` form, 2,269 of the 2,882 verb rows a binyan, and 485 a
root in their Etymology block. The root field is therefore blank for about 97 % of lemmas by
design: it is what the dictionary spells out, never something guessed from the deinflector.
"""

from __future__ import annotations

import html
import re
from typing import TYPE_CHECKING, Any

from anki_miner.languages.he.pos import HE_POS_LABELS
from anki_miner.languages.he.script import GERESH, MAQAF
from anki_miner.languages.profile import CardFieldSpec

if TYPE_CHECKING:  # annotation-only, the ko/render.py pattern
    from anki_miner.config.config import AnkiMinerConfig
    from anki_miner.languages.profile import CardRenderHook

__all__ = [
    "HE_EXTRA_CARD_FIELDS",
    "HE_RENDER_HOOKS",
    "HebrewGrammarHook",
    "HebrewPosHook",
    "HebrewRootHook",
    "binyan",
    "gender",
    "head_line",
    "noun_plural",
    "root",
    "transliteration",
]

_HEAD_RE = re.compile(r'data-sc-content="Grammar-content"[^>]*>(.*?)</div>', re.S)
_ETYMOLOGY_RE = re.compile(r'data-sc-content="Etymology-content"[^>]*>(.*?)</div>', re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_BULLET = "\N{BULLET}"

#: The eight binyanim wty names, in the alternation its head line uses.
_BINYANIM = ("pa'al", "pi'el", "pu'al", "hif'il", "huf'al", "hitpa'el", "nif'al", "hitpu'al")
_BINYAN_RE = re.compile(r"\((" + "|".join(re.escape(name) for name in _BINYANIM) + r")[^)]*construction")
#: The plural form follows BOTH words: the shared hook's rule stops at the first and captures
#: "indefinite".
_PLURAL_RE = re.compile(r"plural indefinite ([^\s,()]+)")
#: A Semitic root, spelled out with maqafs in the Etymology prose: ``k-t-b``.
_ROOT_RE = re.compile(r"root ((?:\w" + re.escape(MAQAF) + r")+\w)")
#: The gender letter, taken after the romanisation clause so a bracketed romanisation cannot
#: supply one.
_GENDER_RE = re.compile(r"^\s*(m|f)\b")
_GENDER_LABELS = {
    "m": "\N{HEBREW LETTER ZAYIN}" + GERESH,
    "f": "\N{HEBREW LETTER NUN}" + GERESH,
}


def _text(block: str) -> str:
    return " ".join(html.unescape(_TAG_RE.sub(" ", block)).split())


def head_line(definition_html: str) -> str:
    """The first Grammar head line of a definition, tags stripped, marks intact."""
    for block in _HEAD_RE.findall(definition_html or ""):
        line = _text(block)
        if line:
            return line
    return ""


def _bracketed(text: str) -> str:
    """The balanced bracket group that follows the head line's bullet (fa's reader)."""
    bullet = text.find(_BULLET)
    if bullet < 0:
        return ""
    opened = text.find("(", bullet)
    if opened < 0:
        return ""
    depth = 0
    for index in range(opened, len(text)):
        if text[index] == "(":
            depth += 1
        elif text[index] == ")":
            depth -= 1
            if depth == 0:
                return text[opened + 1 : index]
    return ""


def _after_romanisation(head: str) -> str:
    """Everything after the head line's romanisation clause: where the gender letter sits."""
    bullet = head.find(_BULLET)
    if bullet < 0:
        return head
    opened = head.find("(", bullet)
    if opened < 0:
        return head[bullet + 1 :]
    depth = 0
    for index in range(opened, len(head)):
        if head[index] == "(":
            depth += 1
        elif head[index] == ")":
            depth -= 1
            if depth == 0:
                return head[index + 1 :]
    return ""


def transliteration(definition_html: str) -> str:
    """The dictionary's own romanisation of the headword: ``kelev``, ``halakh``.

    The first of the bracket group's comma-separated spellings -- wty lists the everyday one first
    and a scholarly transcription after it.
    """
    capture = _bracketed(head_line(definition_html))
    return capture.split(",")[0].strip() if capture else ""


def binyan(definition_html: str) -> str:
    """The verb's construction, as wty's head line names it (``pa'al``, ``nif'al``)."""
    match = _BINYAN_RE.search(head_line(definition_html))
    return match.group(1) if match else ""


def noun_plural(definition_html: str) -> str:
    """The indefinite plural, marks intact. Blank when the entry names none."""
    match = _PLURAL_RE.search(head_line(definition_html))
    return match.group(1) if match else ""


def gender(definition_html: str) -> str:
    """``m`` or ``f`` from the head line, read AFTER the romanisation clause."""
    match = _GENDER_RE.search(_after_romanisation(head_line(definition_html)))
    return match.group(1) if match else ""


def root(definition_html: str) -> str:
    """The Semitic root the Etymology block spells out, or ``""`` for the ~97 % that do not."""
    for block in _ETYMOLOGY_RE.findall(definition_html or ""):
        match = _ROOT_RE.search(_text(block))
        if match:
            return match.group(1)
    return ""


def _definition(word: Any) -> str:
    return str(getattr(word, "definition_html", "") or "")


class HebrewGrammarHook:
    """One head-line parse, four fields: transliteration, binyan, gender and plural."""

    def field_names(self) -> tuple[str, ...]:
        return ("transliteration", "binyan", "noun_gender", "noun_plural")

    def render(self, word: Any, *, config: AnkiMinerConfig) -> dict[str, str]:
        del config  # the mapped field name is the switch; no setting gates these
        definition = _definition(word)
        if not definition:
            return {}
        pos = str(getattr(word, "pos", "") or "")
        out: dict[str, str] = {}
        romanised = transliteration(definition)
        if romanised:
            out["transliteration"] = romanised
        if pos == "VERB":
            construction = binyan(definition)
            if construction:
                out["binyan"] = construction
        if pos == "NOUN":
            letter = gender(definition)
            if letter in _GENDER_LABELS:
                out["noun_gender"] = _GENDER_LABELS[letter]
            plural = noun_plural(definition)
            if plural:
                out["noun_plural"] = plural
        return out


class HebrewRootHook:
    """``root``: what the dictionary's Etymology spells out, never a guess."""

    def field_names(self) -> tuple[str, ...]:
        return ("root",)

    def render(self, word: Any, *, config: AnkiMinerConfig) -> dict[str, str]:
        del config
        found = root(_definition(word))
        return {"root": found} if found else {}


class HebrewPosHook:
    """``pos``: the resolved part of speech as a lowercase English label.

    Not ``_spaced.render.PosHook``: that one keys on the universal POS names, and Hebrew's come
    from Wiktionary's tags through ``pos.HE_TAG_TO_POS``, which has its own label map.
    """

    def field_names(self) -> tuple[str, ...]:
        return ("pos",)

    def render(self, word: Any, *, config: AnkiMinerConfig) -> dict[str, str]:
        del config
        label = HE_POS_LABELS.get(str(getattr(word, "pos", "") or ""), "")
        return {"pos": label.lower()} if label else {}


HE_TRANSLITERATION_FIELD = CardFieldSpec(
    key="transliteration", capability="hebrew_transliteration", placeholder="Transliteration"
)
#: ``root`` carries the capability ar and id share (ruling R-ROOT): the field rows dedup by key.
HE_ROOT_FIELD = CardFieldSpec(key="root", capability="word_root", placeholder="Root")
HE_BINYAN_FIELD = CardFieldSpec(key="binyan", capability="hebrew_binyan", placeholder="Binyan")
HE_GENDER_FIELD = CardFieldSpec(key="noun_gender", capability="noun_gender", placeholder="Gender")
HE_PLURAL_FIELD = CardFieldSpec(key="noun_plural", capability="noun_plural", placeholder="Plural")
HE_POS_FIELD = CardFieldSpec(key="pos", capability="pos_tag", placeholder="POS")

HE_EXTRA_CARD_FIELDS: tuple[CardFieldSpec, ...] = (
    HE_TRANSLITERATION_FIELD,
    HE_ROOT_FIELD,
    HE_BINYAN_FIELD,
    HE_GENDER_FIELD,
    HE_PLURAL_FIELD,
    HE_POS_FIELD,
)

HE_RENDER_HOOKS: tuple[CardRenderHook, ...] = (HebrewGrammarHook(), HebrewRootHook(), HebrewPosHook())
