"""Slovenian SubtitleParser factory: the shared spaced factory plus the form-of front repair (``_spaced/form_of.py``).

The repair reads wty-sl-en's form rows, which the profile's keys reach through ``sl_tone_fold`` (``čȃkam``
is the key the dictionary writes for ``čakam``). Only a headword of the token's own class may become its
front (``same_pos``): the dictionary files ``mȃma`` as a form of the verb ``imeti`` and ``prav`` as a form of
the noun ``pravo``, and a noun or adverb front must not turn into either. The gate also leaves the verb forms
the model tags as nouns (``Čakam``, ``pojdi``) as they are. A target carrying accent notation (``bank`` names
``bánka``) is read through the same fold, so the card front carries no mark. On 135 dialogue lines, with
wty-sl-en imported under the folded keys, the wrong fronts go from 58 of 317 parsed words to 41 of 310. The
one right front that measurement saw it change, ``prebrati`` -> ``brati``, now stays: the dictionary files
``prebrati`` only as the ``perfective`` partner of ``brati``, and the pass keeps a verb named only as another
verb's aspect partner.
"""

from __future__ import annotations

from typing import Any

from anki_miner.languages._spaced.form_of import lemma_row_targets
from anki_miner.languages.sl.morphology import sl_tone_fold


def toneless_row_targets(content: str, tags: str) -> list[str] | None:
    """``lemma_row_targets`` with the accent notation off every target."""
    targets = lemma_row_targets(content, tags)
    return None if targets is None else [sl_tone_fold(target) for target in targets]


def create_parser(config: Any, **kwargs: Any) -> Any:
    from anki_miner.languages._spaced import create_spaced_parser
    from anki_miner.languages._spaced.form_of import FormOfLemmaPass

    kwargs.setdefault("token_post_pass", FormOfLemmaPass(same_pos=True, row_targets=toneless_row_targets))
    return create_spaced_parser(config, **kwargs)
