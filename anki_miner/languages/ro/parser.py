"""Romanian SubtitleParser factory: the shared spaced factory plus three front repairs from wty-ro-en's form rows.

wty-ro-en keys a form row without diacritics and stores its spelling as the reading, and the profile's keys
(``RomanianDictKeys``) let the form lookup match it. The three passes run in this order:

* ``RomanianVerbHomographPass``: the model lemmatises verbs to a look-alike wty-ro-en files only as a noun, adverb
  or name (``Pot`` -> ``pot`` "pot", ``vin`` -> ``vin`` "wine", ``Poate`` -> the adverb ``poate``), and the
  form-of repair never second-guesses a headword. Such a VERB takes the verb its surface's form rows name, when
  they name exactly one: ``Pot`` -> ``putea``, ``sun`` -> ``suna``, ``băut`` -> ``bea``, ``râs`` -> ``râde``.
  Several targets keep the token, since picking the verb among them made ``caise`` ``căi``, the adjective ``vii``
  ``veni`` and ``deșteaptă`` ``deștepta``. On 386 ordinary lines 12 fronted a verb as such a headword, now 4
  (``Ia``, whose rows also name the obsolete ``ii``, and ``vii``).
* ``FormOfLemmaPass`` over every content class (``_spaced/form_of.py``): a content token whose lemma is no headword
  takes the headword the form row names, and that row's class is the new ``pos1``. The model leaves ``Lasă``,
  ``cheile`` and ``gările`` inflected, strips ``tată`` to ``tat`` and ``aseară`` to ``asear``, while wty-ro-en
  files each as a form of one headword (``lăsa``, ``cheie``, ``gară``) or as a headword itself. The ``{VERB}``
  scope it ran with before was copied from fr/it with no Romanian reason: on 1,124 lines widening it changes 91
  fronts and none is wrong. The lemmatised frequency list re-keys through the same pass (``școală`` 1013 -> 495).
* ``RomanianPropnRecovery``: ``ro_core_news_sm`` tags many cue-initial verbs and nouns PROPN (``Aștept``,
  ``Fugi``, ``Mulțumim``, ``Mama``, ``Iarna``), which the content gate drops. The line's first word and a
  lowercase PROPN take a lowercase front no ``name`` row files (``admit_front``, el's rule); a capital mid-line
  is a name whatever its rows say (``Delta Dunării``, ``Banat`` and ``Viorel`` would front ``deltă``, ``banat`` and
  the adjective ``viorel``). It is its own pass, after the content one, because there the class filter hides the
  name rows from the gate and ``Ion`` or ``Sorin`` would become nouns. It recovers 27 words right, 1 wrong
  (``Jur că`` -> the noun ``jur``) and none of ~60 line-initial names; a mid-line ``Mama`` stays dropped.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from anki_miner.languages._spaced.form_of import (
    WTY_TAG_TO_UPOS,
    FormOfLemmaPass,
    OrderedPasses,
    form_targets,
    is_lemma_row,
)
from anki_miner.languages._spaced.pos import UPOS_ALLOWED

if TYPE_CHECKING:  # annotation-only: services must not load at profile build
    from anki_miner.services.morphology import AttestLookup, FormLookup

_VERB = "VERB"
_PROPN = frozenset({"PROPN"})


def _verb_row(tags: str) -> bool:
    """A lemma row whose definition tags open with ``v``."""
    return is_lemma_row(tags) and WTY_TAG_TO_UPOS.get(tags.split(" ")[0]) == _VERB


class RomanianVerbHomographPass:
    """``token_post_pass``: a verb lemmatised to a non-verb headword takes the one verb its surface's form rows name."""

    def __call__(self, tokens: list[Any], attest: AttestLookup | None, forms: FormLookup | None) -> list[Any]:
        del attest
        if forms is None:
            return tokens
        verbs = [token for token in tokens if token.feature.pos1 == _VERB]
        if not verbs:
            return tokens
        rows = forms(
            list(dict.fromkeys(key for token in verbs for key in (token.feature.lemma, token.surface.lower())))
        )
        picks: list[tuple[Any, str]] = []
        for token in verbs:
            heads = [tags for _content, tags in rows.get(token.feature.lemma, ()) if is_lemma_row(tags)]
            if not heads or any(_verb_row(tags) for tags in heads):
                continue
            surface_rows = rows.get(token.surface.lower(), ())
            targets = list(
                dict.fromkeys(
                    target
                    for content, tags in surface_rows
                    if not is_lemma_row(tags)
                    for target in form_targets(content)
                )
            )
            if len(targets) == 1:
                picks.append((token, targets[0]))
        if not picks:
            return tokens
        target_rows = forms(list(dict.fromkeys(target for _token, target in picks)))
        for token, target in picks:
            if any(_verb_row(tags) for _content, tags in target_rows.get(target, ())):
                token.feature.lemma = target
        return tokens


def admit_front(token: Any, front: str, heads: Sequence[tuple[str, str]]) -> bool:
    """A recovered PROPN token takes only a lowercase front no ``name`` row files (el ``admit_front``).

    ``Ion`` is a noun (ion) as well as a name, and the name row keeps it PROPN.
    """
    del token
    return not front[:1].isupper() and not any("name" in tags.split(" ") for _content, tags in heads)


class RomanianPropnRecovery:
    """``token_post_pass``: the form-of repair over the line's first word and its lowercase words tagged PROPN."""

    def __init__(self) -> None:
        self._repair = FormOfLemmaPass(recover_pos=_PROPN, accept=admit_front)

    def __call__(self, tokens: list[Any], attest: AttestLookup | None, forms: FormLookup | None) -> list[Any]:
        first = next((token for token in tokens if any(char.isalpha() for char in token.surface)), None)
        picked = [
            token
            for token in tokens
            if token.feature.pos1 in _PROPN and (token is first or token.surface[:1].islower())
        ]
        if picked:
            self._repair(picked, attest, forms)  # repairs in place: the line keeps its order
        return tokens


def create_parser(config: Any, **kwargs: Any) -> Any:
    from anki_miner.languages._spaced import create_spaced_parser

    kwargs.setdefault(
        "token_post_pass",
        OrderedPasses(
            RomanianVerbHomographPass(),
            FormOfLemmaPass(front_pos=frozenset(UPOS_ALLOWED)),
            RomanianPropnRecovery(),
        ),
    )
    return create_spaced_parser(config, **kwargs)
