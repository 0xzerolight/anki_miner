"""Korean mined-form policy and Sejong POS defaults.

Card Expression for Korean is the DICTIONARY form: predicates (verbs, adjectives
and auxiliaries) mine as stem + 다 - 먹었다 mines as 먹다, not 먹 - because every
Korean dictionary, deck and learner names the word that way. Everything else
mines as its surface: Korean nouns do not inflect, so surface IS the dictionary
form.

The tables were derived from real converter output over Korean prose (tag
histogram + samples), not transcribed from the Sejong documentation: the
documented set omits kiwi's own additions (the Z_CODA tag and the -R/-I
regularity suffix on predicate tags, both handled in tokenizer.py).

Both gate fields are live. KO_ALLOWED_POS holds two-letter Sejong CLASSES and is
tested against pos1 (services/morphology.py:1073); KO_EXCLUDED_SUBTYPES holds
full tags and is tested against pos2 (:1077). The split earns its keep for words
whose class must stay open: NNB (bound nouns 것/수/개/명 - grammar scaffolding)
shares NN with NNG/NNP, and MAJ (conjunctive adverbs 그러나/그리고) shares MA with
MAG. Everything else - particles, endings, punctuation, numerals, pronouns,
determiners, interjections, copulas, affixes - is excluded by being absent from
KO_ALLOWED_POS.
"""

from __future__ import annotations

from anki_miner.languages.ko.tokenizer import coarse_tag

#: Predicate classes whose dictionary form is stem + 다. VC (the copulas VCP 이다
#: / VCN 아니다) is deliberately absent even though kiwi does emit it: VC is not in
#: KO_ALLOWED_POS, so the gate drops a copula before the parser ever asks this
#: policy for its card front. XS is absent for a different reason - XSV/XSA are
#: predicate-forming suffixes but share the class with the noun-forming XSN (-님,
#: -들), and XS is not mineable anyway: 공부하고 drops 하/XSV as a TOKEN. The
#: nominal+suffix PAIR is merged upstream instead (languages/ko/predicate_merge.py),
#: which is what puts 공부하다 - not 공부 - on the card when the dictionary
#: attests it.
PREDICATE_TAGS: frozenset[str] = frozenset({"VV", "VA", "VX"})

#: Mineable content classes, sorted (the settings editor shows them in order).
#: MA = adverbs, NN = nouns, SH = hanja, VA/VV/VX = predicates, XR = the bound
#: root carrying the meaning of 하다-predicates. NP (pronouns), NR (numerals),
#: MM (determiners), IC (interjections), VC (copulas), XS (affixes), J*
#: (particles), E* (endings), S[FPONE] (punctuation, digits, symbols) and Z_*
#: are absent on purpose.
#:
#: SL (a Latin-script loanword run) is absent for a different reason: the real
#: gate for it is the script gate, not this one. KoreanScript.contains_target_
#: script admits only hangul or hanja, and TokenInclusionRule.should_include
#: consults it AFTER the class gate, so an SL token is rejected however this
#: tuple is set. Listing it promised mining that cannot happen and put a class
#: in the POS editor that does nothing when ticked.
KO_ALLOWED_POS: tuple[str, ...] = ("MA", "NN", "SH", "VA", "VV", "VX", "XR")

#: Full tags dropped inside an allowed class - the pos2 gate. Both entries name
#: grammar scaffolding that cannot be excluded by class without taking real
#: content words with it.
KO_EXCLUDED_SUBTYPES: tuple[str, ...] = ("MAJ", "NNB")

#: Human labels for the settings POS editor (Korean tagset vocabulary - UniDic
#: strings must never reach this gate). Covers the excluded subtypes too, so the
#: editor can name what it is dropping.
KO_POS_LABELS: dict[str, str] = {
    "MA": "Adverb",
    "MAJ": "Conjunctive adverb",
    "NN": "Noun",
    "NNB": "Bound noun",
    "SH": "Hanja",
    "VA": "Adjective",
    "VV": "Verb",
    "VX": "Auxiliary predicate",
    "XR": "Bound root",
}


class KoreanMinedForm:
    """MinedFormPolicy for Korean: predicates as stem + 다, else surface."""

    def mined_form(
        self,
        pos: str | None,
        orth_base: str,
        lemma: str,
        surface: str,
        pronunciation: str | None = None,
    ) -> str:
        """Return the card-front spelling for one token.

        ``orth_base`` and ``pronunciation`` exist for the Japanese policy's
        benefit and are unused here; ``pos`` may be None (protocol default),
        which selects the surface branch. ``pos`` normally arrives as the coarse
        class the converter emits, but a raw kiwi tag (VA-I) is tolerated -
        coarse_tag folds both.
        """
        if coarse_tag(pos or "") not in PREDICATE_TAGS:
            return surface
        stem = lemma or surface
        return stem if stem.endswith("다") else stem + "다"
