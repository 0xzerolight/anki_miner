"""Vietnamese language profile (spec Appendix C.4): underthesea over the shared spaced substrate."""

from __future__ import annotations

from anki_miner.config.config import AudioSourceEntry
from anki_miner.languages._spaced.audio import spaced_audio_candidates, spaced_speakable
from anki_miner.languages._spaced.fields import spaced_card_fields, spaced_scoped_defaults
from anki_miner.languages._spaced.morphology import SpacedMinedForm
from anki_miner.languages._spaced.style import SPACED_CONTENT_STYLE
from anki_miner.languages.profile import AudioDefaults, CaptionLangs, CardFieldSpec, LanguageProfile, PosDefaults
from anki_miner.languages.vi.availability import vi_missing_reason
from anki_miner.languages.vi.catalog import VI_CATALOG
from anki_miner.languages.vi.keys import VI_KEYS, vi_fold_term
from anki_miner.languages.vi.morphology import VietnameseLookup
from anki_miner.languages.vi.parser import create_parser
from anki_miner.languages.vi.pos import VI_ALLOWED_POS, VI_EXCLUDED_SUBTYPES, VI_POS_LABELS
from anki_miner.languages.vi.render import HANVIET_FIELD, VI_RENDER_HOOKS
from anki_miner.languages.vi.script import VI_SENTENCE_RULES, VI_SUBTITLE_REGEX, VietnameseScript, vi_normalize

__all__ = ["build_profile"]

VI_SMOKE_SENTENCE = "Hôm nay trời đẹp quá."
VI_EXTRA_CARD_FIELDS: tuple[CardFieldSpec, ...] = (HANVIET_FIELD,)
VI_CARD_FIELDS = spaced_card_fields(VI_EXTRA_CARD_FIELDS)

VI_AUDIO = AudioDefaults(
    gtts_lang="vi",
    # Namespaced stems keyed on the profile code: the stem doubles as the Anki media filename.
    cache_stem_prefix="googletts_vi",
    sentence_cache_stem_prefix="sentencetts_vi",
    custom_fetcher_language="vi",
    papago_speaker=None,
    default_chain=(AudioSourceEntry(kind="googletts"),),
    candidates=spaced_audio_candidates,
    speakable=spaced_speakable,
)


def build_profile() -> LanguageProfile:
    """Build the Vietnamese profile. Never calls ``registry.get_profile`` (non-reentrant lock)."""
    return LanguageProfile(
        code="vi",
        display_name="Tiếng Việt",
        create_parser=create_parser,
        mined_form=SpacedMinedForm(),
        lookup=VietnameseLookup(),
        reading=None,
        sentence_annotator=None,
        script=VietnameseScript(),
        audio_track_codes=frozenset({"vie", "vi", "vietnamese"}),
        # cp1258 decodes to base letter + combining tone; vi_normalize composes it (S5).
        # VNI, VISCII and TCVN3 have no Python codec: a documented gap.
        import_encodings=("utf-8-sig", "cp1258"),
        scoped_defaults=spaced_scoped_defaults(
            subtitle_langs="vi",
            audio=VI_AUDIO,
            allowed_pos=VI_ALLOWED_POS,
            excluded_subtypes=VI_EXCLUDED_SUBTYPES,
            card_fields=VI_CARD_FIELDS,
            subtitle_regex=VI_SUBTITLE_REGEX,
        ),
        sentence_rules=VI_SENTENCE_RULES,
        normalize=vi_normalize,
        dict_keys=VI_KEYS,
        audio=VI_AUDIO,
        asr_language="vi",
        captions=CaptionLangs(
            primary="vi",
            codes=("vi",),
            orig_codes=("vi-orig",),
            audio_pattern="^vi(-|$)",
            bare_fallback=True,
        ),
        pos_defaults=PosDefaults(
            allowed_pos=VI_ALLOWED_POS, excluded_subtypes=VI_EXCLUDED_SUBTYPES, labels=VI_POS_LABELS
        ),
        catalog=VI_CATALOG,
        capabilities=frozenset({"hanviet"}),
        card_field_defaults=VI_CARD_FIELDS,
        render_hooks=VI_RENDER_HOOKS,
        content_style=SPACED_CONTENT_STYLE,
        unavailable_reason=vi_missing_reason,
        extra_card_fields=VI_EXTRA_CARD_FIELDS,
        smoke_sentence=VI_SMOKE_SENTENCE,
        english_name="Vietnamese",
        wiktionary_code="",
        dedup_fold=vi_fold_term,
    )
