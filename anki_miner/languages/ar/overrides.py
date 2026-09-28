"""Where calima-msa-r13 reads a common word wrong: one lexeme repair and the analysis pick overrides.

Both tables are data the analyzer's own analyses are checked against; neither invents an analysis.
``AR_LEX_REPAIRS`` renames a lexeme the database misfiles (``summarise`` applies it before the lemma
and reading are derived). ``AR_PICK_OVERRIDES`` names, for a folded token key, the ``(lex, pos)`` of
the analysis the token takes (``pick_analysis`` consults it before the argmax).

Why a table: the pick is the argmax of ``pos_lex_logprob``, and calima stores ``-99`` - no statistics
- on many of the commonest in-vocabulary words (``\u0643\u064f\u0644\u0651`` "every", ``\u0627\u0644\u0622\u0646\u064e`` "now", ``\u0644\u0650\u0630\u0627`` "so",
``\u0642\u064e\u0628\u0652\u0644\u064e`` "before"), so any rarer reading of the same letters wins; where two readings tie, list order
decides. Upstream CAMeL settles these with its trained disambiguator, which the pack does not carry.
The rows are the top-1,000 OpenSubtitles surfaces that took another word's analysis when hand-checked
against wty-ar-en's lemma rows (2026-09), plus the clitic variants that lose the same way. Keys are
folded tokens, so a clitic-bearing spelling is a row of its own. ``\u062a\u0639\u0627\u0644`` "come" is not here: the
database offers it one (wrong) analysis only.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

#: calima files the verb stems of ``\u0631\u064e\u0623\u064e\u0649`` "see; think; believe" (root ``\u0631.#.#``) under the lexeme
#: ``\u0631\u0627\u0648\u064e\u0646\u0652\u062f`` "rhubarb": every "I saw / you see" card fronted rhubarb. The noun rhubarb keeps its lexeme.
AR_LEX_REPAIRS: Mapping[tuple[str, str], str] = MappingProxyType(
    {("\u0631\u0627\u0648\u064e\u0646\u0652\u062f", "verb"): "\u0631\u064e\u0623\u064e\u0649"}
)

#: Folded token key -> ``(lex, pos)`` of the analysis it takes, spelled exactly as the analyzer
#: returns it (``\u062b\u064f\u0645\u0651\u064e`` carries shadda before fatha). Each comment names the word and,
#: after "not", the reading that won without the row (the argmax, or list order in a tie).
AR_PICK_OVERRIDES: Mapping[str, tuple[str, str]] = MappingProxyType(
    {
        "\u0643\u0644": ("\u0643\u064f\u0644\u0651", "noun"),  # kull "every", not kul "eat!"
        "\u0648\u0643\u0644": ("\u0643\u064f\u0644\u0651", "noun"),  # wa-kull, not "and eat!"
        "\u0628\u0643\u0644": ("\u0643\u064f\u0644\u0651", "noun"),  # bi-kull, not bukla "clasp"
        "\u0641\u0643\u0644": ("\u0643\u064f\u0644\u0651", "noun"),  # fa-kull, not "so eat!"
        "\u0627\u0644\u0622\u0646": ("\u0627\u0644\u0622\u0646\u064e", "adv"),  # al-aana "now", not aan "time"
        "\u0644\u0630\u0627": ("\u0644\u0650\u0630\u0627", "conj"),  # li-dhaa "so", not ladhiidh
        "\u0637\u0648\u0627\u0644": ("\u0637\u0650\u0648\u0627\u0644\u064e", "prep"),  # tiwaala "during", not tawiil
        "\u0645\u0647\u0645\u0627": ("\u0645\u064e\u0647\u0652\u0645\u0627", "conj"),  # mahmaa "whatever", not muhimm
        "\u062e\u0637\u0623": ("\u062e\u064e\u0637\u064e\u0623", "noun"),  # khata' "mistake", not khatt "line"
        "\u0646\u0635\u0641": ("\u0646\u0650\u0635\u0652\u0641", "noun"),  # nisf "half", not wasafa
        "\u0628\u0644\u0627": ("\u0628\u0650\u0644\u0627", "prep"),  # bi-laa "without", not ball
        "\u0648\u0634\u0643": ("\u0648\u064e\u0634\u0652\u0643", "noun"),  # washk "verge", not wa+shakk
        "\u0623\u0644\u0641": ("\u0623\u064e\u0644\u0652\u0641", "noun"),  # alf "thousand", not ilf
        "\u062a\u0631\u0643": ("\u062a\u064e\u0631\u064e\u0643", "verb"),  # taraka "leave", not tara+ka
        "\u0641\u062a\u0631\u0629": ("\u0641\u064e\u062a\u0652\u0631\u064e\u0629", "noun"),  # fatra "period", not "see"
        "\u0644\u0633\u062a": ("\u0644\u064e\u064a\u0652\u0633\u064e", "verb"),  # lastu "I am not", not laasa
        "\u0645\u0639\u0643": ("\u0645\u064e\u0639", "prep"),  # ma'a-ka "with you", not ma'aka
        "\u0645\u0639\u0646\u0627": ("\u0645\u064e\u0639", "prep"),  # ma'a-naa "with us", not maa'a
        "\u064a\u062c\u0631\u064a": ("\u062c\u064e\u0631\u064e\u0649", "verb"),  # jaraa "run, happen", not ajraa
        "\u0643\u064a": ("\u0643\u064e\u064a", "conj"),  # kay "in order to", not kayy
        "\u0642\u0628\u0644": ("\u0642\u064e\u0628\u0652\u0644\u064e", "prep"),  # qabla "before", not qabila
        "\u0623\u062d\u062f": ("\u0623\u064e\u062d\u064e\u062f", "noun"),  # ahad "someone", not ahadd
        "\u062b\u0645": ("\u062b\u064f\u0645\u0651\u064e", "adv"),  # thumma "then", not thamma
        "\u0647\u064a\u0627": ("\u0647\u064e\u064a\u0651\u0627", "verb"),  # hayyaa "come on", not hayya'a
        "\u0628\u0639\u0636": ("\u0628\u064e\u0639\u0652\u0636", "adj"),  # ba'd "some", not ba''ada
        "\u062d\u0633\u0646\u0627": ("\u062d\u064e\u0633\u064e\u0646", "adv"),  # hasanan "okay", not husn
    }
)
