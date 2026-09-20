"""Slovenian abbreviations: the source of ``SL_ABBREVIATIONS`` (spec S8).

Derived, not hand-picked: every dotted stem of spaCy 3.8's Slovenian tokenizer exceptions
(``spacy/lang/sl/tokenizer_exceptions.py``, MIT) - 1,006 of them, casefolded with the final dot
dropped - minus ``SL_ORDINARY_WORDS``. D8's hand-written sets are hr's and lt's; Slovenian has an
upstream list, so it is derived, and ``tests/unit/languages/test_sl_abbreviations.py`` re-derives it.

``build_spacy_tagger(abbreviations=...)`` prunes every single-dot tokenizer rule whose stem is not
in this set, so a stem that is an ordinary Slovene word must be cut or the word disappears at a
sentence end (NOTE 013): ``je`` is rank 1 of hermitdave's OpenSubtitles 2018 ``sl_50k.txt``, ``in``
rank 8, ``film`` rank 845. The same set is the sentence splitter's.

``SL_ORDINARY_WORDS`` is that cut, on two signals because neither alone is enough: a stem is
ordinary when wty-sl-en 2026.09.19 lists it as a term OR ``sl_50k.txt`` ranks it inside the top
2,000. The dictionary is the thinnest of the wave (54,245 terms) and misses everyday loanwords the
frequency list catches (``film`` 845, ``ok`` 370, ``test`` 1,891); the frequency list ranks real
abbreviations highly because subtitles write them (``gdc`` 688), so it cannot be used alone either.
178 cut, 828 kept. The threshold is pinned by its own boundary: the last cut stem is ``san`` at
1,996 and the first kept abbreviation is ``dr`` at 2,966, so 3,000 would lose ``dr``.

Checked on UD Slovenian-SSJ dev+test with the shipped set: 42 dotted tokens tag as abbreviations and
every one is real (``dr.`` 5, ``oz.`` 5, ``str.`` 4, ``npr.`` 3, ``st.`` 3, ``itd.`` 3, ``angl.``
3); no ordinary word is swallowed. The cost of the cut is a sentence split after ``g.``, ``ga.`` and
the spaced ``t. i.`` - ``ga`` is rank 22 ("him") and ``i`` rank 977, so keeping them would swallow
both at a sentence end. ``t.i.`` written closed is a multi-dot exception, which the rule surgery
never touches, and stays whole either way.
"""

from __future__ import annotations

#: The stems cut from the spaCy list: a wty-sl-en term, or inside the top 2,000 of sl_50k.txt.
#: Cutting one costs a sentence split after that abbreviation; keeping one costs the word itself at
#: every sentence end. The asymmetry is why the rule is generous.
SL_ORDINARY_WORDS: frozenset[str] = frozenset(
    {
        "a", "al", "arh", "as", "b", "c", "d", "daj", "dan", "ded", "del", "do", "dol", "duh", "e", "egipt",
        "em", "f", "film", "fin", "franc", "friz", "g", "ga", "gal", "gdč", "gen", "glas", "gor", "gost",
        "gozd", "grad", "h", "i", "in", "islam", "iz", "j", "jak", "jam", "je", "jug", "jur", "k", "kat", "kdo",
        "kip", "kit", "kmet", "kol", "kom", "kost", "kraj", "l", "les", "let", "log", "m", "mak", "mar",
        "mater", "max", "med", "medic", "meh", "mest", "metal", "mi", "mil", "mlad", "moj", "n", "na", "nad",
        "nam", "nem", "nik", "no", "nom", "nov", "o", "ob", "obraz", "od", "ok", "os", "p", "papir", "par",
        "past", "ped", "pet", "po", "pod", "pogoj", "pol", "poljub", "pomen", "por", "prav", "pravopis", "pred",
        "psih", "r", "rad", "red", "rep", "rež", "rib", "rim", "romun", "rus", "s", "sam", "san", "ser", "sh",
        "sin", "slov", "slovak", "slovan", "so", "sod", "srb", "sred", "sta", "star", "ste", "stol", "stroj",
        "strok", "svet", "t", "ted", "teh", "tek", "ter", "test", "tim", "tip", "tolmač", "tom", "trg", "tu",
        "u", "um", "umet", "un", "ur", "us", "v", "val", "var", "ven", "vest", "vez", "vol", "w", "y", "z",
        "zak", "zal", "á", "ä", "é", "ö", "ü", "ć", "č", "čas", "češ", "đ", "š", "šah", "šved", "ž", "živ",
        "žival",
    }
)  # fmt: skip

#: The S8 abbreviation set: the sentence splitter's, and the one source of truth for which of
#: spaCy's single-dot tokenizer rules survive (``_spaced/tokenizer.py::_prune_dotted_rules``).
SL_ABBREVIATIONS: frozenset[str] = frozenset(
    {
        "aa", "ab", "abc", "abit", "abl", "abs", "abt", "acc", "accel", "add", "adj", "adm", "adv", "aer",
        "aet", "afr", "agr", "akad", "alban", "all", "alleg", "alp", "alt", "alter", "alžir", "am", "amer",
        "an", "anat", "andr", "ang", "angl", "anh", "anon", "ans", "ant", "antr", "antrop", "apoc", "app",
        "approx", "apr", "apt", "ar", "arab", "arc", "arch", "arheol", "arhit", "arr", "asist", "assist",
        "assoc", "asst", "astr", "attn", "aug", "avg", "avstr", "avstral", "avt", "az", "bab", "bal", "bbl",
        "bd", "belg", "bibl", "bioinf", "biokem", "biol", "biomed", "bk", "bl", "bn", "bolg", "borg", "bot",
        "bp", "br", "braz", "brit", "bros", "broš", "bt", "bu", "ca", "cal", "can", "cand", "cantab", "cap",
        "capt", "cat", "cath", "cc", "cca", "cd", "cdr", "cdre", "cent", "cerkv", "cert", "cf", "cfr", "ch",
        "chap", "chem", "chr", "chs", "cic", "circ", "cit", "civ", "cl", "cm", "cmd", "cnr", "co", "cod", "col",
        "coll", "colo", "com", "comp", "con", "conc", "cond", "conn", "cons", "cont", "coop", "corr", "cost",
        "cp", "cpl", "cr", "crd", "cres", "cresc", "ct", "cu", "d.d", "d.n.o", "d.o.o", "dat", "davč", "ddr",
        "dec", "def", "dem", "dent", "dept", "dia", "dip", "dipl", "dir", "disp", "diss", "div", "doc", "dok",
        "doo", "dop", "dott", "dr", "dram", "druž", "družb", "drž", "dt", "dur", "dvr", "dwt", "ea", "ecc",
        "eccl", "eccles", "econ", "ed", "edn", "egr", "ekon", "eksp", "el", "enc", "eng", "eo", "ep", "err",
        "esp", "esq", "est", "et", "etc", "etn", "etnogr", "etnol", "ev", "evfem", "evr", "ex", "exc", "excl",
        "exp", "expl", "ext", "exx", "fa", "facs", "fak", "faks", "farm", "fas", "fasc", "fco", "fcp", "feb",
        "febr", "fec", "fed", "fem", "ff", "fff", "fid", "fig", "fil", "filat", "filoz", "fiz", "fiziol",
        "fiziot", "flam", "fm", "fo", "fol", "folk", "fot", "fr", "frag", "fran", "fsc", "gastr", "ge", "geod",
        "geog", "geogr", "geol", "geom", "geotehnol", "germ", "gg", "gimn", "gl", "glag", "glasb", "glav",
        "gled", "gnr", "go", "gosp", "gp", "gr", "graf", "gram", "gren", "grš", "gs", "hab", "hebr", "hf",
        "hist", "ho", "hort", "hrv", "ia", "ib", "ibid", "id", "ide", "idr", "idridr", "igr", "ill", "im",
        "imen", "imp", "impf", "impr", "inc", "incl", "ind", "indus", "inf", "inform", "ing", "init", "ins",
        "int", "inv", "inšp", "inštr", "inž", "ipd", "iron", "is", "ist", "it", "ital", "itd", "itn", "iur",
        "izbr", "izd", "izg", "izgr", "izr", "izv", "jan", "jap", "jav", "jez", "jr", "jsl", "jud",
        "jugoslovan", "jul", "jun", "juž", "jv", "jz", "kal", "kan", "kand", "kem", "knj", "knjiž", "komp",
        "konf", "kont", "kor", "kov", "kp", "kpfw", "kr", "krat", "kub", "kult", "kv", "kval", "l.r", "la",
        "lab", "lat", "lb", "ld", "lib", "lik", "lingv", "lit", "litt", "lj", "ljubk", "ljud", "ll", "loc",
        "lov", "loč", "lt", "ma", "madž", "mag", "manag", "manjš", "masc", "mass", "mat", "maxmax", "mb", "md",
        "mdr", "mech", "medij", "medn", "mehč", "mem", "menedž", "mes", "mess", "meteor", "meteorol", "mex",
        "mikr", "min", "minn", "mio", "misc", "miss", "mit", "mitol", "mk", "mkt", "ml", "mlle", "mlr", "mm",
        "mme", "mn", "množ", "mo", "mont", "moš", "možn", "mr", "mrd", "mrs", "ms", "msc", "msgr", "mt", "murr",
        "mus", "mut", "muz", "nadalj", "nadom", "nagl", "nakl", "namer", "nan", "naniz", "nar", "nasl", "nat",
        "nav", "navt", "nač", "ned", "nedol", "nedov", "neprav", "nepreh", "neskl", "nestrok", "nizoz", "nm",
        "nn", "norv", "notr", "novogr", "npr", "ns", "num", "obd", "obj", "obl", "oblač", "oblik", "obr",
        "obrt", "obs", "obst", "obt", "obč", "oc", "oct", "odd", "odg", "odn", "odst", "odv", "oec", "off",
        "okla", "okr", "okt", "ont", "oo", "op", "opis", "opp", "opr", "or", "orch", "ord", "ore", "oreg",
        "org", "orient", "orig", "ork", "ort", "oseb", "osn", "ot", "otr", "oz", "ozir", "ošk", "pag", "pal",
        "para", "parc", "parl", "part", "pat", "pdk", "pen", "perf", "pert", "perz", "pesn", "pev", "pf", "pfc",
        "ph", "pharm", "phil", "pis", "pisar", "pl", "podaljš", "podr", "pog", "pogl", "pojm", "pok", "pokr",
        "polit", "polj", "poljed", "poljud", "polu", "pom", "pon", "ponov", "pop", "port", "pos", "posl",
        "posn", "pov", "pp", "ppl", "pr", "praet", "pravosl", "preb", "predl", "predm", "predp", "preds",
        "pref", "preg", "pregib", "preh", "prel", "prem", "premen", "pren", "prep", "pres", "pret", "prev",
        "pribl", "prid", "prih", "pril", "prim", "primerj", "primor", "prip", "pripor", "prir", "prisl",
        "prist", "priv", "proc", "prof", "prog", "proiz", "prom", "pron", "prop", "prot", "protest", "prov",
        "ps", "psiht", "pss", "pt", "publ", "pz", "q", "qld", "qu", "quad", "que", "racc", "rastl", "razgl",
        "razl", "razv", "rač", "rd", "ref", "reg", "rel", "relig", "repr", "rer", "resp", "rest", "ret", "rev",
        "revol", "rist", "rkp", "rm", "rod", "roj", "rom", "rp", "rr", "rt", "rud", "ruš", "ry", "s.p", "sal",
        "samogl", "sc", "scen", "sci", "scr", "sdv", "sed", "seg", "sek", "sen", "sep", "sept", "sev", "sg",
        "sgt", "sig", "sigg", "sign", "sim", "sing", "sinh", "skand", "skl", "sklad", "sklanj", "sklep", "skr",
        "sl", "slabš", "slik", "slovaš", "slovn", "sn", "sob", "soc", "sociol", "sopomen", "sopr", "sor", "sov",
        "sovj", "sp", "spec", "spl", "spr", "spreg", "sq", "sr", "sre", "sredoz", "srh", "ss", "ssp", "st",
        "stan", "stanstar", "stcsl", "stil", "stim", "stom", "str", "stsl", "stud", "sup", "supl", "suppl",
        "sv", "sz", "t.i", "t.j", "tab", "tech", "tehn", "tehnol", "teks", "tekst", "tel", "temp", "ten",
        "teol", "term", "th", "theol", "tisk", "tisočl", "tit", "tj", "tl", "tol", "tor", "tov", "tož", "tr",
        "trad", "traj", "trans", "tren", "trib", "tril", "trop", "trp", "trž", "ts", "tt", "tur", "turiz",
        "tvor", "tvorb", "tč", "ukr", "ul", "univ", "up", "upr", "urad", "ust", "utr", "va", "varn", "vel",
        "ver", "verb", "vet", "vezal", "vic", "vis", "viv", "viz", "viš", "vn", "vod", "voj", "vok", "vpr",
        "vrst", "vrstil", "vrtn", "vs", "vulg", "vv", "vzd", "vzg", "vzh", "vznes", "vzor", "wed", "wg", "wk",
        "x", "zah", "zaim", "zap", "zasl", "zastar", "zavar", "zač", "zb", "združ", "zg", "zgod", "zn", "znan",
        "znanstv", "zool", "zoot", "zun", "zv", "zvd", "°c", "°f", "°k", "čeb", "čet", "čl", "člen", "člov",
        "čustv", "ľ", "ł", "ş", "šalj", "šir", "škofl", "škot", "šol", "šp", "špan", "šport", "št", "štev",
        "števil", "štud", "švic", "ů", "ű", "žarg", "žel", "žen",
    }
)  # fmt: skip
