"""Hebrew language profile (spec Appendix F.2): no engine, no pack, no pip extra.

There is no Hebrew tagger worth shipping -- every candidate was disqualified (hebspacy pins
spacy 3.2.2 plus torch, hebpipe needs flair, YAP is a Go binary, Dicta is a cloud API at parse
time, hspell's data is GPL-2-only), and no Hebrew spaCy pipeline exists. So the card front comes
from the dictionary the user installs anyway: ``wty-he-en`` keys 146,419 inflected forms as
``non-lemma`` rows naming the lemma they belong to, and ``morphology.HebrewLemmaPass`` reads them
through the R36 ``form_lookup`` seam.

What is Hebrew-specific and where it lives: the niqqud fold and the script gate in ``script.py``,
the proclitic ladder in ``proclitics.py``, the function-word tier in ``stopwords.py``, the regex
tokenizer in ``tokenizer.py``, the resolver in ``morphology.py``, the head-line card fields in
``render.py``. Nothing downloads; ``unavailable_reason`` stays unset because a Hebrew install can
always mine.
"""

from __future__ import annotations

from types import MappingProxyType

from anki_miner.languages._spaced.fields import spaced_card_fields, spaced_scoped_defaults
from anki_miner.languages.he.audio import HE_AUDIO
from anki_miner.languages.he.catalog import HE_CATALOG
from anki_miner.languages.he.morphology import HebrewMinedForm, HebrewReadingSupport
from anki_miner.languages.he.parser import create_parser
from anki_miner.languages.he.pos import HE_ALLOWED_POS, HE_EXCLUDED_SUBTYPES, HE_POS_LABELS
from anki_miner.languages.he.proclitics import HebrewLookupStrategy
from anki_miner.languages.he.render import HE_EXTRA_CARD_FIELDS, HE_RENDER_HOOKS
from anki_miner.languages.he.script import (
    HE_SENTENCE_RULES,
    HE_SUBTITLE_REGEX,
    HebrewDictKeys,
    HebrewScript,
    he_fold,
    he_normalize,
)
from anki_miner.languages.he.style import HE_CONTENT_STYLE
from anki_miner.languages.profile import CaptionLangs, LanguageProfile, PosDefaults

__all__ = ["build_profile", "HE_CARD_FIELDS", "HE_SMOKE_SENTENCE"]

#: The bundle smoke line (``ANKI_MINER_SMOKE=he``): "The boy read an interesting book yesterday."
HE_SMOKE_SENTENCE = "הילד קרא ספר מעניין אתמול."

HE_KEYS = HebrewDictKeys()
#: The spaced defaults plus the one Hebrew difference: the vocalised headword is the reading every
#: surveyed deck shows on the back, so ``expression_reading`` ships mapped (the ar shape).
HE_CARD_FIELDS = MappingProxyType({**spaced_card_fields(HE_EXTRA_CARD_FIELDS), "expression_reading": "Reading"})


def build_profile() -> LanguageProfile:
    """Build the Hebrew profile. Never calls ``registry.get_profile`` (non-reentrant lock)."""
    return LanguageProfile(
        code="he",
        display_name="עברית",
        create_parser=create_parser,
        mined_form=HebrewMinedForm(),
        lookup=HebrewLookupStrategy(),
        reading=HebrewReadingSupport(),
        sentence_annotator=None,
        script=HebrewScript(),
        # "heb" is ISO 639-2; "iw" is the legacy 639-1 code an Israeli rip still writes, and the
        # English name turns up in hand-tagged files.
        audio_track_codes=frozenset({"heb", "he", "iw", "hebrew"}),
        # cp1255 is the logical-order Windows codepage that also encodes niqqud. ISO-8859-8 is
        # deliberately absent: it is the VISUAL-order label, Python ships no -8-I codec, and a
        # pointed word cannot even be encoded in it.
        import_encodings=("utf-8-sig", "cp1255"),
        scoped_defaults=spaced_scoped_defaults(
            subtitle_langs="iw",
            audio=HE_AUDIO,
            allowed_pos=HE_ALLOWED_POS,
            excluded_subtypes=HE_EXCLUDED_SUBTYPES,
            card_fields=HE_CARD_FIELDS,
            subtitle_regex=HE_SUBTITLE_REGEX,
        ),
        sentence_rules=HE_SENTENCE_RULES,
        normalize=he_normalize,
        dict_keys=HE_KEYS,
        audio=HE_AUDIO,
        asr_language="he",
        # Five captioned Israeli videos probed with yt-dlp returned the keys "iw" and "iw-orig"
        # and audio-format language "iw", with no "he" key on any of them; "he" stays in the
        # tuples as forward compatibility, not as an observed key.
        captions=CaptionLangs(
            primary="iw",
            codes=("iw", "he"),
            orig_codes=("iw-orig", "he-orig"),
            audio_pattern="^(iw|he)(-|$)",
            bare_fallback=True,
        ),
        pos_defaults=PosDefaults(
            allowed_pos=HE_ALLOWED_POS, excluded_subtypes=HE_EXCLUDED_SUBTYPES, labels=HE_POS_LABELS
        ),
        catalog=HE_CATALOG,
        # "rtl" is shared by fa/ar/he and "word_root" by ar/he/id; the rest gate this language's
        # own card-field rows. No "lemmatised_frequency": there is no tagger to lemmatise with.
        capabilities=frozenset(
            {
                "hebrew_transliteration",
                "hebrew_binyan",
                "word_root",
                "noun_gender",
                "noun_plural",
                "pos_tag",
                "rtl",
            }
        ),
        card_field_defaults=HE_CARD_FIELDS,
        render_hooks=HE_RENDER_HOOKS,
        content_style=HE_CONTENT_STYLE,
        extra_card_fields=HE_EXTRA_CARD_FIELDS,
        smoke_sentence=HE_SMOKE_SENTENCE,
        english_name="Hebrew",
        # "" means the code itself: the wty edition and the en.wiktionary section are both "he".
        wiktionary_code="",
        # One function at both seams: a vocalised Anki front and the mined unvocalised lemma are
        # one word everywhere they are compared.
        dedup_fold=he_fold,
    )
