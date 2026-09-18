"""Lithuanian abbreviations, hand-transcribed: the source of ``LT_ABBREVIATIONS`` (spec D8).

WIKTIONARY and ALKSNIS are derived works of CC BY-SA 4.0 material and are redistributed under CC BY-SA 4.0:
en.wiktionary's "Category:Lithuanian abbreviations" (Wiktionary contributors,
https://en.wiktionary.org/wiki/Category:Lithuanian_abbreviations) and UD Lithuanian ALKSNIS r2.8 (Utka,
Rimkute, Bielinskiene, Kovalevskaite, Boizou, Aleksandraviciute, Brokaite, Zeman, Perkova, Griciute;
https://github.com/UniversalDependencies/UD_Lithuanian-ALKSNIS). This file is production code, not a test
fixture, because the wheel excludes ``tests*`` and the derived set ships.

vlkk.lt's own list (https://vlkk.lt/aktualiausios-temos/rasyba/santrumpos) answers every scripted client with a
Cloudflare challenge (HTTP 403, ``cf-mitigated: challenge``; probed 2026-09-12 and 2026-09-17), and spaCy's
``lt/tokenizer_exceptions.py`` drops every dotted base exception, so there was no list to lift. Four sources:

WIKTIONARY  every page title of the category, read through the MediaWiki API (``list=categorymembers``) on
            2026-09-17, verbatim. A title without a dot is an initialism and contributes no key.
ALKSNIS     every ALKSNIS r2.8 token tagged ``sutr.`` that the treebank follows with a ``.`` token, as written,
            over all three splits (144 x ``m``, 55 x ``proc``, 54 x ``d`` ...).
LETTERS     the Lithuanian alphabet plus q w x: a capital and a dot is a name initial (``A. Smetona``).
HAND        common abbreviations in neither list, with their expansion. Each stem is absent from, or ranked
            beyond 30,000 in, hermitdave/FrequencyWords' OpenSubtitles 2018 ``lt_50k.txt``, so none is an
            everyday word. ``sek.`` (sekunde), ``lenk.`` (lenkiskai) and ``up.`` (upe) were left out: ``sek``
            and ``lenk`` are imperatives and ``up`` is English in subtitles.

``tests/unit/languages/test_lt_morphology.py`` re-derives ``LT_ABBREVIATIONS`` from these four, so the shipped
set is regenerated, never hand-edited.
"""

from __future__ import annotations

WIKTIONARY: tuple[str, ...] = (
    "a.", "a. s.", "al.", "aps.", "apskr.", "aut.", "aut. past.", "b-ka", "b. k.", "bal.", "bbd", "bdv.",
    "birž.", "d.", "dem.", "dgs.", "DK", "dkt.", "dll.", "doc.", "DP", "dr.", "dvs.", "el. paštas", "ež.", "f.",
    "faks.", "g.", "G.", "geg.", "gerb.", "gim.", "gruod.", "gub.", "ir pan.", "ir t. t.", "ir t.t.", "Įn.",
    "įnag.", "jng.", "K.", "kov.", "krw", "kt.", "ktm", "kuop.", "l-kla", "l. e.", "l. e. p.", "lapkr.", "LDP",
    "liep.", "LKDP", "LLRA", "LSDP", "LVLS", "LVŽS", "LŽP", "m.", "m. m.", "m. sav.", "m.m.", "menk.", "mėn.",
    "mldc", "mln.", "mlrd.", "mot.", "mst.", "mstl.", "N-", "N.", "naud.", "nr.", "Nr.", "pagr. f.", "pan.",
    "par.", "pav.", "pl.", "plg.", "ppr.", "pr.", "pr. Kr.", "prl.", "proc.", "prof.", "prv.", "pvz.", "r.",
    "rugp.", "rugs.", "saus.", "sav.", "saviv.", "sen.", "skait.", "spal.", "str.", "Š.", "š. m.", "š.m.",
    "šauksm.", "šnek.", "trln.", "TS", "TT", "tūkst.", "V.", "val.", "vard.", "vas.", "viet.", "vyr.", "vyr. g.",
    "vyresn.", "vks.", "vlsč.", "vns.", "Vt.", "žr.",
)  # fmt: skip

ALKSNIS: tuple[str, ...] = (
    "m", "proc", "d", "Nr", "p", "tūkst", "R", "pan", "mln", "Žin", "V", "A", "K", "a", "D", "kt", "M", "G", "pvz",
    "mėn", "mlrd", "J", "Švč", "str", "Šv", "e", "L", "I", "E", "t", "angl", "Z", "šv", "red", "past", "min", "T",
    "S", "C", "š", "Č", "y", "vyr", "val", "sB", "prof", "kg", "gen", "doc", "Pvz", "P", "NR", "N", "H",
)  # fmt: skip

LETTERS = "aąbcčdeęėfghiįyjklmnoprsštuųūvzžqwx"

HAND: dict[str, str] = {
    "akad": "akademikas",
    "buv": "buvęs",
    "gyv": "gyventojai",
    "habil": "habilituotas",
    "inž": "inžinierius",
    "įv": "įvairūs",
    "kun": "kunigas",
    "pranc": "prancūziškai",
    "psl": "puslapis",
    "rus": "rusiškai",
    "sk": "skyrius",
    "t.y": "tai yra",
    "tel": "telefonas",
    "vad": "vadinamasis",
    "vnt": "vienetas",
    "vok": "vokiškai",
    "vysk": "vyskupas",
}
