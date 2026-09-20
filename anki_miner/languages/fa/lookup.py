"""Ordered spelling fallbacks for a Persian lookup miss (the profile's ``lookup``).

Six rungs, five of which are pure string work plus the committed colloquial
table: the ladder has to answer on a fresh install, because the language
contract test and the settings surfaces both call it before any pack exists.

Rung 2 is the one that earns the ladder its keep: the wty Persian build's 4,655
mi-prefixed redirect rows carry NO ZWNJ (miraftam exists, mi-raftam does not),
so a card mined with the ZWNJ never matches them without it (plan probe P-7).
"""

from __future__ import annotations

import csv
from functools import lru_cache
from importlib.resources import files

ZWNJ = "\N{ZERO WIDTH NON-JOINER}"
_YEH = "\N{ARABIC LETTER FARSI YEH}"
_HEH = "\N{ARABIC LETTER HEH}"
_HAMZA_ABOVE = "\N{ARABIC HAMZA ABOVE}"
_HEH_WITH_YEH = "\N{ARABIC LETTER HEH WITH YEH ABOVE}"

#: Hard cap on what one miss may ask the providers for.
MAX_CANDIDATES = 10
#: "Pure spelling variant, no deinflection constraint" -- the value
#: ``DefinitionService._fallback_candidates`` emits for an orth_base.
CONDITIONS = 0


@lru_cache(maxsize=1)
def _colloquial() -> dict[str, str]:
    """The committed informal-to-formal table, read once.

    Package data, not pack data: the 432 rows ship in the wheel and the bundle,
    so this rung works before any download.
    """
    text = (files("anki_miner.languages.fa.data") / "colloquial.tsv").read_text(encoding="utf-8")
    pairs: dict[str, str] = {}
    for row in csv.reader(text.splitlines(), delimiter="\t"):
        if row and not row[0].startswith("#") and len(row) == 3:
            pairs.setdefault(row[0], row[1])
    return pairs


def _ezafe_stripped(word: str) -> str | None:
    """xane-ye / xaneye / xane' -> xane: the three ezafe spellings."""
    if word.endswith(ZWNJ + _YEH) or word.endswith(_HEH + _YEH):
        return word[:-1].rstrip(ZWNJ)
    if word.endswith(_HAMZA_ABOVE):
        return word[:-1]
    if word.endswith(_HEH_WITH_YEH):
        return word[:-1] + _HEH
    return None


class PersianLookupStrategy:
    """LookupStrategy for Persian: spelling variants only, conditions 0."""

    def candidates(self, word: str, orth_base: str, ctype: str | None) -> list[tuple[str, int]]:
        """Ordered fallbacks for *word*; never *word* itself, never a duplicate."""
        from anki_miner.languages.fa.tokenizer import active_lexicon

        found: list[str] = []
        if orth_base:
            found.append(orth_base)
        if ZWNJ in word:
            found.append(word.replace(ZWNJ, ""))
            found.append(word.replace(ZWNJ, " "))
        formal = _colloquial().get(word)
        if formal:
            found.append(formal)
        ezafe = _ezafe_stripped(word)
        if ezafe:
            found.append(ezafe)

        # The last rung needs the verb tables. It uses the lexicon a parse has
        # already built and never builds one: a definition lookup must not pull
        # 14 MB of tables into a process that has parsed nothing.
        lexicon = active_lexicon()
        if lexicon is not None:
            infinitive = lexicon.verb_form(word)
            if infinitive is not None:
                stem = lexicon.present_stem(infinitive)
                if stem:
                    found.append(stem)

        out: list[tuple[str, int]] = []
        seen = {word}
        for candidate in found:
            if candidate and candidate not in seen:
                seen.add(candidate)
                out.append((candidate, CONDITIONS))
            if len(out) == MAX_CANDIDATES:
                break
        return out
