"""Swedish sentence-splitter abbreviations (S8), generated from spaCy's sv tokenizer exceptions.

Every exception ending in a dot that holds a letter, casefolded, final dot dropped, minus
``SV_ABBREVIATION_DROPS``, plus ``SV_ABBREVIATION_ADDITIONS`` (common abbreviations the list lacks). The drops are
the dotless keys that are ordinary Swedish words, checked against the 10,000 most frequent OpenSubtitles 2018
Swedish words (``el``, ``kor``, ``lat``, ``max``, ``min``, ``mån``, ``mos``, ``sid``, ``ung``, ``jan``, ``doc``)
plus every bare letter -- ``i.`` and ``m.`` are a preposition and a unit in running text. A drop stays an ordinary
word twice: ``Hon är ung.`` ends its sentence, and the tokenizer (``build_spacy_tagger`` ``abbreviations``) no
longer glues ``ung.`` into one token. ``test_sv_morphology.py`` re-derives the set from spaCy, so this file is
regenerated, never hand-edited.
"""

from __future__ import annotations

SV_ABBREVIATION_DROPS: frozenset[str] = frozenset(
    set("abcdefghijklmnopqrstuvwxyzäöü") | {"doc", "el", "jan", "kor", "lat", "max", "min", "mån", "mos", "sid", "ung"}
)
SV_ABBREVIATION_ADDITIONS: frozenset[str] = frozenset({"ca", "mr", "mrs", "ms", "nr", "st"})

SV_ABBREVIATIONS: frozenset[str] = frozenset(
    {
        "ang", "anm", "apr", "aug", "bl.a", "ca", "d.v.s", "dec", "dr", "dvs", "e.d", "e.kr", "eng", "etc",
        "ev", "exkl", "f.d", "f.kr", "f.n", "f.ö", "feb", "febr", "fid", "fig", "forts", "fr.o.m", "fre",
        "förf", "h.k.h", "h.m", "inkl", "iofs", "jun", "jur", "kap", "kl", "kr", "kungl", "lör", "m.a.o",
        "m.fl", "m.m", "milj", "mr", "mrs", "ms", "mt", "mvh", "nov", "nr", "o.d", "o.s.v", "obs", "okt",
        "ons", "osv", "p.g.a", "ph.d", "proc", "prof", "ref", "resp", "s.a.s", "s.k", "s.t", "sep", "sept",
        "st", "t.ex", "t.h", "t.o.m", "t.v", "tel", "tis", "tors", "vol", "°c", "°f", "°k", "äv", "övers",
    }
)  # fmt: skip
