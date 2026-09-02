"""zh parser factory, audio defaults and downloadable-resource catalog."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from anki_miner.config import AnkiMinerConfig, AudioSourceEntry
from anki_miner.languages.profile import AudioDefaults, LanguageProfile
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.languages.zh import variants
from anki_miner.languages.zh.audio import ZH_AUDIO, zh_audio_candidates
from anki_miner.languages.zh.catalog import ZH_CATALOG
from anki_miner.languages.zh.parser import create_parser
from anki_miner.languages.zh.reading import ZhReadingSupport
from anki_miner.services.resource_catalog import RESOURCE_KINDS
from anki_miner.services.subtitle_parser import SubtitleParserService


def _zh_config() -> AnkiMinerConfig:
    """Built through ``switch_language``, the way the app builds one.

    ``_zh_config()`` keeps every JA-shaped scoped default,
    the unidic POS names included: a parser built on it tokenizes with jieba
    and mines nothing. Construction-only tests never noticed, so the fixture
    must not be the very mismatch a parse test exists to catch.
    """
    return switch_language(AnkiMinerConfig(), "zh")


@pytest.fixture
def zh_profile() -> LanguageProfile:
    """The registered zh profile ``create_parser`` resolves (task 2A.12)."""
    return get_profile("zh")


def _word(mined_form: str, reading: str) -> SimpleNamespace:
    # Phase 5 hands render/audio helpers a TokenizedWord; only these two
    # attributes are read, so a namespace is the honest stand-in here.
    return SimpleNamespace(mined_form=mined_form, expression_reading=reading)


class TestCreateParser:
    def test_returns_the_shared_subtitle_parser_service(self, zh_profile: LanguageProfile) -> None:
        parser = create_parser(_zh_config())
        assert isinstance(parser, SubtitleParserService)

    def test_satisfies_the_subtitle_parser_protocol_surface(self, zh_profile: LanguageProfile) -> None:
        parser = create_parser(_zh_config())
        for name in ("parse_subtitle_file", "parse_subtitle_file_with_index", "parse_text_units", "count_lemmas"):
            assert callable(getattr(parser, name))
        assert parser.tagger is not None

    def test_the_profile_policy_and_reading_are_injected(self, zh_profile: LanguageProfile) -> None:
        # Without the injection TokenizedWord.mined_form falls back to the JA
        # select_mined_form and the reading field stays empty, silently.
        parser = create_parser(_zh_config())
        assert parser._mined_form_policy is zh_profile.mined_form
        assert parser._reading_support is zh_profile.reading

    def test_an_explicit_argument_still_wins(self, zh_profile: LanguageProfile) -> None:
        other = ZhReadingSupport()
        parser = create_parser(_zh_config(), reading_support=other)
        assert parser._reading_support is other


class TestZhAudio:
    def test_is_a_real_audio_defaults_with_the_zh_values(self) -> None:
        assert isinstance(ZH_AUDIO, AudioDefaults)
        assert ZH_AUDIO.gtts_lang == "zh-CN"
        assert ZH_AUDIO.custom_fetcher_language == "zh"
        assert ZH_AUDIO.papago_speaker is None

    def test_cache_stems_are_namespaced_away_from_ja(self) -> None:
        assert ZH_AUDIO.cache_stem_prefix == "googletts_zh"
        assert ZH_AUDIO.sentence_cache_stem_prefix == "sentencetts_zh"

    def test_default_chain_is_one_real_googletts_entry(self) -> None:
        assert ZH_AUDIO.default_chain == (AudioSourceEntry(kind="googletts"),)
        assert all(isinstance(entry, AudioSourceEntry) for entry in ZH_AUDIO.default_chain)

    def test_candidates_is_the_zh_ladder_builder(self) -> None:
        assert ZH_AUDIO.candidates is zh_audio_candidates


class TestZhAudioCandidates:
    def test_the_mined_form_pair_comes_first(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(variants, "variant_candidates", lambda word: [word, "銀行"])
        assert zh_audio_candidates(_word("银行", "yín háng"))[0] == ("银行", "yín háng")

    def test_script_variants_reuse_the_same_reading(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(variants, "variant_candidates", lambda word: [word, "銀行"])
        assert zh_audio_candidates(_word("银行", "yín háng")) == [
            ("银行", "yín háng"),
            ("銀行", "yín háng"),
        ]

    def test_a_single_script_word_yields_one_pair(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(variants, "variant_candidates", lambda word: [word])
        assert zh_audio_candidates(_word("中文", "zhōng wén")) == [("中文", "zhōng wén")]

    def test_an_empty_term_yields_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(variants, "variant_candidates", lambda word: [word])
        assert zh_audio_candidates(_word("", "")) == []


class TestZhCatalog:
    def test_kinds_are_all_dispatchable(self) -> None:
        assert ZH_CATALOG
        assert {spec.kind for spec in ZH_CATALOG} <= RESOURCE_KINDS

    def test_ids_are_unique_and_urls_are_direct_downloads(self) -> None:
        assert len({spec.id for spec in ZH_CATALOG}) == len(ZH_CATALOG)
        for spec in ZH_CATALOG:
            assert spec.url.startswith("https://"), spec.id
            assert spec.license_note, spec.id

    def test_the_dictionary_slot_is_pinned(self) -> None:
        assert [spec.id for spec in ZH_CATALOG if spec.kind == "dict"] == ["cc-cedict"]
