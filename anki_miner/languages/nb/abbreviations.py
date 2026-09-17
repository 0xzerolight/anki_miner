"""Norwegian Bokmål sentence-splitter abbreviations (S8), generated from spaCy's nb tokenizer exceptions.

Every exception ending in a dot that holds a letter (spaCy's nb list plus its shared base exceptions), casefolded,
final dot dropped, minus ``NB_ABBREVIATION_DROPS``: the keys without an internal dot that are ordinary words — every
such key among the 10,000 most frequent OpenSubtitles 2018 Norwegian words (hermitdave ``no_50k.txt``) except the
real abbreviations (ca dr hr jr kl kr mill mr mrs nr pga pr st), and every bare letter. A drop stays an ordinary
word twice: ``Klokka er ti.`` ends its sentence, and the tokenizer (``build_spacy_tagger`` ``abbreviations``) no
longer glues ``ti.``, ``min.`` or ``jul.`` into one token. ``test_nb_morphology.py`` re-derives the set from spaCy,
so this file is regenerated, never hand-edited.
"""

from __future__ import annotations

NB_ABBREVIATION_DROPS: frozenset[str] = frozenset(
    {
        "a", "ad", "b", "bla", "c", "d", "e", "el", "et", "f", "g", "h", "i", "j", "jan", "jul",
        "k", "l", "lat", "m", "ma", "min", "n", "no", "o", "on", "p", "q", "r", "s", "sen", "sms",
        "t", "ti", "to", "u", "v", "w", "x", "y", "z", "ä", "ö", "ü",
    }
)  # fmt: skip

NB_ABBREVIATIONS: frozenset[str] = frozenset(
    {
        "a.m", "adm.dir", "ap", "apr", "aq", "aug", "b.c", "bl.a", "bm", "bnr",
        "bto", "c.c", "ca", "cand.mag", "chr", "co", "d.d", "d.m", "d.y", "dept",
        "des", "dr", "dr.med", "dr.philos", "dr.psychol", "dvs", "e.kr", "e.l", "eg", "ekskl",
        "etc", "etg", "ev", "evt", "f.eks", "f.kr", "f.o.m", "feb", "fhv", "fk",
        "foreg", "fork", "fr.p", "frp", "fv", "fvt", "gl", "gno", "gnr", "grl",
        "gt", "h.r.adv", "hhv", "hoh", "hr", "ifb", "ifm", "iht", "inkl", "istf",
        "jf", "jr", "jun", "juris", "kfr", "kgl", "kgl.res", "kl", "komm", "kr",
        "kr.f", "kst", "lø", "m.a.o", "m.fl", "m.m", "m.v", "mag.art", "mar", "md",
        "mfl", "mht", "mill", "mnd", "moh", "mr", "mrd", "mrs", "muh", "mv",
        "mva", "n.å", "ndf", "nov", "nr", "nto", "nyno", "o.a", "o.l", "off",
        "ofl", "okt", "op", "org", "osv", "ovf", "p.a", "p.g.a", "p.m", "p.t",
        "pb", "pga", "ph.d", "pkt", "pr", "pst", "pt", "red.anm", "ref", "res",
        "res.kap", "resp", "rv", "s.d", "s.k", "s.u", "s.å", "sep", "siviling", "snr",
        "sp", "spm", "sr", "sst", "st", "st.meld", "st.prp", "stip", "stk", "stud",
        "sv", "såk", "sø", "t.h", "t.o.m", "t.v", "temp", "tils", "tilsv", "tlf",
        "ult", "utg", "vedk", "vedr", "vg", "vgs", "vha", "vit.ass", "vn", "vol",
        "vs", "vsa", "°c", "°f", "°k", "årg", "årh",
    }
)  # fmt: skip
