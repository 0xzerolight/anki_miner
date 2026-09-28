"""Turkish sentence-splitter abbreviations (S8), generated from spaCy's tr tokenizer exceptions.

Every exception ending in a dot that holds a letter, ``str.casefold()``ed (the splitter's own fold, so ``İst.`` keys as
``i`` + U+0307 + ``st``), final dot dropped, minus ``TR_ABBREVIATION_DROPS``: the keys without an internal dot that
are among the 10,000 most frequent OpenSubtitles 2018 Turkish words (hermitdave ``tr_50k.txt``), except ``dr``. A drop
stays an ordinary word: ``Onu bul.`` ends its sentence. Plus ``TR_ABBREVIATION_ADDITIONS``, the day numbers ``1``-``31``
(the da DA8 shape): a Turkish ordinal is a number and a dot (``19. yüzyıl``, ``2. Dünya Savaşı``, ``3. kat``), and
the noun after it may be capitalised, so the splitter's lowercase-continuation rule does not cover it. Over the
review's two Turkish prose samples this takes the ordinal breaks from 7 to 0; the price is a sentence ending in a bare
day-range number running on into the next (``No 5. Orada.``). ``test_tr_abbreviations.py`` re-derives the set from
spaCy, so this file is regenerated, never hand-edited.
"""

from __future__ import annotations

TR_ABBREVIATION_DROPS: frozenset[str] = frozenset({"av", "bul", "kur", "max", "min", "sok", "tel"})

TR_ABBREVIATION_ADDITIONS: frozenset[str] = frozenset(str(day) for day in range(1, 32))

TR_ABBREVIATIONS: frozenset[str] = frozenset(
    {
        "1", "10", "11", "12", "13", "14", "15", "16", "17", "18", "19", "2", "20", "21", "22", "23", "24", "25", "26",
        "27", "28", "29", "3", "30", "31", "4", "5", "6", "7", "8", "9",
        "a.b.d", "alb", "ank", "apt", "ar.gör", "arş.gör", "as.iz", "as.i\u0307z", "asb", "astsb", "bk", "bknz",
        "bnb", "bçvş", "böl", "bşk", "bştbp", "cad", "dak", "dk", "doç", "doğ", "dr", "drl", "dz", "dz.kuv",
        "dz.kuv.k", "dzl", "ecz", "ekon", "fak", "gn", "gn.kur", "gnkur", "gr", "hs.uzm", "hst", "huk", "hv",
        "hv.kuv", "hv.kuv.k", "hz", "hz.öz", "i\u0307ng", "i\u0307st", "jeol", "korg", "kur.bşk", "kuv", "ltd",
        "m.s", "m.ö", "mah", "müh", "onb", "ord", "org", "ped", "prof", "sb", "sn", "t.c", "tbp", "telg", "tic",
        "tug", "tuğg", "tümg", "tğm", "uzm", "vb", "vs", "y.mim", "y.müh", "yar", "yar.doç", "yard", "yard.doç",
        "yb", "yd.sb", "yrd", "yrd.doç", "yy", "çev", "çvş", "üni", "ütğm", "üçvş", "şb", "şti"
    }
)  # fmt: skip
