"""Language post-passes for spaCy languages (spec §4.3) — generic, driven by per-language data.

Item 1 casing (``case_lemma``, ``tagging_copy``); item 2 separable-verb
reattachment (``SeparableVerbPass``, Task 6; the tokenizer stash is
``tokens.to_duck_tokens``); item 3 the enclitic ladder rung (``EncliticRung``);
the mined-form policy and the Latin lookup ladder (A §4.6, E.2.8).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

EMPTY_MAP: Mapping[str, str] = MappingProxyType({})

#: Curly and modifier apostrophes a subtitle uses where spaCy's English
#: exceptions expect ``'`` (A.2): ``I’m`` otherwise tags ``’m`` as a VERB.
APOSTROPHE_FOLD: Mapping[str, str] = MappingProxyType({"’": "'", "‘": "'", "ʼ": "'", "´": "'"})

_LETTER_RUN = re.compile(r"[^\W\d_]+")


def case_lemma(lemma: str, pos: str, title_case_pos: frozenset[str] = frozenset()) -> str:
    """§4.3 item 1: PROPN untouched; a ``title_case_pos`` class capitalised if lowercase; the rest lowered."""
    if pos == "PROPN":
        return lemma
    if pos in title_case_pos:
        return lemma[:1].upper() + lemma[1:] if lemma[:1].islower() else lemma
    return lemma.lower()


def is_all_caps_cue(text: str) -> bool:
    """At least two letter runs, some uppercase, no lowercase (``THE END``).

    ``ß`` does not count as lowercase: it has no one-character capital, so a
    shouted German line keeps it (``ICH WEIß ES NICHT``). Inert without ``ß``.
    """
    return (
        len(_LETTER_RUN.findall(text)) >= 2
        and any(char.isupper() for char in text)
        and not any(char.islower() and char != "ß" for char in text)
    )


def tagging_copy(text: str, char_map: Mapping[str, str] = EMPTY_MAP) -> str:
    """The string the model tags: same length as *text*, always.

    Each ``char_map`` entry is one character for one character, and an
    all-caps cue is lowercased character-wise only when that keeps the length
    (``İ`` lowercases to two code points). Surfaces are sliced from the
    ORIGINAL line by ``tok.idx``, so every offset stays valid (spec §4.2).
    """
    copy = "".join(char_map.get(char, char) for char in text) if char_map else text
    if is_all_caps_cue(copy):
        lowered = "".join(char.lower() for char in copy)
        if len(lowered) == len(copy):
            return lowered
    return copy


class SpacedMinedForm:
    """MinedFormPolicy: the card front is the (already cased) lemma, for every POS."""

    def mined_form(
        self,
        pos: str | None,
        orth_base: str,
        lemma: str,
        surface: str,
        pronunciation: str | None = None,
    ) -> str:
        return lemma or orth_base or surface

    def expression_tracks_surface(self, word: Any) -> bool:
        """S12 / Stage S D6: a lemma front never follows an i+1 swap's new surface."""
        return False

    def lookup_alternate(self, word: Any) -> str:
        """The ``orth_base`` the lookup-miss ladder receives: the token surface (en plan D7a).

        The card front is the lemma, so the default okurigana-safe lemma
        alternate would hand the ladder the front again and every surface rung
        (casefolded surface, the es/it enclitic strip) would be unreachable.
        Read by ``EpisodeProcessor._lookup_alternate`` through ``getattr``.
        """
        return str(getattr(word, "surface", "") or "")


#: A lookup rung over ``(mined_form, surface)``; ``surface`` may be ``""`` off the mining path.
Rung = Callable[[str, str], Iterable[str]]


class LatinLookupStrategy:
    """LookupStrategy (A §4.6, E.2.8): surface · casefolded surface · extra rungs · hyphen parts.

    ``word`` is the card front (the lemma, itself probed before this runs);
    ``orth_base`` is the token surface on the mining path
    (``SpacedMinedForm.lookup_alternate``) and the lemma or ``""`` elsewhere.
    ``conditions`` is 0 on every candidate (pure spelling variants). The probe
    word itself is never emitted and duplicates collapse to their first rung.
    Candidates are tried in order and the first hit wins, so every rung is
    already miss-only.
    """

    def __init__(self, extra_rungs: Sequence[Rung] = ()) -> None:
        self._extra_rungs = tuple(extra_rungs)

    def candidates(self, word: str, orth_base: str, ctype: str | None) -> list[tuple[str, int]]:
        del ctype  # duck tokens carry no cType
        out: list[tuple[str, int]] = []
        seen = {word}

        def add(text: str) -> None:
            if text and text not in seen:
                seen.add(text)
                out.append((text, 0))

        add(orth_base)
        add(orth_base.casefold())
        for rung in self._extra_rungs:
            for text in rung(word, orth_base):
                add(text)
        parts = [part for part in word.split("-") if part]
        if len(parts) > 1:
            add(parts[0])
            add(parts[-1])
        return out


_MIN_ENCLITIC_STEM = 2


@dataclass(frozen=True)
class EncliticRung:
    """§4.3 item 3: strip a trailing enclitic cluster from the SURFACE; offer the stem and its re-lemmatised forms.

    Lookup only (``conditions=0``); the card front stays the tokenizer's lemma
    (which may itself be mangled: es ``Dámelo`` lemmatises to ``dámelir``).
    ``clusters`` is the language's table (es/it); ``relemmatize`` maps a stem
    to infinitive candidates when the language has a rule for it. With no
    surface (a caller off the mining path) the mined form is stripped instead.
    """

    clusters: tuple[str, ...]
    relemmatize: Callable[[str], Sequence[str]] | None = None

    def __call__(self, word: str, surface: str) -> list[str]:
        source = (surface or word).casefold()
        out: list[str] = []
        for cluster in sorted(set(self.clusters), key=lambda c: (-len(c), c)):
            if len(source) - len(cluster) >= _MIN_ENCLITIC_STEM and source.endswith(cluster):
                stem = source[: -len(cluster)]
                out.append(stem)
                if self.relemmatize is not None:
                    out.extend(self.relemmatize(stem))
        return out
