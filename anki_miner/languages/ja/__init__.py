"""The Japanese profile — today's behaviour, expressed as a LanguageProfile.

Every field delegates to an existing implementation in ``anki_miner.services``
/ ``anki_miner.utils`` (wrap-in-place, spec 3), so building this profile can
never change a Japanese output. Built lazily by
``anki_miner.languages.registry``; nothing imports Qt at module level.
"""

from __future__ import annotations

from typing import Any

from anki_miner.config.config import AnkiMinerConfig
from anki_miner.languages.ja.support import (
    JaDictKeys,
    JaLookupStrategy,
    JaMinedForm,
    JaReadingSupport,
    JaScriptSupport,
    JaSentenceAnnotator,
)
from anki_miner.languages.ja.text import ja_phrase_wrap
from anki_miner.languages.profile import (
    AudioDefaults,
    CaptionLangs,
    ContentTextStyle,
    LanguageProfile,
    PosDefaults,
    SentenceRules,
)
from anki_miner.languages.switching import LANGUAGE_SCOPED_FIELDS
from anki_miner.services.reading import sentence_splitter
from anki_miner.services.resource_catalog import RECOMMENDED_DEFAULT_SET
from anki_miner.services.sentence_tts_fetcher import PAPAGO_SPEAKER_JA
from anki_miner.services.subtitle_parser import SubtitleParserService
from anki_miner.utils.audio_track_detector import JAPANESE_LANGUAGE_CODES
from anki_miner.utils.ja_normalize import normalize_for_tokenization

__all__ = ["JA_AUDIO", "build_profile"]

#: Copied verbatim from gui/app.py::_LANGUAGE_SMOKE_LINES["ja"].
JA_SMOKE_SENTENCE = "今日は良い天気ですね。"

#: ja keeps today's cache stem literals ("googletts_…", "sentencetts_…"), so
#: existing cached audio files stay valid byte-for-byte. ``papago_speaker`` is
#: the JA voice explicitly: the factory used to coerce a missing speaker to it,
#: which would have read Chinese sentences in Japanese once zh registered.
JA_AUDIO = AudioDefaults(
    gtts_lang="ja",
    cache_stem_prefix="googletts",
    sentence_cache_stem_prefix="sentencetts",
    custom_fetcher_language="ja",
    papago_speaker=PAPAGO_SPEAKER_JA,
    default_chain=AnkiMinerConfig().expression_audio_chain,
    candidates=None,
)


def _create_parser(*args: Any, **kwargs: Any) -> SubtitleParserService:
    """Build the unchanged JA parser (module attribute, so tests can patch it)."""
    return SubtitleParserService(*args, **kwargs)


def _scoped_defaults() -> dict[str, object]:
    """Derive ja's scoped values from the dataclass defaults (never hand-written)."""
    base = AnkiMinerConfig()
    return {name: getattr(base, name) for name in LANGUAGE_SCOPED_FIELDS}


def build_profile() -> LanguageProfile:
    """Return the Japanese profile. Called once per process via the registry."""
    base = AnkiMinerConfig()
    return LanguageProfile(
        code="ja",
        display_name="日本語",
        create_parser=_create_parser,
        mined_form=JaMinedForm(),
        lookup=JaLookupStrategy(),
        reading=JaReadingSupport(),
        sentence_annotator=JaSentenceAnnotator(),
        script=JaScriptSupport(),
        audio_track_codes=JAPANESE_LANGUAGE_CODES,
        import_encodings=("utf-8-sig", "cp932", "euc_jp"),
        scoped_defaults=_scoped_defaults(),
        sentence_rules=SentenceRules(
            terminators=sentence_splitter._HARD_TERMINATORS,
            ellipses=sentence_splitter._ELLIPSIS,
            openers=sentence_splitter._OPENERS,
            closers=sentence_splitter._CLOSERS,
            space_aware=False,
        ),
        normalize=normalize_for_tokenization,
        dict_keys=JaDictKeys(),
        audio=JA_AUDIO,
        asr_language="ja",
        captions=CaptionLangs(
            primary="ja",
            codes=("ja",),
            orig_codes=("ja-orig",),
            audio_pattern="^ja(-|$)",
            # The bare automatic caption is the LAST-RESORT leg the pre-existing
            # fetcher behaviour includes: still after "ja-orig" and after the
            # manual track, but a video whose metadata carries no "*-orig" key
            # at all and reports language "ja" is accepted rather than rejected.
            bare_fallback=True,
        ),
        pos_defaults=PosDefaults(
            allowed_pos=tuple(base.allowed_pos),
            excluded_subtypes=tuple(base.excluded_subtypes),
        ),
        catalog=RECOMMENDED_DEFAULT_SET,
        capabilities=frozenset({"pitch", "furigana", "kana_filters", "name_wordsets", "deinflection"}),
        card_field_defaults=dict(base.anki_fields),
        render_hooks=(),
        content_style=ContentTextStyle(font_role="japanese", families=(), wrap=ja_phrase_wrap),
        english_name="Japanese",
        smoke_sentence=JA_SMOKE_SENTENCE,
    )
