"""Slovenian language profile: every field constructed from the shared spaCy substrate (spec Appendix E)."""

from __future__ import annotations

import dataclasses

from anki_miner.config.config import AudioSourceEntry
from anki_miner.languages._spaced.audio import spaced_audio_candidates, spaced_speakable
from anki_miner.languages._spaced.availability import spaced_missing_reason
from anki_miner.languages._spaced.fields import (
    ASPECT_PAIR_FIELD,
    NOUN_GENDER_FIELD,
    POS_FIELD,
    spaced_card_fields,
    spaced_scoped_defaults,
)
from anki_miner.languages._spaced.form_of import WTY_TAG_TO_UPOS
from anki_miner.languages._spaced.grammar_hook import GrammarTagHook
from anki_miner.languages._spaced.keys import CasefoldDictKeys, spaced_dedup_fold
from anki_miner.languages._spaced.morphology import LatinLookupStrategy, SpacedMinedForm
from anki_miner.languages._spaced.pos import UPOS_LABELS
from anki_miner.languages._spaced.render import PosHook
from anki_miner.languages._spaced.script import LatinScript, nfc_normalize
from anki_miner.languages._spaced.sentence import sentence_rules
from anki_miner.languages._spaced.style import SPACED_CONTENT_STYLE
from anki_miner.languages.profile import AudioDefaults, CaptionLangs, LanguageProfile, PosDefaults
from anki_miner.languages.sl.abbreviations import SL_ABBREVIATIONS
from anki_miner.languages.sl.catalog import SL_CATALOG
from anki_miner.languages.sl.morphology import (
    SL_ALLOWED_POS,
    SL_CLOSERS,
    SL_EXCLUDED_SUBTYPES,
    SL_MODEL_PACKAGE,
    SL_OPENERS,
    SL_SUBTITLE_REGEX,
    sl_tone_fold,
)
from anki_miner.languages.sl.parser import create_parser

__all__ = ["build_profile"]

SL_SMOKE_SENTENCE = "Študent je včeraj prebral zanimivo knjigo."
#: No noun_plural: Slovenian has three numbers (singular, dual, plural), so a two-way Plural field
#: would print two thirds of a paradigm and hide the dual, which is the number a learner most needs
#: told. The dictionary would rarely fill it anyway - 6.8 % of noun rows carry a Grammar head line.
SL_EXTRA_CARD_FIELDS = (POS_FIELD, NOUN_GENDER_FIELD, ASPECT_PAIR_FIELD)
SL_CARD_FIELDS = spaced_card_fields(SL_EXTRA_CARD_FIELDS)


#: The two classes the Definition splice treats as one: a Slovenian adverb in -o is the neuter singular
#: of its adjective, and wty-sl-en files it only as that form. 43 of the top 3,000 list words end in -o
#: and reach no row but their adjective's (``lepo``, ``dolgo``, ``ravno``, ``težko``, ``očitno``,
#: ``odlično``). The tagger calls about half of them ADV in a line (``Odlično, hvala.``, ``Dolgo te nisem
#: videl.``), and those cards would otherwise lose their only meaning.
_ADJ_ADV = frozenset({"ADJ", "ADV"})


class SlovenianDictKeys(CasefoldDictKeys):
    """The Latin key fold, whose Definition splice reads only the target rows of the token's own class.

    Once folded, wty-sl-en's form rows meet words of another class: ``mȃma`` is a form of the verb
    ``imeti``, ``prav`` of the noun ``pravo``, ``stanovanje`` the noun-from-verb of ``stanovati``. The
    front repair already refuses those targets (``same_pos`` in ``parser.py``), and ``storage`` asks
    :meth:`splice_row_fits` before it splices a target's lemma row into the definition, so a noun card
    never reads "to have". A target with no row that fits adds nothing, and the hit becomes a miss.
    """

    def splice_row_fits(self, tags: str, pos: str) -> bool:
        """A target lemma row of the token's own class, where an adjective and an adverb count as one."""
        row_pos = WTY_TAG_TO_UPOS.get(tags.split(" ", 1)[0])
        return row_pos == pos or {row_pos, pos} <= _ADJ_ADV


#: NFC + the accent-notation fold + casefold. wty-sl-en writes 41,212 of its keys - nearly all of them
#: form rows - in accent notation (``čȃkam``, ``učím``, ``mȃma``), which no Slovenian text spells, so
#: the fold runs inside the key at import and at query alike (the ro precedent). It keeps the caron and
#: is the identity on Slovenian spelling, so an index imported before it keeps answering as it did until
#: wty-sl-en is re-imported. No NFKC: no term carries a ligature.
SL_KEYS = SlovenianDictKeys(extra_fold=sl_tone_fold)

SL_AUDIO = AudioDefaults(
    # gTTS has no Slovenian voice (tts_langs() 2.5.4), so the synthetic leg is Microsoft Edge
    # read-aloud (seam-edgetts, D14). Petra is the female GA voice; sl-SI-RokNeural is the male one,
    # and a user who prefers it can add the row in Settings -> Word Audio.
    gtts_lang="",
    edge_voice="sl-SI-PetraNeural",
    # Namespaced stems: the stem doubles as the Anki media filename. The Google prefixes keep the
    # shared shape even with no Google leg; the Edge fetcher namespaces its own files by voice.
    cache_stem_prefix="googletts_sl",
    sentence_cache_stem_prefix="sentencetts_sl",
    custom_fetcher_language="sl",
    papago_speaker=None,
    default_chain=(AudioSourceEntry(kind="edgetts"),),
    candidates=spaced_audio_candidates,
    speakable=spaced_speakable,
)


def build_profile() -> LanguageProfile:
    """Build the Slovenian profile. Never calls ``registry.get_profile`` (non-reentrant lock)."""
    return LanguageProfile(
        code="sl",
        display_name="Slovenščina",
        create_parser=create_parser,
        mined_form=SpacedMinedForm(),
        lookup=LatinLookupStrategy(),
        reading=None,
        sentence_annotator=None,
        script=LatinScript(),
        # slv is ISO 639-2; sl and the English name are the ko shape (D20).
        audio_track_codes=frozenset({"slv", "sl", "slovenian"}),
        # Latin-2 before cp1250: the two differ on š ž Š Ž, so a Latin-2 file read as cp1250 mines ąola and
        # moąki. A cp1250 file fails the Latin-2 leg on its first š ž „ “ – …, which are C1 controls there.
        import_encodings=("utf-8-sig", "iso8859_2", "cp1250"),
        scoped_defaults=spaced_scoped_defaults(
            subtitle_langs="sl",
            audio=SL_AUDIO,
            allowed_pos=SL_ALLOWED_POS,
            excluded_subtypes=SL_EXCLUDED_SUBTYPES,
            card_fields=SL_CARD_FIELDS,
            subtitle_regex=SL_SUBTITLE_REGEX,
        ),
        # Slovenian writes „…“ and »…«, which the shared Latin pairs do not track, so a quote holding two
        # sentences split inside it ('„Dobro jutro.' | 'Kako si?“ je vprašala.'); the da/lt/hu override.
        sentence_rules=dataclasses.replace(sentence_rules(SL_ABBREVIATIONS), openers=SL_OPENERS, closers=SL_CLOSERS),
        normalize=nfc_normalize,
        dict_keys=SL_KEYS,
        audio=SL_AUDIO,
        asr_language="sl",
        captions=CaptionLangs(
            primary="sl",
            codes=("sl",),
            orig_codes=("sl-orig",),
            audio_pattern="^sl(-|$)",
            bare_fallback=True,
        ),
        pos_defaults=PosDefaults(
            allowed_pos=SL_ALLOWED_POS, excluded_subtypes=SL_EXCLUDED_SUBTYPES, labels=UPOS_LABELS
        ),
        catalog=SL_CATALOG,
        capabilities=frozenset({"pos_tag", "noun_gender", "aspect_pairs", "lemmatised_frequency"}),
        card_field_defaults=SL_CARD_FIELDS,
        render_hooks=(
            PosHook(),
            # Shared English labels: the learner reading the back is an English speaker, and
            # Slovenian has no articles to build a gender pair from. The partner fold strips the
            # dictionary's accent notation.
            GrammarTagHook(("noun_gender", "aspect_pair"), partner_fold=sl_tone_fold),
        ),
        content_style=SPACED_CONTENT_STYLE,
        unavailable_reason=spaced_missing_reason("sl", "Slovenian", SL_MODEL_PACKAGE),
        extra_card_fields=SL_EXTRA_CARD_FIELDS,
        smoke_sentence=SL_SMOKE_SENTENCE,
        english_name="Slovenian",
        # S3 is empty (D18): Slovenian has no articles, so a deck front carries no leading word the
        # lemma lacks.
        dedup_fold=spaced_dedup_fold(SL_KEYS),
    )
