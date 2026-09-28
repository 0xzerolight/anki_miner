"""French SubtitleParser factory: the shared spaced factory plus the verb-front repairs.

The form-of repair (``_spaced/form_of.py``) runs first: a content token whose lemma is no headword
takes the verb the dictionary's form row names (``viens`` lemmatised ``vien`` -> ``venir``,
``peux`` -> ``pouvoir``). Only a ``v`` lemma row may make the front, and its class is the new POS
(``devoir`` files the noun first). ``FrenchVerbLemmaPass`` then repairs the ``-e`` gap it leaves:
``porte`` is itself a headword (the door).
"""

from __future__ import annotations

from typing import Any


def create_parser(config: Any, **kwargs: Any) -> Any:
    from anki_miner.languages._spaced import create_spaced_parser
    from anki_miner.languages._spaced.form_of import FormOfLemmaPass, OrderedPasses
    from anki_miner.languages.fr.morphology import FrenchVerbLemmaPass

    kwargs.setdefault(
        "token_post_pass",
        OrderedPasses(FormOfLemmaPass(front_pos=frozenset({"VERB"})), FrenchVerbLemmaPass()),
    )
    return create_spaced_parser(config, **kwargs)
