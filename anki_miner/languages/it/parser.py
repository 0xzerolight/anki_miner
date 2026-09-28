"""Italian SubtitleParser factory: the shared spaced factory plus the verb-front and attested-lemma repairs.

The form-of repair (``_spaced/form_of.py``) runs first: a content token whose lemma is no headword
takes the verb the dictionary's form row names (``chiamo`` lemmatised ``chare`` -> ``chiamare``),
where ``AttestedLemmaPass`` alone would front the inflected surface. Only a ``v`` lemma row may make
the front, so ``maestra``, a form row of the noun ``maestro``, keeps its own front.
``AttestedLemmaPass`` then handles what the form rows cannot settle.
"""

from __future__ import annotations

from typing import Any


def create_parser(config: Any, **kwargs: Any) -> Any:
    from anki_miner.languages._spaced import create_spaced_parser
    from anki_miner.languages._spaced.form_of import FormOfLemmaPass, OrderedPasses
    from anki_miner.languages.it.morphology import AttestedLemmaPass

    kwargs.setdefault(
        "token_post_pass",
        OrderedPasses(FormOfLemmaPass(front_pos=frozenset({"VERB"})), AttestedLemmaPass()),
    )
    return create_spaced_parser(config, **kwargs)
