"""Slovenian data for the shared spaCy substrate (spec Appendix E, sl column).

Pinned on real ``sl_core_news_sm`` 3.8.0 output (``tests/fixtures/sl/``), UD Slovenian-SSJ dev+test
(statistics only), wty-sl-en 2026.09.19 and hermitdave's OpenSubtitles 2018 ``sl_50k.txt``.

``SL_EXCLUDED_SUBTYPES``: the model's fine tagger is live (1,141 MULTEXT-East labels, ``tag_acc``
.9041 against ``pos_acc`` .9695), and every non-vocabulary category really does surface under an
allowed UPOS - ``Va-r3s-n`` on the copula ``je`` the morphologizer calls VERB, ``Ml`` on the
numerals ``dva``/``dve``, ``Np`` on Novak, ``Pd`` on ``to``, ``Y`` on ``dr.``/``npr.``. The table is
therefore every label whose MULTEXT-East category is outside ``{A, Nc, R, Vm}`` (adjective, common
noun, adverb, main verb): 784 of the 1,141, the same rule that produces hr's shipped 407 of 660.
Generated from ``meta.json``, never transcribed; the test re-derives it from the installed model. It
blocks 465 of 23,929 content-UPOS tokens (1.94 %) on UD Slovenian-SSJ dev+test, led by ``Va-r3s-n``
85 and ``Va-p-sn`` 36.

``sl_tone_fold``: wty head-line headwords are written in the dictionary's accent notation, which
Slovenian orthography never writes - U+0300 grave, U+0301 acute, U+0302 circumflex, U+0304 macron,
U+030F double grave, U+0311 inverted breve and U+0323 dot below, on a vowel or ``r``. U+030C caron
is NEVER folded: it IS the letter in c/s/z. Stacked marks fall out in one pass because NFD orders
the dot below before the marks above, so the lower one is dropped first and the upper one then sees
the bare vowel. The fold is the hook's ``partner_fold`` and nothing else; all 21 aspect partners in
the dictionary fold to plain Slovene.

The schwa and the stroked l are deliberately untouched, and so is a mark sitting ON a schwa: the
bases are the vowels and ``r``, hr's set exactly, so the ``pes`` head line comes back unchanged.
Both are dictionary-notation LETTERS, not marks - no mark fold can turn the schwa spelling of
``pes`` into ``pes`` - and they occur in 0 of the 21 aspect partners, which is the only place the
fold prints, so widening the base set would be a mechanism with no caller. 212 head-line headwords
carry a schwa and 27 a stroked l. The gender read is unaffected: the hook's own D2
``_without_combining_marks`` strips every mark before it looks for the letter, so the ``pes`` head
line still reads masculine.

``SL_SPEAKER_PATTERN``: Slovenian speaker cues (ZENSKA:, SOFER:) - the Latin rule with the three
capitals Latin-1 lacks. There is no ``normalize`` of sl's own: Slovenian has no digraph ligature
(D7 is hr's) and no cedilla map (D5 is ro's), so the profile passes the shared ``nfc_normalize``,
which is all a decomposed caron needs.
"""

from __future__ import annotations

import unicodedata

from anki_miner.languages._spaced.pos import UPOS_ALLOWED
from anki_miner.languages._spaced.script import (
    BRACKETS_PATTERN,
    DIALOGUE_DASH_PATTERN,
    MUSIC_PATTERN,
    PARENS_PATTERN,
)

#: The model package the tokenizer loads and the availability probe looks for.
SL_MODEL_PACKAGE = "sl_core_news_sm"

SL_ALLOWED_POS: tuple[str, ...] = UPOS_ALLOWED

SL_EXCLUDED_SUBTYPES: tuple[str, ...] = (
    "Cc", "Cs", "I", "Mdc", "Mdo", "Mlc-pa", "Mlc-pd", "Mlc-pg", "Mlc-pi", "Mlc-pl", "Mlc-pn", "Mlcfda", "Mlcfdg",
    "Mlcfdi", "Mlcfdl", "Mlcfdn", "Mlcfpa", "Mlcfpd", "Mlcfpg", "Mlcfpi", "Mlcfpl", "Mlcfpn", "Mlcmda", "Mlcmdg",
    "Mlcmdi", "Mlcmdl", "Mlcmdn", "Mlcmpa", "Mlcmpd", "Mlcmpg", "Mlcmpi", "Mlcmpl", "Mlcmpn", "Mlcnda", "Mlcndg",
    "Mlcndi", "Mlcndl", "Mlcndn", "Mlcnpa", "Mlcnpg", "Mlcnpi", "Mlcnpl", "Mlcnpn", "Mlofpa", "Mlofpd", "Mlofpg",
    "Mlofpi", "Mlofpl", "Mlofpn", "Mlofsa", "Mlofsd", "Mlofsg", "Mlofsi", "Mlofsl", "Mlofsn", "Mlompa", "Mlompg",
    "Mlompi", "Mlompl", "Mlompn", "Mlomsa", "Mlomsd", "Mlomsg", "Mlomsi", "Mlomsl", "Mlomsn", "Mlonda", "Mlonpg",
    "Mlonpl", "Mlonpn", "Mlonsa", "Mlonsg", "Mlonsi", "Mlonsl", "Mlonsn", "Mlpfdl", "Mlpfdn", "Mlpfpa", "Mlpfpg",
    "Mlpfpi", "Mlpfpl", "Mlpfpn", "Mlpfsa", "Mlpfsd", "Mlpfsg", "Mlpfsi", "Mlpfsl", "Mlpfsn", "Mlpmdl", "Mlpmpa",
    "Mlpmpd", "Mlpmpg", "Mlpmpi", "Mlpmpl", "Mlpmpn", "Mlpmsa", "Mlpmsan", "Mlpmsay", "Mlpmsd", "Mlpmsg", "Mlpmsi",
    "Mlpmsl", "Mlpmsn", "Mlpmsnn", "Mlpmsny", "Mlpnpa", "Mlpnpg", "Mlpnpi", "Mlpnpl", "Mlpnpn", "Mlpnsa", "Mlpnsg",
    "Mlpnsi", "Mlpnsl", "Mlpnsn", "Mlsfpa", "Mlsfsg", "Mlsfsi", "Mlsfsn", "Mlsmpi", "Mlsmsg", "Mlsmsi", "Mlsnsa",
    "Mlsnsi", "Mlsnsn", "Mrc", "Mro", "Npfpa", "Npfpd", "Npfpg", "Npfpi", "Npfpl", "Npfpn", "Npfsa", "Npfsd",
    "Npfsg", "Npfsi", "Npfsl", "Npfsn", "Npmda", "Npmdg", "Npmdn", "Npmpa", "Npmpd", "Npmpg", "Npmpi", "Npmpl",
    "Npmpn", "Npmsan", "Npmsay", "Npmsd", "Npmsg", "Npmsi", "Npmsl", "Npmsn", "Npnpn", "Npnsa", "Npnsd", "Npnsg",
    "Npnsi", "Npnsl", "Npnsn", "Pd-fda", "Pd-fpa", "Pd-fpd", "Pd-fpg", "Pd-fpi", "Pd-fpl", "Pd-fpn", "Pd-fsa",
    "Pd-fsd", "Pd-fsg", "Pd-fsi", "Pd-fsl", "Pd-fsn", "Pd-mda", "Pd-mdg", "Pd-mdi", "Pd-mdl", "Pd-mdn", "Pd-mpa",
    "Pd-mpd", "Pd-mpg", "Pd-mpi", "Pd-mpl", "Pd-mpn", "Pd-msa", "Pd-msd", "Pd-msg", "Pd-msi", "Pd-msl", "Pd-msn",
    "Pd-npa", "Pd-npd", "Pd-npg", "Pd-npi", "Pd-npl", "Pd-npn", "Pd-nsa", "Pd-nsd", "Pd-nsg", "Pd-nsi", "Pd-nsl",
    "Pd-nsn", "Pg-fda", "Pg-fdg", "Pg-fdi", "Pg-fdl", "Pg-fdn", "Pg-fpa", "Pg-fpd", "Pg-fpg", "Pg-fpi", "Pg-fpl",
    "Pg-fpn", "Pg-fsa", "Pg-fsd", "Pg-fsg", "Pg-fsi", "Pg-fsl", "Pg-fsn", "Pg-mda", "Pg-mdd", "Pg-mdg", "Pg-mdi",
    "Pg-mdl", "Pg-mdn", "Pg-mpa", "Pg-mpd", "Pg-mpg", "Pg-mpi", "Pg-mpl", "Pg-mpn", "Pg-msa", "Pg-msd", "Pg-msg",
    "Pg-msi", "Pg-msl", "Pg-msn", "Pg-nda", "Pg-ndd", "Pg-ndg", "Pg-ndn", "Pg-npa", "Pg-npd", "Pg-npg", "Pg-npi",
    "Pg-npl", "Pg-npn", "Pg-nsa", "Pg-nsd", "Pg-nsg", "Pg-nsi", "Pg-nsl", "Pg-nsn", "Pi-fdn", "Pi-fpa", "Pi-fpd",
    "Pi-fpg", "Pi-fpi", "Pi-fpl", "Pi-fpn", "Pi-fsa", "Pi-fsd", "Pi-fsg", "Pi-fsi", "Pi-fsl", "Pi-fsn", "Pi-mpa",
    "Pi-mpd", "Pi-mpg", "Pi-mpi", "Pi-mpl", "Pi-mpn", "Pi-msa", "Pi-msd", "Pi-msg", "Pi-msi", "Pi-msl", "Pi-msn",
    "Pi-npa", "Pi-npg", "Pi-npi", "Pi-npl", "Pi-npn", "Pi-nsa", "Pi-nsg", "Pi-nsi", "Pi-nsl", "Pi-nsn", "Pp1-da",
    "Pp1-dd", "Pp1-dg", "Pp1-di", "Pp1-pa", "Pp1-pd", "Pp1-pg", "Pp1-pi", "Pp1-pl", "Pp1-sa", "Pp1-sa--b",
    "Pp1-sa--y", "Pp1-sd", "Pp1-sd--y", "Pp1-sg", "Pp1-sg--y", "Pp1-si", "Pp1-sl", "Pp1-sn", "Pp1fpn", "Pp1mdn",
    "Pp1mpn", "Pp2-da", "Pp2-dd", "Pp2-di", "Pp2-pa", "Pp2-pd", "Pp2-pg", "Pp2-pi", "Pp2-pl", "Pp2-sa", "Pp2-sa--b",
    "Pp2-sa--y", "Pp2-sd", "Pp2-sd--y", "Pp2-sg", "Pp2-sg--y", "Pp2-si", "Pp2-sn", "Pp2fdn", "Pp2mpn", "Pp3fda",
    "Pp3fda--b", "Pp3fda--y", "Pp3fdd--y", "Pp3fdg--y", "Pp3fdi", "Pp3fdl", "Pp3fpa--b", "Pp3fpa--y", "Pp3fpd",
    "Pp3fpd--y", "Pp3fpg", "Pp3fpg--y", "Pp3fpi", "Pp3fpl", "Pp3fsa", "Pp3fsa--b", "Pp3fsa--y", "Pp3fsd",
    "Pp3fsd--y", "Pp3fsg", "Pp3fsg--y", "Pp3fsi", "Pp3fsl", "Pp3fsn", "Pp3mda", "Pp3mda--y", "Pp3mdd", "Pp3mdd--y",
    "Pp3mdg", "Pp3mdi", "Pp3mdl", "Pp3mdn", "Pp3mpa", "Pp3mpa--b", "Pp3mpa--y", "Pp3mpd", "Pp3mpd--y", "Pp3mpg",
    "Pp3mpg--y", "Pp3mpi", "Pp3mpl", "Pp3mpn", "Pp3msa", "Pp3msa--b", "Pp3msa--y", "Pp3msd", "Pp3msd--y", "Pp3msg",
    "Pp3msg--y", "Pp3msi", "Pp3msl", "Pp3msn", "Pp3nda--y", "Pp3npa--b", "Pp3npa--y", "Pp3npd--y", "Pp3npg",
    "Pp3npg--y", "Pp3npi", "Pp3npl", "Pp3nsa--b", "Pp3nsa--y", "Pp3nsd--y", "Pp3nsg", "Pp3nsg--y", "Pp3nsi",
    "Pp3nsl", "Pq-fda", "Pq-fdi", "Pq-fpa", "Pq-fpd", "Pq-fpg", "Pq-fpi", "Pq-fpl", "Pq-fpn", "Pq-fsa", "Pq-fsd",
    "Pq-fsg", "Pq-fsi", "Pq-fsl", "Pq-fsn", "Pq-mdg", "Pq-mdi", "Pq-mpa", "Pq-mpd", "Pq-mpg", "Pq-mpi", "Pq-mpl",
    "Pq-mpn", "Pq-msa", "Pq-msd", "Pq-msg", "Pq-msi", "Pq-msl", "Pq-msn", "Pq-npa", "Pq-npg", "Pq-npi", "Pq-npl",
    "Pq-npn", "Pq-nsa", "Pq-nsd", "Pq-nsg", "Pq-nsi", "Pq-nsl", "Pq-nsn", "Pr----sm", "Pr-fpa", "Pr-fpg", "Pr-fpl",
    "Pr-fsa", "Pr-fsg", "Pr-fsi", "Pr-fsl", "Pr-fsn", "Pr-mdn", "Pr-mpl", "Pr-mpn", "Pr-msa", "Pr-msd", "Pr-msg",
    "Pr-msi", "Pr-msn", "Pr-nsa", "Pr-nsd", "Pr-nsg", "Pr-nsi", "Pr-nsl", "Pr-nsn", "Ps1fdip", "Ps1fpap", "Ps1fpdp",
    "Ps1fpgp", "Ps1fpgs", "Ps1fpip", "Ps1fplp", "Ps1fpnd", "Ps1fpnp", "Ps1fpns", "Ps1fsap", "Ps1fsas", "Ps1fsdp",
    "Ps1fsds", "Ps1fsgp", "Ps1fsid", "Ps1fsip", "Ps1fsis", "Ps1fslp", "Ps1fsls", "Ps1fsnd", "Ps1fsnp", "Ps1fsns",
    "Ps1mdgd", "Ps1mdid", "Ps1mdns", "Ps1mpap", "Ps1mpdp", "Ps1mpgd", "Ps1mpgp", "Ps1mpgs", "Ps1mpip", "Ps1mplp",
    "Ps1mpnd", "Ps1mpnp", "Ps1mpns", "Ps1msap", "Ps1msas", "Ps1msds", "Ps1msgp", "Ps1msgs", "Ps1msis", "Ps1mslp",
    "Ps1msls", "Ps1msnd", "Ps1msnp", "Ps1msns", "Ps1npap", "Ps1npas", "Ps1npgp", "Ps1npgs", "Ps1nplp", "Ps1npnp",
    "Ps1nsap", "Ps1nsas", "Ps1nsdp", "Ps1nsgd", "Ps1nsgp", "Ps1nsgs", "Ps1nsip", "Ps1nslp", "Ps1nsls", "Ps1nsnd",
    "Ps1nsnp", "Ps1nsns", "Ps2fdnp", "Ps2fpap", "Ps2fpgp", "Ps2fpnp", "Ps2fpns", "Ps2fsad", "Ps2fsap", "Ps2fsds",
    "Ps2fsgd", "Ps2fsgp", "Ps2fsgs", "Ps2fsid", "Ps2fsip", "Ps2fslp", "Ps2fsnp", "Ps2fsns", "Ps2mpdp", "Ps2mpgp",
    "Ps2mpid", "Ps2mpnp", "Ps2mpns", "Ps2msap", "Ps2msas", "Ps2msgp", "Ps2msgs", "Ps2msip", "Ps2mslp", "Ps2msnp",
    "Ps2msns", "Ps2ndgd", "Ps2npap", "Ps2npnp", "Ps2npns", "Ps2nsap", "Ps2nsas", "Ps2nsgp", "Ps2nslp", "Ps2nsnp",
    "Ps2nsns", "Ps3fdnd", "Ps3fpap", "Ps3fpasf", "Ps3fpasm", "Ps3fpdp", "Ps3fpgp", "Ps3fpgsf", "Ps3fpgsm",
    "Ps3fpip", "Ps3fpisf", "Ps3fpism", "Ps3fplp", "Ps3fplsf", "Ps3fplsm", "Ps3fpnp", "Ps3fpnsf", "Ps3fpnsm",
    "Ps3fsap", "Ps3fsasf", "Ps3fsasm", "Ps3fsdp", "Ps3fsdsf", "Ps3fsdsm", "Ps3fsgd", "Ps3fsgp", "Ps3fsgsf",
    "Ps3fsgsm", "Ps3fsid", "Ps3fsisf", "Ps3fsism", "Ps3fslp", "Ps3fslsf", "Ps3fslsm", "Ps3fsnd", "Ps3fsnp",
    "Ps3fsnsf", "Ps3fsnsm", "Ps3fsnsn", "Ps3mdnd", "Ps3mdnsm", "Ps3mpap", "Ps3mpasf", "Ps3mpasm", "Ps3mpdp",
    "Ps3mpdsf", "Ps3mpdsm", "Ps3mpgp", "Ps3mpgsf", "Ps3mpgsm", "Ps3mpip", "Ps3mpism", "Ps3mplp", "Ps3mplsf",
    "Ps3mplsm", "Ps3mpnd", "Ps3mpnp", "Ps3mpnsf", "Ps3mpnsm", "Ps3msad", "Ps3msap", "Ps3msasf", "Ps3msasm",
    "Ps3msdp", "Ps3msdsf", "Ps3msdsm", "Ps3msgd", "Ps3msgp", "Ps3msgsf", "Ps3msgsm", "Ps3msip", "Ps3msisf",
    "Ps3msism", "Ps3mslp", "Ps3mslsf", "Ps3mslsm", "Ps3msnd", "Ps3msnp", "Ps3msnsf", "Ps3msnsm", "Ps3ndad",
    "Ps3ndnsf", "Ps3npap", "Ps3npasf", "Ps3npasm", "Ps3npgd", "Ps3npgp", "Ps3npgsf", "Ps3npgsm", "Ps3nplp",
    "Ps3nplsm", "Ps3npnp", "Ps3npnsm", "Ps3nsad", "Ps3nsap", "Ps3nsasf", "Ps3nsasm", "Ps3nsdp", "Ps3nsdsf",
    "Ps3nsgp", "Ps3nsgsf", "Ps3nsgsm", "Ps3nsisf", "Ps3nsism", "Ps3nsld", "Ps3nslp", "Ps3nslsf", "Ps3nslsm",
    "Ps3nsnd", "Ps3nsnp", "Ps3nsnsf", "Ps3nsnsm", "Px------y", "Px---a", "Px---a--b", "Px---d", "Px---d--y",
    "Px---g", "Px---i", "Px---l", "Px-fda", "Px-fpa", "Px-fpd", "Px-fpg", "Px-fpi", "Px-fpl", "Px-fsa", "Px-fsd",
    "Px-fsg", "Px-fsi", "Px-fsl", "Px-mda", "Px-mpa", "Px-mpd", "Px-mpg", "Px-mpi", "Px-mpl", "Px-msa", "Px-msd",
    "Px-msg", "Px-msi", "Px-msl", "Px-msn", "Px-npa", "Px-npd", "Px-npg", "Px-npi", "Px-npl", "Px-nsa", "Px-nsd",
    "Px-nsg", "Px-nsi", "Px-nsl", "Px-nsn", "Pz-fpg", "Pz-fpi", "Pz-fsa", "Pz-fsg", "Pz-fsn", "Pz-mpg", "Pz-msa",
    "Pz-msd", "Pz-msg", "Pz-msi", "Pz-msl", "Pz-msn", "Pz-nsa", "Pz-nsg", "Pz-nsl", "Pz-nsn", "Q", "Sa", "Sd", "Sg",
    "Si", "Sl", "Va-c", "Va-f1d-n", "Va-f1p-n", "Va-f1s-n", "Va-f2d-n", "Va-f2p-n", "Va-f2s-n", "Va-f3d-n",
    "Va-f3p-n", "Va-f3s-n", "Va-m2p", "Va-m2s", "Va-n", "Va-p-df", "Va-p-dm", "Va-p-dn", "Va-p-pf", "Va-p-pm",
    "Va-p-pn", "Va-p-sf", "Va-p-sm", "Va-p-sn", "Va-r1d-n", "Va-r1d-y", "Va-r1p-n", "Va-r1p-y", "Va-r1s-n",
    "Va-r1s-y", "Va-r2d-n", "Va-r2p-n", "Va-r2p-y", "Va-r2s-n", "Va-r2s-y", "Va-r3d-n", "Va-r3d-y", "Va-r3p-n",
    "Va-r3p-y", "Va-r3s-n", "Va-r3s-y", "X", "Xf", "Y", "Z", "_SP",
)  # fmt: skip

#: The seven accent marks wty writes; NEVER U+030C, where the mark IS the letter (c/s/z).
_TONE_MARKS = frozenset({"\u0300", "\u0301", "\u0302", "\u0304", "\u030f", "\u0311", "\u0323"})
#: Bases the notation marks: the vowels and syllabic r. The schwa is a LETTER here, not a base.
_TONE_BASES = frozenset("aeiourAEIOUR")

#: Slovenian speaker cues; the filter runs after the shared NFC normaliser.
SL_SPEAKER_PATTERN = (
    r"^[A-Z\u00c0-\u00d6\u00d8-\u00de\u010c\u0160\u017d]"
    r"[A-Z\u00c0-\u00d6\u00d8-\u00de\u010c\u0160\u017d0-9 .'-]*"
    r"[A-Z\u00c0-\u00d6\u00d8-\u00de\u010c\u0160\u017d]:\s*"
)
#: The S10 default for Slovenian: the shared Latin parts with the Slovenian speaker rule.
SL_SUBTITLE_REGEX = "|".join(
    (BRACKETS_PATTERN, PARENS_PATTERN, MUSIC_PATTERN, SL_SPEAKER_PATTERN, DIALOGUE_DASH_PATTERN)
)


def sl_tone_fold(text: str) -> str:
    """Drop a wty accent mark from a vowel or ``r``; the caron is a letter and always stays."""
    out: list[str] = []
    for char in unicodedata.normalize("NFD", text):
        if char in _TONE_MARKS and out and unicodedata.normalize("NFC", out[-1])[:1] in _TONE_BASES:
            continue
        out.append(char)
    return unicodedata.normalize("NFC", "".join(out))
