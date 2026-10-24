"""Romanian card front: a verb is written as its citation infinitive, ``a vorbi``.

Learner dictionaries, course material and Wiktionary's head line cite a Romanian verb with the infinitive's ``a``
(``a vorbi`` "to speak"), and so does a deck built from them (``a fi``, ``a avea``); the bare ``vorbi`` reads as a
non-word next to them, and the ``a`` tells a verb from its look-alike noun at a glance (``a suna`` / ``sun``). The hook
moves the front only: ``mined_form`` stays ``vorbi`` for the dictionary, the frequency list, word audio and known
words, and the profile's comparison fold (``RO_LEADING_WORDS``) meets ``a vorbi`` in Anki with the mined ``vorbi``, so
the known gate needs no ``card_front`` probe. A reflexive is not told apart from the line (``se bucură`` fronts ``a
bucura``), which is still the verb's citation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from anki_miner.languages.profile import CARD_FRONT_KEY

if TYPE_CHECKING:  # annotation-only, the ko/render.py pattern
    from anki_miner.config.config import AnkiMinerConfig

_VERB = "VERB"


class RomanianInfinitiveFront:
    """``CARD_FRONT_KEY``: ``a`` + the infinitive for a word mined as a verb; no field of its own."""

    def field_names(self) -> tuple[str, ...]:
        return ()

    def render(self, word: Any, *, config: AnkiMinerConfig) -> dict[str, str]:
        del config  # every Romanian verb card fronts its citation; no setting gates it
        mined = str(getattr(word, "mined_form", "") or "")
        if not mined or str(getattr(word, "pos", "") or "") != _VERB:
            return {}
        return {CARD_FRONT_KEY: f"a {mined}"}
