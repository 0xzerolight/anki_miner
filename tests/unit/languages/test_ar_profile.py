"""The Arabic profile: registration, the data-only pack, defaults, keys, audio and the card fields (spec C.1)."""

from __future__ import annotations

import tomllib
from pathlib import Path

from anki_miner.config import AnkiMinerConfig
from anki_miner.config.config import _LANGUAGE_CODES
from anki_miner.languages import AVAILABLE_LANGUAGES
from anki_miner.languages.ar.morphology import (
    AR_ALLOWED_POS,
    ArabicLookupStrategy,
    ArabicMinedForm,
    ArabicReadingSupport,
)
from anki_miner.languages.ar.render import AR_EXTRA_CARD_FIELDS, ArabicCardHook
from anki_miner.languages.ar.script import AR_SUBTITLE_REGEX, ArabicDictKeys, ArabicScript, ar_normalize
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.services.language_pack_installer import load_pack

ROOT = Path(__file__).resolve().parents[3]
ZIP_URL = (
    "https://github.com/CAMeL-Lab/camel-tools-data/releases/download/2022.03.21/"
    "morphology_db_calima-msa-r13-0.4.0.zip"
)


def test_registration_without_an_extra():
    assert "ar" in AVAILABLE_LANGUAGES and _LANGUAGE_CODES == AVAILABLE_LANGUAGES
    extras = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["optional-dependencies"]
    assert "ar" not in extras and not any("[ar]" in item for item in extras["languages"])


def test_the_pack_is_the_calima_zip_pinned_twice():
    pack = load_pack("ar")
    assert pack is not None and pack.requires == () and pack.approx_download_mb == 41
    (component,) = pack.components
    spec = component.universal
    assert component.import_name == "calima_msa" and component.sentinels == ("morphology.db", "LICENSE")
    assert spec is not None and (spec.url, spec.kind, spec.member_prefix) == (ZIP_URL, "zip", "")
    assert spec.sha256 == "fe6531250c5529307627cc63ed56447cbb9968020d6ea3ab867e6ad9af94c738"
    assert spec.inner_sha256 == (("morphology.db", "195bc25a333237a2126470da888d7936b59ed3729f9210e0a4194ba43497dd70"),)


def test_the_profile_wires_the_arabic_policies():
    profile = get_profile("ar")
    assert (profile.display_name, profile.english_name) == ("\u0627\u0644\u0639\u0631\u0628\u064a\u0629", "Arabic")
    assert isinstance(profile.mined_form, ArabicMinedForm) and isinstance(profile.lookup, ArabicLookupStrategy)
    assert isinstance(profile.reading, ArabicReadingSupport) and isinstance(profile.script, ArabicScript)
    assert isinstance(profile.dict_keys, ArabicDictKeys) and profile.normalize is ar_normalize
    assert (
        profile.dedup_fold is not None
        and profile.dedup_fold("\u0643\u0650\u062a\u064e\u0627\u0628\u064c") == "\u0643\u062a\u0627\u0628"
    )  # kitaabun -> kitaab
    assert profile.import_encodings == ("utf-8-sig", "cp1256")
    assert {"ara", "ar", "arb", "arabic", "arz", "apc"} <= profile.audio_track_codes
    assert profile.asr_language == "ar" and profile.captions.orig_codes == ("ar-orig",)
    assert profile.capabilities == frozenset({"word_root", "arabic_grammar", "arabic_clitics", "lemmatised_frequency"})
    assert profile.extra_card_fields == AR_EXTRA_CARD_FIELDS
    assert [type(hook) for hook in profile.render_hooks] == [ArabicCardHook]
    assert profile.pos_defaults.allowed_pos == AR_ALLOWED_POS
    assert (
        profile.smoke_sentence
        == "\u0630\u0647\u0628 \u0627\u0644\u0637\u0627\u0644\u0628 \u0625\u0644\u0649 \u0627\u0644\u0645\u062f\u0631\u0633\u0629 \u0635\u0628\u0627\u062d\u0627\u064b."
    )
    assert profile.content_style.font_role == "ar" and profile.content_style.families[0] == "Noto Naskh Arabic"


def test_scoped_defaults_map_the_reading_and_turn_on_the_arabic_sdh_filter():
    config = switch_language(AnkiMinerConfig(), "ar")
    assert config.language == "ar" and config.downloader_subtitle_langs == "ar"
    assert config.allowed_pos == AR_ALLOWED_POS and config.excluded_subtypes == ()
    assert config.use_subtitle_regex_filter is True and config.subtitle_regex_filter == AR_SUBTITLE_REGEX
    assert config.anki_fields["expression_reading"] == "Reading"
    assert {config.anki_fields[key] for key in ("root", "expression_grammar", "clitic_segmentation")} == {""}
    assert config.anki_fields["expression_furigana"] == config.anki_fields["sentence_furigana"] == ""
    assert [entry.kind for entry in config.expression_audio_chain] == ["googletts"]


def test_audio_speaks_the_vocalised_lemma_through_google():
    audio = get_profile("ar").audio
    assert audio.resolved_gtts_lang(AnkiMinerConfig()) == "ar"
    assert (audio.cache_stem_prefix, audio.sentence_cache_stem_prefix) == ("googletts_ar", "sentencetts_ar")
    assert (
        audio.speakable is not None
        and audio.speakable("\u0643\u062a\u0627\u0628", "\u0643\u0650\u062a\u0627\u0628")
        == "\u0643\u0650\u062a\u0627\u0628"
    )


def test_a_missing_pack_refuses_the_switch_with_the_download_hint():
    reason = get_profile("ar").unavailable_reason
    assert reason is not None and "Arabic language pack" in (reason() or "")
