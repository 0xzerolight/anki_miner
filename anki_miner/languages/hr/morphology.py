"""Croatian data for the shared spaCy substrate (spec Appendix E, hr column).

Pinned on real ``hr_core_news_sm`` 3.8.0 output (``tests/fixtures/hr/``), UD Croatian-SET dev+test
(statistics only), wty-sh-en 2026.08.29 and hermitdave's OpenSubtitles 2018 ``hr_50k.txt``.

``HR_EXCLUDED_SUBTYPES``: the model's fine tagger is live (660 MULTEXT-East labels, ``tag_acc`` .9029 against
``pos_acc`` .9666), and every non-vocabulary category really does surface under an allowed UPOS - ``Np`` on
Tropolje, ``Ml`` on drugi/prvi, ``Ps`` on tvoj, ``Y`` on prof/dr/tzv, ``Z`` on an abbreviation-like residual.
The table is therefore every label whose MULTEXT-East category is outside ``{A, Nc, R, Vm}`` (adjective,
common noun, adverb, main verb): 407 of the 660. Generated from ``meta.json``, never transcribed
(``docs/spacy10-bus/build_excluded_subtypes.py``); the test re-derives it from the installed model and a
second case pins its effect on real sentences. It blocks 876 of 23,665 content-UPOS tokens on UD SET
dev+test.

``HR_DIGRAPH_LIGATURES``: D7's ligature half. The nine U+01C4-U+01CC codepoints spell ``dz-caron``, ``lj``
and ``nj`` as one character; the map rewrites them to the ordinary two letters, which can only move a query
ONTO a dictionary key. It is inert on real data (0 of 457,883 wty-sh-en terms and 0 of 202,438,751
OpenSubtitles token occurrences carry one) and ships as spec compliance. D7's other half - folding d-bar to
``d`` - is REJECTED: 3,028 real terms carry d-bar, the fold lands 262 of them on a different real term
(Inđija -> Indija, Anđa -> Ande) and on nothing for the other 2,766, and ``normalize`` output is
also the sentence stored on the card, so it would print ``dak`` for ``đak``. Croatian keeps its letter.

``HR_ABBREVIATIONS``: hand-written. spaCy ships no Croatian tokenizer exceptions (``spacy/lang/hr/`` holds
only ``__init__.py`` and ``stop_words.py``), so there is no upstream list to inherit; these are the standard
abbreviations of Croatian orthography (pravopis.hr, the Institut za hrvatski jezik guide - a client-rendered
SPA, so the list is transcribed by hand and UNVERIFIED by scripted fetch). Four candidates are left out
because they are ordinary words that end sentences: ``svi`` (svibanj/May, but "all", rank 108), ``pet``
(petak/Friday, but "five", rank 425), ``im`` ("to them", rank 188) and ``red`` ("row", rank 913). Month and
weekday abbreviations are left out with them: Croatian dates are written with ordinal numerals.

``hr_tone_fold``: wty head-line headwords carry tonal diacritics Croatian orthography never writes
(U+0300 grave, U+0301 acute, U+0304 macron, U+030F double grave, U+0311 inverted breve). They are stripped
from a vowel or ``r`` only - on c/s/z the same shape IS the letter (c-caron, c-acute, s-caron, z-caron) - and
the fold recovers the plain term for 28,856 of 28,939 single-word headwords. A mark on any other base is
left alone: across the whole dictionary a mark sits on schwa 0 times and U+0309 occurs 0 times.

``hr_short_infinitive_pass``: Croatian speech and subtitles drop the infinitive's final ``i`` (``radit``,
``morat``, ``vidjet``). In ``hr_50k.txt`` 461 such surfaces carry 541,718 occurrences, 0.268 % of every
token, led by morat 28,236, vidjet 20,514 and dat 16,783. The model answers them in three shapes - the
surface as its own lemma, a fabricated ``-tti`` lemma (``imat`` -> ``imatti``), or a non-verb tag - so the
repair keys on the SURFACE, not the lemma, and asks the dictionary before rewriting anything.
"""

from __future__ import annotations

import logging
import unicodedata
from collections.abc import Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from anki_miner.languages._spaced.pos import UPOS_ALLOWED
from anki_miner.languages._spaced.script import (
    BRACKETS_PATTERN,
    DIALOGUE_DASH_PATTERN,
    MUSIC_PATTERN,
    PARENS_PATTERN,
    nfc_normalize,
)

if TYPE_CHECKING:  # annotation-only: no services import at profile build
    from anki_miner.services.morphology import AttestLookup, FormLookup

logger = logging.getLogger(__name__)

#: The model package the tokenizer loads and the availability probe looks for.
HR_MODEL_PACKAGE = "hr_core_news_sm"

HR_ALLOWED_POS: tuple[str, ...] = UPOS_ALLOWED

HR_EXCLUDED_SUBTYPES: tuple[str, ...] = (
    "Cc", "Cs", "I", "Mdc", "Mdm", "Mdo", "Mds", "Mlc", "Mlc--g", "Mlc--i", "Mlc--l", "Mlcf-a", "Mlcf-d", "Mlcf-g",
    "Mlcf-n", "Mlcfsa", "Mlcfsd", "Mlcfsg", "Mlcfsi", "Mlcfsl", "Mlcfsn", "Mlcm-a", "Mlcm-g", "Mlcm-l", "Mlcm-n",
    "Mlcmpn", "Mlcmsan", "Mlcmsay", "Mlcmsg", "Mlcmsi", "Mlcmsl", "Mlcmsn", "Mlcn-n", "Mlcnsa", "Mlcnsg", "Mlcnsn",
    "Mlofpa", "Mlofpd", "Mlofpg", "Mlofpi", "Mlofpl", "Mlofpn", "Mlofsa", "Mlofsd", "Mlofsg", "Mlofsi", "Mlofsl",
    "Mlofsn", "Mlompa", "Mlompd", "Mlompg", "Mlompi", "Mlompl", "Mlompn", "Mlomsan", "Mlomsay", "Mlomsd", "Mlomsg",
    "Mlomsi", "Mlomsl", "Mlomsn", "Mlonpa", "Mlonpg", "Mlonpl", "Mlonpn", "Mlonsa", "Mlonsd", "Mlonsg", "Mlonsi",
    "Mlonsl", "Mlonsn", "Mls", "Mlsf-a", "Mlsf-g", "Mlsf-i", "Mlsf-l", "Mlsf-n", "Mlsm-a", "Mlsm-g", "Mlsm-l",
    "Mlsm-n", "Mlsmpn", "Mlsn-n", "Mrc", "Mro", "Npfpa", "Npfpg", "Npfpl", "Npfpn", "Npfsa", "Npfsd", "Npfsg",
    "Npfsi", "Npfsl", "Npfsn", "Npmpa", "Npmpd", "Npmpg", "Npmpi", "Npmpl", "Npmpn", "Npmsan", "Npmsay", "Npmsd",
    "Npmsg", "Npmsi", "Npmsl", "Npmsn", "Npmsv", "Npnpg", "Npnpn", "Npnsa", "Npnsd", "Npnsg", "Npnsi", "Npnsl",
    "Npnsn", "Pd-fpa", "Pd-fpd", "Pd-fpg", "Pd-fpi", "Pd-fpl", "Pd-fpn", "Pd-fsa", "Pd-fsd", "Pd-fsg", "Pd-fsi",
    "Pd-fsl", "Pd-fsn", "Pd-mpa", "Pd-mpd", "Pd-mpg", "Pd-mpi", "Pd-mpl", "Pd-mpn", "Pd-msan", "Pd-msay", "Pd-msd",
    "Pd-msg", "Pd-msi", "Pd-msl", "Pd-msn", "Pd-npa", "Pd-npg", "Pd-npi", "Pd-npn", "Pd-nsa", "Pd-nsd", "Pd-nsg",
    "Pd-nsi", "Pd-nsl", "Pd-nsn", "Pi-fpa", "Pi-fpd", "Pi-fpg", "Pi-fpi", "Pi-fpl", "Pi-fpn", "Pi-fsa", "Pi-fsd",
    "Pi-fsg", "Pi-fsi", "Pi-fsl", "Pi-fsn", "Pi-mpa", "Pi-mpd", "Pi-mpg", "Pi-mpi", "Pi-mpl", "Pi-mpn", "Pi-msan",
    "Pi-msay", "Pi-msd", "Pi-msg", "Pi-msi", "Pi-msl", "Pi-msn", "Pi-npa", "Pi-npd", "Pi-npg", "Pi-npi", "Pi-npl",
    "Pi-npn", "Pi-nsa", "Pi-nsd", "Pi-nsg", "Pi-nsi", "Pi-nsl", "Pi-nsn", "Pi3m-a", "Pi3m-d", "Pi3m-g", "Pi3m-i",
    "Pi3m-n", "Pi3n-a", "Pi3n-d", "Pi3n-g", "Pi3n-i", "Pi3n-l", "Pi3n-n", "Pp1-pa", "Pp1-pd", "Pp1-pg", "Pp1-pi",
    "Pp1-pl", "Pp1-pn", "Pp1-sa", "Pp1-sd", "Pp1-sg", "Pp1-si", "Pp1-sl", "Pp1-sn", "Pp2-pa", "Pp2-pd", "Pp2-pl",
    "Pp2-pn", "Pp2-sa", "Pp2-sd", "Pp2-sg", "Pp2-sl", "Pp2-sn", "Pp3-pa", "Pp3-pd", "Pp3-pg", "Pp3-pi", "Pp3-pl",
    "Pp3fpn", "Pp3fsa", "Pp3fsd", "Pp3fsg", "Pp3fsi", "Pp3fsl", "Pp3fsn", "Pp3mpn", "Pp3msa", "Pp3msd", "Pp3msg",
    "Pp3msi", "Pp3msl", "Pp3msn", "Pp3npn", "Pp3nsa", "Pp3nsi", "Pp3nsn", "Pq-fpa", "Pq-fpn", "Pq-fsa", "Pq-fsi",
    "Pq-fsl", "Pq-fsn", "Pq-mpn", "Pq-msn", "Pq-nsn", "Pq3m-d", "Pq3m-n", "Pq3n-a", "Pq3n-l", "Pq3n-n", "Ps1fpa",
    "Ps1fpg", "Ps1fpl", "Ps1fpn", "Ps1fsa", "Ps1fsd", "Ps1fsg", "Ps1fsi", "Ps1fsl", "Ps1fsn", "Ps1fsv", "Ps1mpa",
    "Ps1mpd", "Ps1mpg", "Ps1mpi", "Ps1mpl", "Ps1mpn", "Ps1msan", "Ps1msay", "Ps1msd", "Ps1msg", "Ps1msi", "Ps1msl",
    "Ps1msn", "Ps1msv", "Ps1nsa", "Ps1nsg", "Ps1nsl", "Ps1nsn", "Ps2fpa", "Ps2fpl", "Ps2fpn", "Ps2fsg", "Ps2fsn",
    "Ps2mpa", "Ps2mpg", "Ps2mpn", "Ps2msan", "Ps2msd", "Ps2msi", "Ps2msl", "Ps2msn", "Ps2npn", "Ps2nsg", "Ps2nsi",
    "Ps2nsl", "Ps2nsn", "Ps3fpa", "Ps3fpg", "Ps3fpl", "Ps3fpn", "Ps3fsa", "Ps3fsd", "Ps3fsg", "Ps3fsi", "Ps3fsl",
    "Ps3fsn", "Ps3mpa", "Ps3mpd", "Ps3mpg", "Ps3mpi", "Ps3mpl", "Ps3mpn", "Ps3msan", "Ps3msay", "Ps3msd", "Ps3msg",
    "Ps3msi", "Ps3msl", "Ps3msn", "Ps3npa", "Ps3npg", "Ps3npl", "Ps3npn", "Ps3nsa", "Ps3nsg", "Ps3nsi", "Ps3nsl",
    "Ps3nsn", "Px--sa", "Px--sd", "Px--sg", "Px--si", "Px--sl", "Px-fpa", "Px-fpg", "Px-fpi", "Px-fpl", "Px-fpn",
    "Px-fsa", "Px-fsd", "Px-fsg", "Px-fsi", "Px-fsl", "Px-mpa", "Px-mpd", "Px-mpg", "Px-mpi", "Px-mpl", "Px-msan",
    "Px-msay", "Px-msd", "Px-msg", "Px-msi", "Px-msl", "Px-npa", "Px-npd", "Px-npg", "Px-npi", "Px-npl", "Px-nsa",
    "Px-nsg", "Px-nsi", "Px-nsl", "Qo", "Qq", "Qr", "Qz", "Sa", "Sd", "Sg", "Si", "Sl", "Vaa1p", "Vaa1s", "Vaa2p",
    "Vaa2s", "Vaa3p", "Vaa3s", "Vae3s", "Vam2p", "Van", "Vap-pf", "Vap-pm", "Vap-pn", "Vap-sf", "Vap-sm", "Vap-sn",
    "Var1p", "Var1s", "Var2p", "Var2s", "Var3p", "Var3s", "X", "Xf", "Y", "Z", "_SP",
)  # fmt: skip

HR_ABBREVIATIONS: frozenset[str] = frozenset(
    {
        # titles and people
        "dr", "mr", "prof", "ing", "dipl", "akad", "sveuč", "g", "gđa", "gđica", "gosp", "sv",
        # references and editorial
        "str", "br", "bilj", "usp", "op", "ur", "izd", "sl", "isl", "tzv", "tj", "odn", "npr", "itd", "pr", "cca",
        # units, time and addresses
        "god", "st", "km", "cm", "mm", "kg", "dkg", "ml", "dl", "kn", "tel", "ul",
    }
)  # fmt: skip

#: D7, ligature half: the one-character digraphs to their ordinary spelling (inert on real text).
HR_DIGRAPH_LIGATURES: Mapping[int, str] = MappingProxyType(
    {
        0x01C4: "DŽ", 0x01C5: "Dž", 0x01C6: "dž",
        0x01C7: "LJ", 0x01C8: "Lj", 0x01C9: "lj",
        0x01CA: "NJ", 0x01CB: "Nj", 0x01CC: "nj",
    }
)  # fmt: skip

#: The four tone marks wty writes plus U+0311; NEVER U+030C or U+0301 on c/s/z, where the mark IS the letter.
_TONE_MARKS = frozenset({"\u0300", "\u0301", "\u0304", "\u030f", "\u0311"})
#: Schwa never carries a mark in wty-sh-en, so it is not a base.
_TONE_BASES = frozenset("aeiourAEIOUR")

#: Croatian speaker cues (``ŽELJKO:``, ``ĐURO:``): the Latin rule with the five letters Latin-1 lacks.
#: The filter runs after ``hr_normalize``, so a digraph ligature has already become two ordinary letters.
HR_SPEAKER_PATTERN = r"^[A-ZÀ-ÖØ-ÞĆČĐŠŽ]" r"[A-ZÀ-ÖØ-ÞĆČĐŠŽ0-9 .'-]*" r"[A-ZÀ-ÖØ-ÞĆČĐŠŽ]:\s*"
#: The S10 default for Croatian: the shared Latin parts with the Croatian speaker rule.
HR_SUBTITLE_REGEX = "|".join(
    (BRACKETS_PATTERN, PARENS_PATTERN, MUSIC_PATTERN, HR_SPEAKER_PATTERN, DIALOGUE_DASH_PATTERN)
)

#: A short infinitive is at least three characters long; the candidate is the surface plus the dropped ``i``.
_MIN_SHORT_INFINITIVE = 3


def hr_normalize(text: str) -> str:
    """S5 for Croatian: NFC, then the D7 digraph ligatures. The d-bar letter is deliberately untouched."""
    return nfc_normalize(text).translate(HR_DIGRAPH_LIGATURES)


def hr_tone_fold(text: str) -> str:
    """Drop a wty tone mark from a vowel or ``r``; every Croatian letter keeps its own mark (c-caron ...)."""
    out: list[str] = []
    for char in unicodedata.normalize("NFD", text):
        if char in _TONE_MARKS and out and unicodedata.normalize("NFC", out[-1])[:1] in _TONE_BASES:
            continue
        out.append(char)
    return unicodedata.normalize("NFC", "".join(out))


def hr_short_infinitive_pass(tokens: list[Any], attest: AttestLookup | None, forms: FormLookup | None) -> list[Any]:
    """A ``token_post_pass``: ``radit`` takes its ``i`` back when the dictionary knows ``raditi``.

    The candidate comes from the SURFACE (``surface.casefold() + "i"``), because the model's lemma is not a
    usable stem: it is the surface itself for ``mislit``, a fabricated ``imatti`` for ``imat``. A verb whose
    own lemma the dictionary knows is never touched, so an ordinary ``-t`` verb form keeps its lemma; one
    attestation call per line covers every suspect's lemma and candidate. ``attest is None`` - no offline
    dictionary - changes nothing. The pass is gated on VERB (the ``FrenchVerbLemmaPass`` precedent): the
    wider four-class gate adds 18 surfaces / 18,188 occurrences of false positives, several of them Croatian
    words it would turn wrong (vrsti, tableti, isti), for 16:1 against 221:1. The cost is a short infinitive
    the model tags NOUN (``Gledat``) or PROPN (``Radit``), which keeps its surface. A repaired lemma no longer
    matches the suspect rule, so a second run is a no-op. The third argument (R36's form lookup) is ignored.
    """
    del forms
    if attest is None:
        return tokens
    suspects = [token for token in tokens if _is_short_infinitive(token)]
    if not suspects:
        return tokens
    candidates = [token.surface.casefold() + "i" for token in suspects]
    probe = [str(token.feature.lemma or "") for token in suspects] + candidates
    attested = attest(list(dict.fromkeys(probe)))
    for token, candidate in zip(suspects, candidates, strict=True):
        lemma = str(token.feature.lemma or "")
        if lemma not in attested and candidate in attested:
            token.feature.lemma = candidate
        else:
            logger.debug("Croatian infinitive %r not repaired; keeping %r", candidate, lemma)
    return tokens


def _is_short_infinitive(token: Any) -> bool:
    surface = token.surface.casefold()
    return bool(token.feature.pos1 == "VERB" and len(surface) >= _MIN_SHORT_INFINITIVE and surface.endswith("t"))
