"""th card render hooks (spec C.3).

Each hook returns LOGICAL ``anki_fields`` keys; EpisodeProcessor phase 5 merges
them into ``extra_fields`` and ``anki_note_builder`` maps key -> Anki field name.
An unmapped key is skipped by the existing empty-name rule, so every hook field
is opt-in exactly like frequency/pitch/expression_audio.

Thai has no reading engine in this app: PyThaiNLP's ``royin`` transliterator is
RTGS, which carries neither tone nor vowel length and is wrong on common words,
and the neural romanisers need torch. The reading a learner wants is Paiboon,
and it already exists in the dictionary data -- so both hooks READ
``definition_html`` rather than generating anything. No dictionary hit means a
blank field, which is honest.

Two dictionary styles, both measured against the real indexes:

* Wiktionary (``wty-th-en``) renders a Grammar line with the headword, a bullet
  and the transcription in parentheses. Over all 19,570 Grammar lines in the
  term bank no line carries two bullets and no bullet group contains a Thai
  character, so the first match is the reading.
* Volubilis renders the reading in leading brackets at the start of the gloss,
  the brackets sometimes holding a variant, which is kept: it is part of what
  that dictionary states about the pronunciation.

The classifier regex requires the WORD "classifier" inside the group. The same
parenthesis shape carries an "abstract noun" note on 20 wty lines, and an
abstract noun in the classifier field is worse than an empty one.
"""

from __future__ import annotations

import html as html_mod
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # annotation-only: keeps profile.py's resource_catalog import out of the runtime path
    from anki_miner.config.config import AnkiMinerConfig
    from anki_miner.languages.profile import CardRenderHook

_TAG_RE = re.compile(r"<[^>]+>")
#: wty: the bullet that separates the headword from its transcription.
_WTY_READING_RE = re.compile(r"•\s*\(([^)]+)\)")
#: Volubilis: the leading bracket group at the start of a gloss.
_VOL_READING_RE = re.compile(r"(?:^|>|\n)\s*\[([^\]]+)\]")
#: wty: "(classifier X)", "(classifier X or Y or Z)".
_WTY_CLASSIFIER_RE = re.compile(r"\(classifiers?\s+([^)]+)\)")
#: Volubilis: "classifier: X" (the run stops at the line break the renderer
#: turned into a newline, or at a comma separating the next field).
_VOL_CLASSIFIER_RE = re.compile(r"classifiers?\s*:\s*([^\n<,;]+)")


def _text(definition_html: str) -> str:
    """Tags out, entities decoded, <br> kept as a line break."""
    with_breaks = re.sub(r"<br\s*/?>", "\n", definition_html or "", flags=re.IGNORECASE)
    return html_mod.unescape(_TAG_RE.sub("", with_breaks))


class ThaiPaiboonHook:
    """Paiboon transcription, read out of whichever dictionary answered."""

    def field_names(self) -> tuple[str, ...]:
        return ("reading_paiboon",)

    def render(self, word: Any, *, config: AnkiMinerConfig) -> dict[str, str]:
        del config  # the reading is always emitted when a dictionary carries one
        text = _text(getattr(word, "definition_html", "") or "")
        for pattern in (_WTY_READING_RE, _VOL_READING_RE):
            match = pattern.search(text)
            if match:
                reading = match.group(1).strip()
                if reading:
                    return {"reading_paiboon": reading}
        return {}


class ThaiClassifierHook:
    """The noun classifier a dictionary states, "a / b" when it lists several."""

    def field_names(self) -> tuple[str, ...]:
        return ("classifier",)

    def render(self, word: Any, *, config: AnkiMinerConfig) -> dict[str, str]:
        del config
        text = _text(getattr(word, "definition_html", "") or "")
        for pattern in (_WTY_CLASSIFIER_RE, _VOL_CLASSIFIER_RE):
            match = pattern.search(text)
            if not match:
                continue
            forms = [part.strip() for part in re.split(r"\bor\b|,", match.group(1)) if part.strip()]
            if forms:
                return {"classifier": " / ".join(dict.fromkeys(forms))}
        return {}


TH_RENDER_HOOKS: tuple[CardRenderHook, ...] = (ThaiPaiboonHook(), ThaiClassifierHook())
