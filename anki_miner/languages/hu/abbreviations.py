"""Hungarian sentence-splitter abbreviations (S8), generated from spaCy's hu tokenizer exceptions.

Every exception ending in a dot that holds a letter, casefolded, final dot dropped, minus
``HU_ABBREVIATION_DROPS``: the keys without an internal dot that are ordinary words. Those are every such key among
the 10,000 most frequent OpenSubtitles 2018 Hungarian words (hermitdave ``hu_50k.txt``) except the real abbreviations
(dr kb mr mrs ms sz), every bare letter, and four rarer Hungarian words (elv folyt int szül). A drop stays an
ordinary word twice: ``Gyere be.`` ends its sentence, and the tokenizer (``build_spacy_tagger`` ``abbreviations``)
no longer glues ``be.`` or ``út.`` into one ``X`` token. ``test_hu_morphology.py`` re-derives the set from spaCy,
so this file is regenerated, never hand-edited.
"""

from __future__ import annotations

HU_ABBREVIATION_DROPS: frozenset[str] = frozenset(
    {
        "a", "adj", "all", "at", "b", "be", "bo", "c", "cal", "cia", "cső", "d", "de", "dj", "e", "ed", "elv", "em",
        "et", "f", "fej", "folyt", "ford", "g", "h", "hm", "ho", "i", "int", "j", "jan", "k", "kat", "km", "közt",
        "l", "m", "ma", "max", "min", "miss", "mo", "n", "no", "o", "old", "p", "phil", "q", "r", "red", "s",
        "szül", "t", "ti", "tv", "ty", "u", "v", "vas", "w", "x", "y", "z", "á", "ä", "é", "ész", "í", "ó", "ö",
        "ú", "út", "ü", "üdv", "ű"
    }
)  # fmt: skip

HU_ABBREVIATIONS: frozenset[str] = frozenset(
    {
        "a.c", "ac", "adm", "ag", "agit", "akh", "alez", "alk", "altbgy", "an", "ang", "arch", "atc", "aug", "aö",
        "b.a", "b.cs", "b.s", "b.sc", "b.ú.é.k", "bat", "bek", "belker", "berend", "bfok", "biz", "bizt", "bk",
        "bp", "br", "bros", "bsc", "bt", "btk", "btke", "btét", "ca", "cc", "cca", "cf", "cg", "cgf", "cgt", "cif",
        "co", "colo", "comp", "copr", "corp", "cos", "cs", "csc", "csop", "cstv", "csüt", "ctv", "ctvr", "dbj",
        "dd", "ddr", "dec", "dikt", "dipl", "dk", "dl", "dny", "dolg", "dr", "dsz", "du", "dzs", "ea", "eff",
        "egyh", "ek", "ell", "elvt", "eng", "eny", "etc", "eu", "ev", "ezr", "eü", "f.h", "f.é", "fam", "fb",
        "febr", "felv", "felügy", "ff", "ffi", "fhdgy", "fil", "fiz", "fla", "fm", "foglalk", "fp", "fpk", "fr",
        "frsz", "fszla", "fszt", "ft", "fuv", "főig", "főisk", "főszerk", "főtörm", "főv", "gazd", "gfv", "gimn",
        "gk", "gkv", "gm", "gmk", "gondn", "gr", "grav", "group", "gt", "gy", "gyak", "gyártm", "gör", "hads",
        "hallg", "hdm", "hdp", "hds", "hg", "hiv", "hk", "hksz", "hmvh", "honv", "hp", "hr", "hrsz", "hsz", "ht",
        "htb", "hv", "hőm", "i.e", "i.sz", "id", "ie", "ifj", "ig", "igh", "ill", "imp", "inc", "ind", "inform",
        "inic", "io", "ip", "ir", "irod", "isk", "ism", "izr", "iá", "jav", "jegyz", "jgmk", "jjv", "jkv", "jogh",
        "jogt", "jr", "jv", "jvb", "júl", "jún", "k.m.f", "karb", "kath", "kb", "kcs", "kd", "ker", "kf", "kft",
        "kg", "kht", "kir", "kirend", "kisip", "kiv", "kk", "kkt", "klin", "kong", "korm", "kp", "kr", "kr.e",
        "kr.u", "krt", "kt", "ktsg", "kult", "kv", "kve", "képv", "kísérl", "kóth", "könyvt", "körz", "köv", "közj",
        "közl", "közp", "kü", "lat", "lb", "ld", "legs", "lg", "lgv", "llc", "loc", "lt", "ltd", "ltp", "luth",
        "m.a", "m.s", "m.sc", "mass", "mat", "mb", "med", "megh", "met", "mf", "mfszt", "mh", "mjr", "mjv", "mk",
        "mlle", "mme", "mn", "mozg", "mr", "mrs", "ms", "msc", "mt", "má", "máj", "márc", "mé", "mélt", "mü", "műh",
        "műsz", "műv", "művez", "n.n", "nagyker", "nagys", "nat", "nb", "nbr", "neg", "nk", "nov", "nr", "nu", "ny",
        "nyh", "nyilv", "nyr", "nyrt", "nyug", "obj", "oj", "okl", "okt", "olv", "op", "orsz", "ort", "ov", "ovh",
        "p.h", "p.s", "pf", "pg", "ph.d", "phd", "pjt", "pk", "pl", "plb", "plc", "pld", "plur", "pol", "polg",
        "poz", "pp", "proc", "prof", "prot", "pság", "ptk", "pu", "pü", "r.k", "rac", "rad", "ref", "reg", "rer",
        "rev", "rf", "rkp", "rkt", "rt", "rtg", "röv", "s.b", "s.k", "sa", "salg", "sb", "sch", "sel", "sgt", "sm",
        "spa", "st", "stat", "stb", "strat", "stud", "sz", "szakm", "szaksz", "szakszerv", "szd", "szds", "szept",
        "szerk", "szf", "szfv", "szimf", "szjt", "szkv", "szla", "szn", "szolg", "szrt", "szt", "sztv", "szubj",
        "szvt", "számv", "szöv", "tanm", "tb", "tbk", "tc", "techn", "tek", "tel", "tf", "tgk", "tip", "tisztv",
        "titks", "tk", "tkp", "tny", "tp", "tszf", "tszk", "tszkv", "tvr", "tyr", "törv", "tü", "ua", "ui", "unit",
        "uo", "ut", "uv", "vb", "vcs", "vegy", "vh", "vhol", "vhr", "vht", "vill", "vizsg", "vk", "vkf", "vkny",
        "vm", "vol", "vs", "vsz", "vv", "vál", "várm", "vízv", "vö", "x.y", "zrt", "zs", "°c", "°f", "°k", "áe",
        "áht", "ált", "ápr", "ásv", "ék", "ény", "épt", "érk", "évf", "össz", "ötk", "özv", "ú.n", "új-z", "újz",
        "úm", "ún", "üag", "üd", "üe", "ümk", "ütk", "üv", "őrgy", "őrpk", "őrv"
    }
)  # fmt: skip
