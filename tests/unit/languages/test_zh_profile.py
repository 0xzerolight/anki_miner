"""zh profile assembly: every contract field, every scoped default."""

from __future__ import annotations

import dataclasses

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.profile import AudioDefaults, ContentTextStyle, LanguageProfile
from anki_miner.languages.registry import available_languages, get_profile
from anki_miner.languages.switching import LANGUAGE_SCOPED_FIELDS, switch_language
from anki_miner.languages.zh.style import ZH_CONTENT_STYLE, zh_cjk_wrap


def test_zh_is_registered():
    assert "zh" in available_languages()
    assert isinstance(get_profile("zh"), LanguageProfile)
    assert get_profile("zh") is get_profile("zh")


def test_zh_profile_identity_and_capabilities():
    profile = get_profile("zh")
    assert (profile.code, profile.display_name) == ("zh", "中文")
    assert profile.capabilities == frozenset({"pinyin", "tone_color", "script_variants", "measure_word"})
    assert profile.sentence_annotator is None
    assert profile.asr_language == "zh"
    assert profile.audio_track_codes == frozenset({"chi", "zho", "zh", "chinese", "cmn"})
    assert profile.import_encodings == ("utf-8-sig", "gb18030", "big5")
    assert profile.captions.primary == "zh-Hans"
    assert profile.captions.orig_codes == ("zh-Hans-orig", "zh-Hant-orig")
    assert profile.captions.audio_pattern == "^zh(-|$)"
    assert profile.captions.bare_fallback is True
    assert profile.sentence_rules.space_aware is False
    assert "。" in profile.sentence_rules.terminators


def test_zh_audio_defaults_are_real_audio_source_entries():
    audio = get_profile("zh").audio
    assert isinstance(audio, AudioDefaults)
    assert audio.gtts_lang == "zh-CN"
    assert audio.cache_stem_prefix == "googletts_zh"
    assert audio.sentence_cache_stem_prefix == "sentencetts_zh"
    assert audio.custom_fetcher_language == "zh"
    assert audio.papago_speaker is None
    assert [entry.kind for entry in audio.default_chain] == ["googletts"]
    assert all(hasattr(entry, "pack_id") for entry in audio.default_chain)
    assert callable(audio.candidates)


def test_scoped_defaults_cover_every_scoped_field():
    scoped = get_profile("zh").scoped_defaults
    assert set(scoped) == set(LANGUAGE_SCOPED_FIELDS)
    assert scoped["script_variant"] == "simplified"
    assert scoped["reading_tone_color"] is True
    assert scoped["downloader_subtitle_langs"] == "zh-Hans"
    assert scoped["excluded_wordsets"] == ()
    assert scoped["dictionary_chain"] == ()
    assert scoped["anki_fields"]["measure_word"] == ""


def test_switch_language_to_zh_applies_every_scoped_field():
    config = switch_language(AnkiMinerConfig(), "zh")
    assert config.language == "zh"
    for name in LANGUAGE_SCOPED_FIELDS:
        assert getattr(config, name) == get_profile("zh").scoped_defaults[name], name
    assert dataclasses.replace(config).script_variant == "simplified"


def test_zh_targets_neither_the_ja_note_type_nor_a_nameless_deck():
    """The two overrides the derive loop cannot supply (contract item R11).

    Blanking by type would leave ``anki_deck_name`` "", which AnkiConnect
    rejects, and inheriting ja's default would file Chinese cards into the
    Japanese deck. ``anki_note_type`` is the opposite case: "Lapis" is a JP
    Mining Note type whose fields a zh run cannot fill, so the empty string is
    the honest value and the user picks one.
    """
    scoped = get_profile("zh").scoped_defaults
    assert scoped["anki_deck_name"] == "Anki Miner"
    assert scoped["anki_note_type"] == ""
    assert switch_language(AnkiMinerConfig(), "zh").anki_note_type == ""


def test_content_style_comes_from_the_zh_style_module():
    style = get_profile("zh").content_style
    assert isinstance(style, ContentTextStyle)
    assert style is ZH_CONTENT_STYLE
    assert style.font_role == "zh"
    assert style.families and all(isinstance(f, str) for f in style.families)
    assert style.wrap is zh_cjk_wrap


def test_build_profile_never_re_enters_the_registry(monkeypatch):
    """``get_profile`` holds a NON-reentrant lock across the builder call, so a
    builder that reaches back into the registry self-deadlocks with no
    traceback and no test failure — only a hung process. Pinned here rather
    than discovered there."""
    from anki_miner.languages import registry
    from anki_miner.languages.zh import build_profile

    reentries: list[str] = []
    monkeypatch.setattr(registry, "get_profile", lambda code: reentries.append(code))
    monkeypatch.setattr(registry, "_CACHE", {})

    profile = build_profile()

    assert isinstance(profile, LanguageProfile)
    assert reentries == []
    assert registry._CACHE == {}
    assert not registry._LOCK.locked()


def test_the_papago_speaker_is_the_profile_s_and_zh_has_none():
    from anki_miner.services.sentence_tts_fetcher import PAPAGO_SPEAKER_JA

    assert get_profile("ja").audio.papago_speaker == PAPAGO_SPEAKER_JA
    assert get_profile("zh").audio.papago_speaker is None


def test_papago_joins_the_ja_sentence_chain_and_stays_out_of_the_zh_one():
    """Papago speaks Japanese and Korean, not Chinese. The chain used to coerce
    a missing speaker to the JA voice, which would read a Chinese sentence in
    Japanese; membership now follows the profile's own speaker."""
    from anki_miner.gui.utils.service_factory import _build_sentence_audio_fetcher
    from anki_miner.services.sentence_tts_fetcher import PAPAGO_SPEAKER_JA, PapagoSentenceTtsFetcher

    base = dataclasses.replace(AnkiMinerConfig(), reading_tts_enabled=True)

    ja_chain = _build_sentence_audio_fetcher(base)
    try:
        papago = [f for f in ja_chain._fetchers if isinstance(f, PapagoSentenceTtsFetcher)]
        assert len(papago) == 1
        assert papago[0]._speaker == PAPAGO_SPEAKER_JA
    finally:
        ja_chain.close()

    zh_chain = _build_sentence_audio_fetcher(switch_language(base, "zh"))
    try:
        assert not any(isinstance(f, PapagoSentenceTtsFetcher) for f in zh_chain._fetchers)
        assert zh_chain._fetchers, "the Google leg still serves zh"
    finally:
        zh_chain.close()
