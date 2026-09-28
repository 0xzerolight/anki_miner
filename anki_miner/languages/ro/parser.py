"""Romanian SubtitleParser factory: the shared spaced factory plus the verb-front repair (``_spaced/form_of.py``).

A content token whose lemma is no headword takes the verb the dictionary's form row names: the model leaves
``Lasă``, ``înțelegi`` and ``cunosc`` inflected, strips ``caut`` to ``cauta`` and makes ``Sper`` ``spere``,
while wty-ro-en files each as a form of one verb (``lăsa``, ``înțelege``, ``cunoaște``, ``căuta``, ``spera``).
Form rows spelt with diacritics are keyed without them, and the profile's keys (``RomanianDictKeys``) let
the form lookup match their reading. Only a ``v`` lemma row may make the front, the fr/it rule.

On 110 ordinary and clitic lines, 12 of 298 mined fronts change: 11 to the verb the form row names, and the
non-word ``vorb`` to its surface ``vorbești``, which wty-ro-en files as a headword of its own (``v sg``,
"second-person singular ... of vorbi"). On 91 more lines, 6 of 260 change (``culcă`` -> ``culca``,
``locuii`` -> ``locui``, ``plâng`` -> ``plânge``, ``vorbest`` -> ``vorbești`` ...).
"""

from __future__ import annotations

from typing import Any


def create_parser(config: Any, **kwargs: Any) -> Any:
    from anki_miner.languages._spaced import create_spaced_parser
    from anki_miner.languages._spaced.form_of import FormOfLemmaPass

    kwargs.setdefault("token_post_pass", FormOfLemmaPass(front_pos=frozenset({"VERB"})))
    return create_spaced_parser(config, **kwargs)
