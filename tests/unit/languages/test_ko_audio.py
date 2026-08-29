"""Korean audio defaults."""

from types import SimpleNamespace

from anki_miner.languages.ko.audio import KO_AUDIO, ko_audio_candidates


def test_default_chain_is_googletts_only_and_enabled():
    assert [(e.kind, e.enabled) for e in KO_AUDIO.default_chain] == [("googletts", True)]


def test_no_jpod101_or_class101_entry_ships_by_default():
    assert all(e.kind not in {"jpod101", "custom", "custom_json"} for e in KO_AUDIO.default_chain)


def test_language_parameters():
    assert KO_AUDIO.gtts_lang == "ko"
    assert KO_AUDIO.papago_speaker == "kyuri"
    assert KO_AUDIO.cache_stem_prefix == "googletts_ko"
    assert KO_AUDIO.sentence_cache_stem_prefix == "sentencetts_ko"
    assert KO_AUDIO.custom_fetcher_language == "ko"
    assert KO_AUDIO.candidates is ko_audio_candidates


def test_candidate_ladder_speaks_the_mined_form_then_the_respelling():
    word = SimpleNamespace(mined_form="국물", expression_reading="궁물")
    assert ko_audio_candidates(word) == [("국물", "국물"), ("국물", "궁물")]
    assert ko_audio_candidates(SimpleNamespace(mined_form="학생", expression_reading="")) == [("학생", "학생")]
    assert ko_audio_candidates(SimpleNamespace(mined_form="", expression_reading="")) == []
