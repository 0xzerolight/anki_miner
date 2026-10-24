"""Romanian card front: a verb is written as its citation infinitive, ``a vorbi``.

Learner dictionaries, course material and Wiktionary's head line cite a Romanian verb with the infinitive's ``a``
(``a vorbi`` "to speak"), and so does a deck built from them (``a fi``, ``a avea``); the bare ``vorbi`` reads as a
non-word next to them, and the ``a`` tells a verb from its look-alike noun at a glance (``a suna`` / ``sun``). The hook
moves the front only: ``mined_form`` stays ``vorbi`` for the dictionary, the frequency list, word audio and known
words, and the profile's comparison fold (``RO_LEADING_WORDS``) meets ``a vorbi`` in Anki with the mined ``vorbi``, so
the known gate needs no ``card_front`` probe. A reflexive is not told apart from the line (``se bucură`` fronts ``a
bucura``), which is still the verb's citation.

The front takes the ``a`` only when the card's own definition cites the word that way: wty-ro-en opens every verb
entry with that head line (``a vorbi (third-person singular present vorbește, …)``). A word the tagger calls a verb
wrongly keeps its bare front, so it does not pose as a verb: on one episode of a TV series 45 of 59 verb cards cite
``a``, and 13 of the other 14 were no verbs at all (``haide``, the auxiliaries ``va``/``vei``, the noun ``tacă``, a
name). The one real verb left bare, ``gira``, showed its participle's definition.
"""

from __future__ import annotations

import html
import re
from typing import TYPE_CHECKING, Any

from anki_miner.languages.profile import CARD_FRONT_KEY

if TYPE_CHECKING:  # annotation-only, the ko/render.py pattern
    from anki_miner.config.config import AnkiMinerConfig

_VERB = "VERB"
_TAG_RE = re.compile(r"<[^>]+>")


def _cites_infinitive(definition_html: str, mined: str) -> bool:
    """Whether the definition writes *mined* as ``a <mined>``, a word of its own (markup read as spaces)."""
    text = html.unescape(_TAG_RE.sub(" ", definition_html))
    return re.search(rf"(?<!\S)a {re.escape(mined)}(?=[\s(,;:]|$)", text) is not None


class RomanianInfinitiveFront:
    """``CARD_FRONT_KEY``: ``a`` + the infinitive for a verb its definition cites so; no field of its own."""

    def field_names(self) -> tuple[str, ...]:
        return ()

    def render(self, word: Any, *, config: AnkiMinerConfig) -> dict[str, str]:
        del config  # every Romanian verb card fronts its citation; no setting gates it
        mined = str(getattr(word, "mined_form", "") or "")
        if not mined or str(getattr(word, "pos", "") or "") != _VERB:
            return {}
        if not _cites_infinitive(str(getattr(word, "definition_html", "") or ""), mined):
            return {}
        return {CARD_FRONT_KEY: f"a {mined}"}
