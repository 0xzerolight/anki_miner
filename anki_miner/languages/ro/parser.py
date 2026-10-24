"""Romanian SubtitleParser factory: the shared spaced factory plus four front repairs from wty-ro-en's form rows.

wty-ro-en keys a form row without diacritics and stores its spelling as the reading, and the profile's keys
(``RomanianDictKeys``) let the form lookup match it. Every pass reads the rows through ``ro_row_targets``: kaikki also
files common forms as headwords whose every gloss only names the inflection (``vorbim``: "first-person plural present
indicative of vorbi: we speak", ``fii``: "plural of fiu", "second-person singular imperative of fi"), and read as
headwords they fronted the form itself. Such a row is the form row it describes; on three episodes of a TV series
(2,400 ASR cues) that moves 11 fronts, all right (``vorbești``, ``vorbim``, ``urăști``, ``Fiți``, ``bați``). The four
passes run in this order:

* ``RomanianHomographPass``: the model lemmatises a word to a look-alike wty-ro-en files only in other classes (``Pot``
  -> the noun ``pot``, ``vin`` -> "wine", ``noaptea`` -> the adverb "at night"), and the form-of repair never
  second-guesses a headword. Such a token takes the one lemma its surface's form rows name, when that lemma has a row
  of the token's class: ``Pot`` -> ``putea``, ``sun`` -> ``suna``, ``noaptea`` -> ``noapte``, ``Bună`` (an
  interjection headword) -> ``bun``. Several such lemmas keep the token, since picking the verb among them made
  ``caise`` ``căi``, the adjective ``vii`` ``veni`` and ``deșteaptă`` ``deștepta``. The tagger's own lemma counts
  among them: ``Taci`` names ``tacă`` (silence) and ``tăcea``, and not counting ``tacă`` would front ``tăcea``, but
  the same reading made ``dragă domnule`` ``draga`` and ``ați atins limita`` the verb ``limita``. On 386 ordinary
  lines 12 fronted a verb as such a headword, then 4 (``Ia``, whose rows also name the obsolete ``ii``, and ``vii``).
  Widened from verbs to every content class, it moves 21 fronts on the 2,400 cues, 20 right and one in a garbled cue.
* ``FormOfLemmaPass`` over every content class (``_spaced/form_of.py``): a content token whose lemma is no headword
  takes the headword the form row names, and that row's class is the new ``pos1``. The model leaves ``Lasă``,
  ``cheile`` and ``gările`` inflected, strips ``tată`` to ``tat`` and ``aseară`` to ``asear``, while wty-ro-en
  files each as a form of one headword (``lăsa``, ``cheie``, ``gară``) or as a headword itself. The ``{VERB}``
  scope it ran with before was copied from fr/it with no Romanian reason: on 1,124 lines widening it changes 91
  fronts and none is wrong. The lemmatised frequency list re-keys through the same pass (``școală`` 1013 -> 495).
* ``RomanianNonHeadwordFront``: what the content pass leaves on a lemma wty-ro-en files as no headword, or only as a
  name. The surface's form rows name several lemmas (``scapi``: the noun ``scap`` and ``scăpa``; ``ții``: ``ținea``,
  itself a form, and ``ține``), or its only headword is one no card fronts (``uite``, an interjection; ``sucești``, a
  village), and the front was a non-word (``ția``, ``scapi``, ``uit``). A surface filed as a content word is the
  front; else the one target filed as one, else the one of the token's class: ``scăpa``, ``ține``, ``uita``,
  ``suci``. Two of the token's class (``vina``: ``veni`` and ``vâna``) keep it, and so does a lemma filed as a
  pronoun, preposition or conjunction (``peste``, ``tu`` for ``Voi``): the word is right and only its tag is wrong.
  On the 2,400 cues it moves 55 fronts: 47 right, 3 in garbled cues, and 5 to a real word of the class the tagger got
  wrong (``muște`` "flies" tagged VERB -> ``mușca``, ``deșteaptă`` -> ``deștepta``), each a non-word before. On the
  review's fresh dialogue and prose, 7 of 8 right (``deșteaptă`` again).
* ``RomanianPropnRecovery``: ``ro_core_news_sm`` tags many cue-initial verbs and nouns PROPN (``Aștept``,
  ``Fugi``, ``Mulțumim``, ``Mama``, ``Iarna``), which the content gate drops. The line's first word and a
  lowercase PROPN take a lowercase front no ``name`` row files (``admit_front``, el's rule); a capital mid-line
  is a name whatever its rows say (``Delta Dunării``, ``Banat`` and ``Viorel`` would front ``deltă``, ``banat`` and
  the adjective ``viorel``). It is its own pass, after the content one, because there the class filter hides the
  name rows from the gate and ``Ion`` or ``Sorin`` would become nouns. It recovers 27 words right, 1 wrong
  (``Jur că`` -> the noun ``jur``) and none of ~60 line-initial names; a mid-line ``Mama`` stays dropped.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

from anki_miner.languages._spaced.form_of import (
    GLOSS_ITEM_RE,
    WTY_TAG_TO_UPOS,
    FormOfLemmaPass,
    OrderedPasses,
    form_targets,
    is_lemma_row,
    rendered_text,
)
from anki_miner.languages._spaced.pos import UPOS_ALLOWED

if TYPE_CHECKING:  # annotation-only: services must not load at profile build
    from anki_miner.services.morphology import AttestLookup, FormLookup

_CONTENT = frozenset(UPOS_ALLOWED)
_PROPN = frozenset({"PROPN"})

#: A gloss that only names an inflection and its lemma: "first-person plural present indicative of vorbi: we
#: speak", "plural of fiu: sons", "third-person singular simple perfect indicative of arma".
_INFLECTION_GLOSS = re.compile(
    r"(?:(?:first|second|third)-person|singular|plural|(?:in)?definite|masculine|feminine|neuter|nominative|"
    r"accusative|genitive|dative|vocative|imperative|infinitive|gerund|supine|(?:present|past) participle)\b"
    r"[^:;]*? of ([^\s:;,()]+)",
    re.IGNORECASE,
)

Row = tuple[str, str]


def ro_row_targets(content: str, tags: str) -> list[str] | None:
    """wty-ro-en's row reading: ``None`` for a headword row, else the lemmas the row names.

    A form row names its targets; a headword row every gloss of which is an inflection names the lemmas those glosses
    end on (module docstring). A real headword row opens with its Grammar and Etymology, and none of its glosses
    reads that way.
    """
    if not is_lemma_row(tags):
        return form_targets(content)
    glosses = [rendered_text(item) for item in GLOSS_ITEM_RE.findall(content)]
    named = [match.group(1).rstrip(".") for match in map(_INFLECTION_GLOSS.match, glosses) if match]
    return named if glosses and len(named) == len(glosses) else None


def _classes(rows: Iterable[Row]) -> set[str | None]:
    """The classes of the headword rows among *rows*, as UPOS (``None`` for a first tag naming no class)."""
    return {WTY_TAG_TO_UPOS.get(tags.split(" ")[0]) for content, tags in rows if ro_row_targets(content, tags) is None}


def _targets(rows: Iterable[Row]) -> list[str]:
    """The lemmas the form rows among *rows* name, first seen first."""
    return list(dict.fromkeys(target for content, tags in rows for target in ro_row_targets(content, tags) or ()))


class RomanianHomographPass:
    """``token_post_pass``: a word lemmatised to a headword of other classes only takes the one other lemma its surface
    names, when that lemma has a row of the token's class."""

    def __call__(self, tokens: list[Any], attest: AttestLookup | None, forms: FormLookup | None) -> list[Any]:
        del attest
        if forms is None:
            return tokens
        content = [token for token in tokens if token.feature.pos1 in _CONTENT]
        if not content:
            return tokens
        rows = forms(
            list(dict.fromkeys(key for token in content for key in (token.feature.lemma, token.surface.lower())))
        )
        picks: list[tuple[Any, str]] = []
        for token in content:
            classes = _classes(rows.get(token.feature.lemma, ()))
            if not classes or token.feature.pos1 in classes:
                continue
            targets = _targets(rows.get(token.surface.lower(), ()))
            if len(targets) == 1:
                picks.append((token, targets[0]))
        if not picks:
            return tokens
        target_rows = forms(list(dict.fromkeys(target for _token, target in picks)))
        for token, target in picks:
            if token.feature.pos1 in _classes(target_rows.get(target, ())):
                token.feature.lemma = target
        return tokens


class RomanianNonHeadwordFront:
    """``token_post_pass`` after the content one: a content word on a lemma filed as no content word takes the one
    target its surface (else its lemma) names that is filed as one, else the one of its own class."""

    def __call__(self, tokens: list[Any], attest: AttestLookup | None, forms: FormLookup | None) -> list[Any]:
        del attest
        if forms is None:
            return tokens
        content = [token for token in tokens if token.feature.pos1 in _CONTENT]
        if not content:
            return tokens
        rows = forms(
            list(dict.fromkeys(key for token in content for key in (token.surface.lower(), token.feature.lemma)))
        )
        # A lemma filed as a pronoun, preposition or conjunction is that word (``peste``, ``tu`` for ``Voi``) with a
        # wrong tag, not a non-word: only a lemma with no headword, or a name only, is repaired.
        loose = [token for token in content if not _classes(rows.get(token.feature.lemma, ())) - {"PROPN", None}]
        picks: list[tuple[Any, list[str]]] = []
        for token in loose:
            surface = token.surface.lower()
            if _classes(rows.get(surface, ())) & _CONTENT:
                picks.append((token, [surface]))  # the surface is itself the word: ``tată`` for the lemma ``ta``
                continue
            targets = next(
                (found for key in (surface, token.feature.lemma) if (found := _targets(rows.get(key, ())))), None
            )
            if targets:
                picks.append((token, targets))
        if not picks:
            return tokens
        rows.update(
            forms([target for target in dict.fromkeys(t for _token, ts in picks for t in ts) if target not in rows])
        )
        for token, targets in picks:
            front = self._pick(token.feature.pos1, targets, rows)
            if front is not None:
                token.feature.lemma, token.feature.pos1 = front
        return tokens

    @staticmethod
    def _pick(pos1: str, targets: list[str], rows: Mapping[str, list[Row]]) -> tuple[str, str] | None:
        """The one target filed as a content word, else the one of class *pos1*, and the class it fronts in."""
        words = {target: _classes(rows.get(target, ())) & _CONTENT for target in targets}
        words = {target: classes for target, classes in words.items() if classes}
        if len(words) > 1:
            words = {target: classes for target, classes in words.items() if pos1 in classes}
        if len(words) != 1:
            return None
        ((front, classes),) = words.items()
        if pos1 in classes:
            return front, pos1
        first = next(
            WTY_TAG_TO_UPOS[tags.split(" ")[0]]
            for content, tags in rows[front]
            if ro_row_targets(content, tags) is None and WTY_TAG_TO_UPOS.get(tags.split(" ")[0]) in _CONTENT
        )
        return front, first


def admit_front(token: Any, front: str, heads: Sequence[tuple[str, str]]) -> bool:
    """A recovered PROPN token takes only a lowercase front no ``name`` row files (el ``admit_front``).

    ``Ion`` is a noun (ion) as well as a name, and the name row keeps it PROPN.
    """
    del token
    return not front[:1].isupper() and not any("name" in tags.split(" ") for _content, tags in heads)


class RomanianPropnRecovery:
    """``token_post_pass``: the form-of repair over the line's first word and its lowercase words tagged PROPN."""

    def __init__(self) -> None:
        self._repair = FormOfLemmaPass(recover_pos=_PROPN, accept=admit_front, row_targets=ro_row_targets)

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
            RomanianHomographPass(),
            FormOfLemmaPass(front_pos=_CONTENT, row_targets=ro_row_targets),
            RomanianNonHeadwordFront(),
            RomanianPropnRecovery(),
        ),
    )
    return create_spaced_parser(config, **kwargs)
