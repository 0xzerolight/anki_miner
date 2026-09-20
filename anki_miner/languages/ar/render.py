"""Arabic card fields (spec C.1): the root, the dictionary's grammar line and the clitic segmentation.

All three are plain text and gated only by their mapped field name. ``root`` renders the analyzer's
radicals spaced (``\u0643.\u062a.\u0628`` → ``\u0643 \u062a \u0628``); a root with a ``#`` (a weak or hamza radical the database does
not name) or a non-radical marker renders blank rather than guessing a letter. ``expression_grammar`` is
wty's first ``Grammar-content`` head line after the bullet, romanisations removed: gender and plurals for a
noun (``m (plural \u0643\u064f\u062a\u064f\u0628)``), the verb form and non-past/verbal noun for a verb. ``clitic_segmentation`` is
the d3tok of the surface the learner saw, present only for a clitic-bearing token.
"""

from __future__ import annotations

import html
import re
import unicodedata
from typing import TYPE_CHECKING, Any

from anki_miner.languages.ar.script import is_arabic_letter
from anki_miner.languages.profile import CardFieldSpec

if TYPE_CHECKING:  # annotation-only, the ko/render.py pattern
    from anki_miner.config.config import AnkiMinerConfig

AR_ROOT_FIELD = CardFieldSpec(key="root", capability="word_root", placeholder="Root")
AR_GRAMMAR_FIELD = CardFieldSpec(key="expression_grammar", capability="arabic_grammar", placeholder="Grammar")
AR_CLITICS_FIELD = CardFieldSpec(key="clitic_segmentation", capability="arabic_clitics", placeholder="Segmentation")
AR_EXTRA_CARD_FIELDS: tuple[CardFieldSpec, ...] = (AR_ROOT_FIELD, AR_GRAMMAR_FIELD, AR_CLITICS_FIELD)

_HEAD_RE = re.compile(r'data-sc-content="Grammar-content"[^>]*>(.*?)</div>', re.S)
_TAG_RE = re.compile(r"<[^>]+>")
#: A parenthesised run holding no Arabic-block character: a romanisation such as ``(kutub)``.
_ROMANISATION_RE = re.compile(r"\s*\([^()" + chr(0x0600) + "-" + chr(0x06FF) + r"]*\)")
_SPACE_RE = re.compile(r"\s+")
_BULLET = "•"


def render_root(root: str) -> str:
    """``\u0643.\u062a.\u0628`` → ``\u0643 \u062a \u0628``; blank unless every radical is one Arabic letter (3 or 4 of them)."""
    radicals = root.split(".")
    if not 3 <= len(radicals) <= 4 or not all(len(r) == 1 and is_arabic_letter(r) for r in radicals):
        return ""
    return " ".join(radicals)


def arabic_grammar_line(definition_html: str) -> str:
    """The first wty head line after its bullet, romanisations removed; ``""`` when there is none."""
    match = _HEAD_RE.search(definition_html or "")
    if match is None:
        return ""
    head = unicodedata.normalize("NFC", html.unescape(_TAG_RE.sub(" ", match.group(1))))
    _headword, bullet, rest = head.partition(_BULLET)
    if not bullet:
        return ""
    previous = None
    while previous != rest:  # innermost first: "(plural \u0643\u064f\u062a\u064f\u0628 (kutub))" keeps its Arabic form
        previous, rest = rest, _ROMANISATION_RE.sub("", rest)
    return _SPACE_RE.sub(" ", rest).strip()


def _morph_value(morph: str, name: str) -> str:
    for item in morph.split("|"):
        key, _, value = item.partition("=")
        if key == name:
            return value
    return ""


class ArabicCardHook:
    """``root``, ``expression_grammar`` and ``clitic_segmentation`` in one pass; empty values are omitted."""

    def field_names(self) -> tuple[str, ...]:
        return tuple(spec.key for spec in AR_EXTRA_CARD_FIELDS)

    def render(self, word: Any, *, config: AnkiMinerConfig) -> dict[str, str]:
        del config  # the mapped field name is the switch
        morph = str(getattr(word, "morph", "") or "")
        values = {
            AR_ROOT_FIELD.key: render_root(_morph_value(morph, "Root")),
            AR_GRAMMAR_FIELD.key: arabic_grammar_line(str(getattr(word, "definition_html", "") or "")),
            AR_CLITICS_FIELD.key: _morph_value(morph, "Segmentation"),
        }
        return {key: value for key, value in values.items() if value}
