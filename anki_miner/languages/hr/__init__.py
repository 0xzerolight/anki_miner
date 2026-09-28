"""Croatian language profile: every field constructed from the shared spaCy substrate (spec Appendix E)."""

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
from anki_miner.languages._spaced.grammar_hook import GrammarTagHook
from anki_miner.languages._spaced.keys import CasefoldDictKeys, spaced_dedup_fold
from anki_miner.languages._spaced.morphology import LatinLookupStrategy, SpacedMinedForm
from anki_miner.languages._spaced.pos import UPOS_LABELS
from anki_miner.languages._spaced.render import PosHook
from anki_miner.languages._spaced.script import LatinScript
from anki_miner.languages._spaced.sentence import sentence_rules
from anki_miner.languages._spaced.style import SPACED_CONTENT_STYLE
from anki_miner.languages.hr.catalog import HR_CATALOG
from anki_miner.languages.hr.morphology import (
    HR_ABBREVIATIONS,
    HR_ALLOWED_POS,
    HR_CLOSERS,
    HR_EXCLUDED_SUBTYPES,
    HR_MODEL_PACKAGE,
    HR_OPENERS,
    HR_SUBTITLE_REGEX,
    hr_normalize,
    hr_tone_fold,
)
from anki_miner.languages.hr.parser import create_parser
from anki_miner.languages.profile import AudioDefaults, CaptionLangs, LanguageProfile, PosDefaults

__all__ = ["build_profile"]

HR_SMOKE_SENTENCE = "Student je ju\u010der pro\u010ditao zanimljivu knjigu."
HR_EXTRA_CARD_FIELDS = (POS_FIELD, NOUN_GENDER_FIELD, ASPECT_PAIR_FIELD)
HR_CARD_FIELDS = spaced_card_fields(HR_EXTRA_CARD_FIELDS)
#: NFC + the tone fold + casefold. wty-sh-en keys every lemma row plain, but its form rows name their
#: targets with tone marks (``primati`` names ``prímiti``), which no Croatian text spells, so the fold runs
#: inside the key and the Definition splice reaches the plain headword (the sl precedent). It keeps
#: c-caron, c-acute, s-caron, z-caron and d-bar, and is the identity on Croatian spelling: an index imported
#: before it keeps answering, and a re-import moves only the 15 wty-sh-en rows whose key carries a mark (the
#: toned-letter ``char`` rows and ``ciudad de méxico``). No NFKC: no term carries a digraph ligature, and
#: ``normalize`` folds one before the ladder runs.
HR_KEYS = CasefoldDictKeys(extra_fold=hr_tone_fold)

HR_AUDIO = AudioDefaults(
    gtts_lang="hr",
    # Namespaced stems: the stem doubles as the Anki media filename.
    cache_stem_prefix="googletts_hr",
    sentence_cache_stem_prefix="sentencetts_hr",
    custom_fetcher_language="hr",
    papago_speaker=None,
    # Wiktionary recordings lead once the wiktionary audio kind exists (Stage W); Google TTS until then.
    default_chain=(AudioSourceEntry(kind="googletts"),),
    candidates=spaced_audio_candidates,
    speakable=spaced_speakable,
)


def build_profile() -> LanguageProfile:
    """Build the Croatian profile. Never calls ``registry.get_profile`` (non-reentrant lock)."""
    return LanguageProfile(
        code="hr",
        display_name="Hrvatski",
        create_parser=create_parser,
        mined_form=SpacedMinedForm(),
        lookup=LatinLookupStrategy(),
        reading=None,
        sentence_annotator=None,
        script=LatinScript(),
        # hrv is ISO 639-2, scr the legacy Serbo-Croatian-Roman tag muxers still write.
        audio_track_codes=frozenset({"hrv", "scr", "hr", "croatian"}),
        # Latin-2 before cp1250: the two differ on š ž Š Ž, so a Latin-2 file read as cp1250 mines ąutjeti
        # and ľelią. A cp1250 file fails the Latin-2 leg on its first š ž „ “ – …, which are C1 controls there.
        import_encodings=("utf-8-sig", "iso8859_2", "cp1250"),
        scoped_defaults=spaced_scoped_defaults(
            subtitle_langs="hr",
            audio=HR_AUDIO,
            allowed_pos=HR_ALLOWED_POS,
            excluded_subtypes=HR_EXCLUDED_SUBTYPES,
            card_fields=HR_CARD_FIELDS,
            subtitle_regex=HR_SUBTITLE_REGEX,
        ),
        sentence_rules=dataclasses.replace(sentence_rules(HR_ABBREVIATIONS), openers=HR_OPENERS, closers=HR_CLOSERS),
        normalize=hr_normalize,
        dict_keys=HR_KEYS,
        audio=HR_AUDIO,
        asr_language="hr",
        captions=CaptionLangs(
            primary="hr",
            codes=("hr",),
            orig_codes=("hr-orig",),
            audio_pattern="^hr(-|$)",
            bare_fallback=True,
        ),
        pos_defaults=PosDefaults(
            allowed_pos=HR_ALLOWED_POS, excluded_subtypes=HR_EXCLUDED_SUBTYPES, labels=UPOS_LABELS
        ),
        catalog=HR_CATALOG,
        capabilities=frozenset({"pos_tag", "noun_gender", "aspect_pairs", "lemmatised_frequency"}),
        card_field_defaults=HR_CARD_FIELDS,
        render_hooks=(
            PosHook(),
            # Shared English labels: the learner reading the back is an English speaker, and Croatian has no
            # articles to build a gender pair from. The partner fold strips the dictionary's tone marks.
            GrammarTagHook(("noun_gender", "aspect_pair"), partner_fold=hr_tone_fold),
        ),
        content_style=SPACED_CONTENT_STYLE,
        unavailable_reason=spaced_missing_reason("hr", "Croatian", HR_MODEL_PACKAGE),
        extra_card_fields=HR_EXTRA_CARD_FIELDS,
        smoke_sentence=HR_SMOKE_SENTENCE,
        english_name="Croatian",
        # R28: Wiktionary files Croatian under Serbo-Croatian; only the dictionary says sh.
        wiktionary_code="sh",
        # S3 is empty: Croatian has no articles, so a deck front carries no leading word the lemma lacks.
        dedup_fold=spaced_dedup_fold(HR_KEYS),
    )
