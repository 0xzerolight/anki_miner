"""zeyrek 0.1.3 behind a deterministic analyzer (spec §4.4, B.1; the Stage F spike in the tr plan).

Imported only by ``tokenizer.build_tagger``, function-locally, so zeyrek loads on first use. Uses
``MorphAnalyzer._parse`` per word: the public ``analyze`` tokenizes with nltk's punkt data, which nobody downloads.

Three zeyrek bugs made stock analyses depend on history or on ``PYTHONHASHSEED``. Over zeyrek's own 9,984-word
``first-10K`` list, a second pass changed 322 words' analyses and 441 words ended with none; the hash seed changed
the lexicon (``adı`` lost every ``ad`` reading, ``tıbbı`` and ``zıddı`` every reading at all, under seed 2).

1. Lexicon build: ``TurkishMorphotactics`` stores the cached set on stem transitions and adds attributes to it, so
   every item sharing the cache entry inherits them. ``_uncached_phonetics`` points ``zeyrek.morphotactics`` at
   zeyrek's own undecorated function (``__wrapped__``): a fresh set per call.
2. Parse: ``RuleBasedAnalyzer.advance`` adds and discards attributes on the stem transition's own set, or on the
   cached result. ``_DeterministicAnalyzer`` ports ``search`` and ``advance`` (MIT, ``licenses/zeyrek/``) with a
   copy. The port drops upstream's per-result WARNING (``APPENDING RESULT``, which reached stderr through
   logging's last-resort handler) and its ``prune_cyclic_paths`` call, which can only raise.
3. Root attributes: ``generate_modified_root_nodes`` walks ``dict_item.attributes``, a plain ``set`` of
   ``RootAttribute``, and each attribute rewrites the root the next one reads. ``Enum.__hash__`` is
   ``hash(self._name_)``, so that walk follows ``PYTHONHASHSEED``: ``reddi`` analysed to the roots ``redd`` and
   ``ret`` under seed 0 but only ``redd`` under seed 2. ``_ordered_lexicon`` gives every item an
   ``_OrderedRootAttributes``, which iterates in declaration order - the order of Zemberek's own ``EnumSet``.

``analyse`` orders readings by ``_ranked`` (plan decision 2).
"""

from __future__ import annotations

import collections
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import zeyrek
import zeyrek.attributes
import zeyrek.morphotactics
from zeyrek.attributes import PhoneticAttribute, RootAttribute, calculate_phonetic_attributes
from zeyrek.lexicon import RootLexicon
from zeyrek.morphotactics import SurfaceTransition, generate_surface
from zeyrek.rulebasedanalyzer import RuleBasedAnalyzer

from anki_miner.languages.tr.morphology import TrAnalysis, front_spelling, tr_casefold, upos

#: Readings of a lowercased word with no capital evidence: ``bize`` → ``Bize``, ``mu`` → ``Mu``.
MARKED_SECONDARY_POS = frozenset({"Prop", "Abbrv"})
#: Closed-class roots win a morpheme-count tie: ``bana`` is ``ben``, not ``banmak`` + optative.
CLOSED_CLASS_POS = frozenset({"Pron", "Postp", "Conj", "Det", "Ques"})
_APOSTROPHES = str.maketrans({"’": "'", "ʼ": "'"})
_ROOT_ATTRIBUTE_ORDER = {attribute: index for index, attribute in enumerate(RootAttribute)}


def _uncached_phonetics() -> None:
    """Fix 1 (module docstring). Idempotent; zeyrek is used by nothing else in the app."""
    zeyrek.morphotactics.calculate_phonetic_attributes = zeyrek.attributes.calculate_phonetic_attributes.__wrapped__


class _OrderedRootAttributes(set[Any]):
    """Fix 3: a dictionary item's root attributes, iterated in ``RootAttribute`` declaration order."""

    __slots__ = ()

    def __iter__(self) -> Iterator[Any]:
        return iter(sorted(set.__iter__(self), key=_ROOT_ATTRIBUTE_ORDER.__getitem__))


def _ordered_lexicon() -> Any:
    """Fix 3: zeyrek's own lexicon, every item's attributes re-wrapped so their order is the declaration order."""
    lexicon = RootLexicon.default_text_dictionaries()
    for item in lexicon.item_set:
        item.attributes = _OrderedRootAttributes(item.attributes)
    return lexicon


class _DeterministicAnalyzer(RuleBasedAnalyzer):
    """Port of zeyrek 0.1.3 ``RuleBasedAnalyzer.search``/``advance`` (MIT, Olga Bulat) where every path owns its set."""

    def search(self, current_paths: list[Any]) -> list[Any]:
        result: list[Any] = []
        while current_paths:
            new_paths: list[Any] = []
            for path in current_paths:
                if (
                    not path.tail
                    and path.is_terminal
                    and PhoneticAttribute.CannotTerminate not in path.phonetic_attributes
                ):
                    result.append(path)
                    continue
                new_paths.extend(self.advance(path))
            current_paths = new_paths
        return result

    def advance(self, path: Any) -> list[Any]:
        new_paths: list[Any] = []
        for transition in path.current_state.outgoing:
            if not path.tail and transition.has_surface_form:
                continue
            surface = generate_surface(transition, path.phonetic_attributes)
            if not path.tail.startswith(surface) or not transition.can_pass(path):
                continue
            if not transition.has_surface_form:
                new_paths.append(path.copy(SurfaceTransition("", transition), path.phonetic_attributes))
                continue
            if path.tail == surface:
                attributes = set(path.phonetic_attributes)
            else:
                attributes = set(calculate_phonetic_attributes(surface, tuple(path.phonetic_attributes)))
            attributes.discard(PhoneticAttribute.CannotTerminate)
            last_token = transition.last_template_token
            if last_token.type_ == "LAST_VOICED":
                attributes.add(PhoneticAttribute.ExpectsConsonant)
            elif last_token.type_ == "LAST_NOT_VOICED":
                attributes.add(PhoneticAttribute.ExpectsVowel)
                attributes.add(PhoneticAttribute.CannotTerminate)
            new_paths.append(path.copy(SurfaceTransition(surface, transition), attributes))
        return new_paths


def _secondary(item: Any) -> str:
    value = item.secondary_pos.value
    return "" if value in (None, "Unk") else str(value)


class TurkishAnalyzer:
    """``analyse(word)``: every distinct reading of one word, the card-front choice first."""

    def __init__(self) -> None:
        _uncached_phonetics()
        self._zeyrek = zeyrek.MorphAnalyzer(lexicon=_ordered_lexicon())
        self._zeyrek.analyzer = _DeterministicAnalyzer(self._zeyrek.morphotactics)
        self._family = self._family_counts()

    def _family_counts(self) -> collections.Counter[str]:
        """How many of zeyrek's own ``first-10K`` surfaces have a reading with each dictionary item (decision 2.5)."""
        counts: collections.Counter[str] = collections.Counter()
        path = Path(zeyrek.__file__).parent / "resources" / "tr" / "first-10K"
        for line in path.read_text(encoding="utf-8").splitlines():
            word = line.strip()
            if word[:1].isalpha():
                counts.update({reading.dict_item.id_ for reading in self._zeyrek._parse(word)})
        return counts

    def _ranked(self, readings: list[Any]) -> list[Any]:
        """Plan decision 2: a total, hash-seed-free order."""
        content = {
            tr_casefold(front_spelling(r.dict_item.lemma, ""))
            for r in readings
            if r.dict_item.primary_pos.value != "Interj"
        }

        def key(reading: Any) -> tuple[bool, bool, int, bool, int, int, str, str]:
            item = reading.dict_item
            primary = item.primary_pos.value
            shadowed = primary == "Interj" and tr_casefold(front_spelling(item.lemma, "")) in content
            return (
                not (primary == "Ques" and not reading.ending),
                _secondary(item) in MARKED_SECONDARY_POS or shadowed,
                len(reading.morphemes),
                primary not in CLOSED_CLASS_POS,
                -self._family[item.id_],
                -len(reading.stem),
                item.lemma,
                item.id_,
            )

        return sorted(readings, key=key)

    def analyse(self, word: str) -> list[TrAnalysis]:
        out: list[TrAnalysis] = []
        for reading in self._ranked(self._zeyrek._parse(word.translate(_APOSTROPHES))):
            item = reading.dict_item
            secondary = _secondary(item)
            pos1 = upos(item.primary_pos.value, secondary)
            lemma = front_spelling(item.lemma, word)
            analysis = TrAnalysis(lemma if pos1 == "PROPN" else tr_casefold(lemma), pos1, secondary)
            if analysis not in out:
                out.append(analysis)
        return out
