"""Turkish sentence-splitter abbreviations (S8), generated from spaCy's tr tokenizer exceptions.

Every exception ending in a dot that holds a letter, ``str.casefold()``ed (the splitter's own fold, so ``İst.`` keys as
``i`` + U+0307 + ``st``), final dot dropped, minus ``TR_ABBREVIATION_DROPS``: the keys without an internal dot that
are among the 10,000 most frequent OpenSubtitles 2018 Turkish words (hermitdave ``tr_50k.txt``), except ``dr``. A drop
stays an ordinary word: ``Onu bul.`` ends its sentence. ``test_tr_abbreviations.py`` re-derives the set from spaCy, so
this file is regenerated, never hand-edited.
"""

from __future__ import annotations

TR_ABBREVIATION_DROPS: frozenset[str] = frozenset({"av", "bul", "kur", "max", "min", "sok", "tel"})

TR_ABBREVIATIONS: frozenset[str] = frozenset(
    {
        "a.b.d", "alb", "ank", "apt", "ar.gör", "arş.gör", "as.iz", "as.i\u0307z", "asb", "astsb", "bk", "bknz",
        "bnb", "bçvş", "böl", "bşk", "bştbp", "cad", "dak", "dk", "doç", "doğ", "dr", "drl", "dz", "dz.kuv",
        "dz.kuv.k", "dzl", "ecz", "ekon", "fak", "gn", "gn.kur", "gnkur", "gr", "hs.uzm", "hst", "huk", "hv",
        "hv.kuv", "hv.kuv.k", "hz", "hz.öz", "i\u0307ng", "i\u0307st", "jeol", "korg", "kur.bşk", "kuv", "ltd",
        "m.s", "m.ö", "mah", "müh", "onb", "ord", "org", "ped", "prof", "sb", "sn", "t.c", "tbp", "telg", "tic",
        "tug", "tuğg", "tümg", "tğm", "uzm", "vb", "vs", "y.mim", "y.müh", "yar", "yar.doç", "yard", "yard.doç",
        "yb", "yd.sb", "yrd", "yrd.doç", "yy", "çev", "çvş", "üni", "ütğm", "üçvş", "şb", "şti"
    }
)  # fmt: skip
