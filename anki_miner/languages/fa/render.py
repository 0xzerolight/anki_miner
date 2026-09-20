"""Persian card fields (spec C.2): Romanization, Colloquial form and Present stem.

The romanisation is the dictionary's OWN head line -- wty-fa-en renders
``Grammar-content`` as ``<headword> (bullet) (romanisation)`` -- never a
transliteration computed here: Persian omits its short vowels, so no rule over
the spelling can tell xar ("donkey") from xor ("eat"), and the dictionary
already knows.

16,965 of the 17,040 lemma rows carry a head line, in six shapes (measured over
the six term banks): a single spelling, two split by ``/`` with or without
spaces, two split by ``,``, alternatives joined by ``or``, and a spelling that
contains its own parentheses (``ab(-e) kir``). The group is therefore read by
counting brackets rather than with ``[^)]+``, which truncates those 19 rows, and
the trailing ``(plural ...)``/``(Tajik spelling ...)`` groups real rows append
are never mistaken for the romanisation.

The other two fields read the token's ``morph`` string (``morphology.fa_morph``):
a ``TokenizedWord`` carries no ``pos2`` and no feature namespace, so that is the
only channel between the tokenizer and the card.
"""

from __future__ import annotations

import html
import re
from typing import TYPE_CHECKING, Any

from anki_miner.languages.fa.morphology import FA_MORPH_INFORMAL, FA_MORPH_PRESENT_STEM, fa_morph_value

if TYPE_CHECKING:  # annotation-only, the ko/render.py pattern
    from anki_miner.config.config import AnkiMinerConfig
    from anki_miner.languages.profile import CardRenderHook

_HEAD_RE = re.compile(r'data-sc-content="Grammar-content"[^>]*>(.*?)</div>', re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_BULLET = "\N{BULLET}"


def _bracketed(text: str) -> str:
    """The balanced bracket group that follows the head line's bullet."""
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


def _preferred(capture: str) -> str:
    """One spelling out of the head line's variants.

    ``or`` separates whole alternatives (``nawid /navid or nuwed /-``), so it is
    resolved first and the rest of the rule runs on the first of them. Of a
    slash pair the SECOND member is the Iranian circumflex spelling a learner
    wants (``halal / halal``); of a comma pair the first is.
    """
    variant = capture.split(" or ")[0]
    if "/" in variant:
        variant = variant.rsplit("/", 1)[1]
    elif "," in variant:
        variant = variant.split(",")[0]
    return variant.strip()


def romanization(definition_html: str) -> str:
    """The romanisation of the first entry that carries a head line, or ``""``."""
    for block in _HEAD_RE.findall(definition_html or ""):
        text = " ".join(html.unescape(_TAG_RE.sub(" ", block)).split())
        capture = _bracketed(text)
        if capture:
            return _preferred(capture)
    return ""


def _morph(word: Any) -> str:
    return str(getattr(word, "morph", "") or "")


class PersianRomanizationHook:
    """``reading_romanized``: the dictionary's own Latin spelling of the front."""

    def field_names(self) -> tuple[str, ...]:
        return ("reading_romanized",)

    def render(self, word: Any, *, config: AnkiMinerConfig) -> dict[str, str]:
        del config  # the mapped field name is the switch; no setting gates it
        reading = romanization(str(getattr(word, "definition_html", "") or ""))
        return {"reading_romanized": reading} if reading else {}


class PersianRegisterHook:
    """``colloquial_form``: the everyday spelling the line used, when the front is the standard one.

    xune is what a subtitle writes; xane is what a dictionary lists and what the
    card asks for. The field keeps the spelling the learner actually heard.
    """

    def field_names(self) -> tuple[str, ...]:
        return ("colloquial_form",)

    def render(self, word: Any, *, config: AnkiMinerConfig) -> dict[str, str]:
        del config
        if FA_MORPH_INFORMAL not in _morph(word).split("|"):
            return {}
        surface = str(getattr(word, "surface", "") or "")
        front = str(getattr(word, "mined_form", "") or "")
        return {"colloquial_form": surface} if surface and surface != front else {}


class PersianStemHook:
    """``present_stem``: a verb's present stem, which every everyday form is built on.

    An infinitive alone does not tell a learner how to say the verb: raftan
    ("to go") conjugates on ro-, not raft-, and the pair has to be learnt
    together. Empty for everything that is not a verb.
    """

    def field_names(self) -> tuple[str, ...]:
        return ("present_stem",)

    def render(self, word: Any, *, config: AnkiMinerConfig) -> dict[str, str]:
        del config
        stem = fa_morph_value(_morph(word), FA_MORPH_PRESENT_STEM)
        return {"present_stem": stem} if stem else {}


FA_RENDER_HOOKS: tuple[CardRenderHook, ...] = (
    PersianRomanizationHook(),
    PersianRegisterHook(),
    PersianStemHook(),
)
