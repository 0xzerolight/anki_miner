"""A chain never includes a slot indexed for another mining language."""

from __future__ import annotations

import dataclasses

from anki_miner.config import (
    AnkiMinerConfig,
    AudioSourceEntry,
    ChainEntry,
    FreqEntry,
    PitchSourceEntry,
)
from anki_miner.gui.utils.service_factory import ServiceLoadResult
from anki_miner.services.audio_packs.registry import AudioPackMeta, AudioPackRegistry
from anki_miner.services.dictionary.registry import DictionaryRegistry, DictMeta
from anki_miner.services.frequency.registry import FreqSourceMeta, FrequencySourceRegistry
from anki_miner.services.pitch_accent.registry import PitchSourceMeta, PitchSourceRegistry
from tests.unit.languages.stub_registry import register_stub_profile


def _dict_meta(dict_id: str, language: str, tmp_path) -> DictMeta:
    return DictMeta(
        dict_id=dict_id,
        source_name=dict_id,
        format="yomitan",
        entry_count=5,
        schema_ok=True,
        db_path=tmp_path / dict_id / "index.sqlite",
        language=language,
    )


def _freq_meta(source_id: str, language: str, tmp_path) -> FreqSourceMeta:
    return FreqSourceMeta(
        source_id=source_id,
        source_name=source_id,
        format="csv",
        entry_count=3,
        schema_ok=True,
        version=1,
        db_path=tmp_path / source_id / "index.sqlite",
        language=language,
    )


def _pitch_meta(source_id: str, language: str, tmp_path) -> PitchSourceMeta:
    return PitchSourceMeta(
        source_id=source_id,
        source_name=source_id,
        format="csv",
        entry_count=3,
        schema_ok=True,
        version=1,
        db_path=tmp_path / source_id / "index.sqlite",
        language=language,
    )


def _pack_meta(pack_id: str, language: str, tmp_path) -> AudioPackMeta:
    pack_dir = tmp_path / pack_id
    pack_dir.mkdir(parents=True, exist_ok=True)
    return AudioPackMeta(
        pack_id=pack_id,
        source=pack_id,
        format="folder",
        entry_count=3,
        schema_ok=True,
        pack_dir=pack_dir,
        pack_dir_exists=True,
        db_path=pack_dir / "index.sqlite",
        language=language,
    )


# ---------------------------------------------------------------------------
# Dictionaries
# ---------------------------------------------------------------------------


def test_dict_chain_drops_other_language_slots(tmp_path):
    registry = DictionaryRegistry(tmp_path)
    registry._dicts = {
        "ja-dict": _dict_meta("ja-dict", "ja", tmp_path),
        "zh-dict": _dict_meta("zh-dict", "zh", tmp_path),
    }
    config = dataclasses.replace(
        AnkiMinerConfig(),
        language="zh",
        dictionary_chain=(
            ChainEntry(kind="indexed", dict_id="ja-dict", enabled=True),
            ChainEntry(kind="indexed", dict_id="zh-dict", enabled=True),
        ),
    )
    result = ServiceLoadResult()
    chain = registry.build_provider_chain(config, load_result=result)
    assert [p.dict_id for p in chain] == ["zh-dict"]
    assert any("ja-dict" in w for w in result.warnings)


def test_ja_config_keeps_a_legacy_unstamped_slot(tmp_path):
    registry = DictionaryRegistry(tmp_path)
    registry._dicts = {"legacy": _dict_meta("legacy", "ja", tmp_path)}
    config = dataclasses.replace(
        AnkiMinerConfig(),
        dictionary_chain=(ChainEntry(kind="indexed", dict_id="legacy", enabled=True),),
    )
    assert [p.dict_id for p in registry.build_provider_chain(config)] == ["legacy"]


def test_ja_slot_in_a_ja_session_never_warns(tmp_path):
    """The byte-stability guard: no warning is manufactured for a ja slot."""
    registry = DictionaryRegistry(tmp_path)
    registry._dicts = {"legacy": _dict_meta("legacy", "ja", tmp_path)}
    config = dataclasses.replace(
        AnkiMinerConfig(),
        dictionary_chain=(ChainEntry(kind="indexed", dict_id="legacy", enabled=True),),
    )
    result = ServiceLoadResult()
    registry.build_provider_chain(config, load_result=result)
    assert result.warnings == []


def test_dict_chain_skips_without_a_load_result(tmp_path, monkeypatch):
    """The skip is the registry's, not the sink's: no load_result, same chain."""
    register_stub_profile(monkeypatch, "ko")
    registry = DictionaryRegistry(tmp_path)
    registry._dicts = {"ja-dict": _dict_meta("ja-dict", "ja", tmp_path)}
    config = dataclasses.replace(
        AnkiMinerConfig(),
        language="ko",
        dictionary_chain=(ChainEntry(kind="indexed", dict_id="ja-dict", enabled=True),),
    )
    assert registry.build_provider_chain(config) == []


# ---------------------------------------------------------------------------
# Frequency
# ---------------------------------------------------------------------------


def test_frequency_chain_drops_other_language_slots(tmp_path, monkeypatch):
    register_stub_profile(monkeypatch, "ko")
    registry = FrequencySourceRegistry(tmp_path)
    registry._sources = {
        "ja-freq": FreqSourceMeta(
            source_id="ja-freq",
            source_name="ja",
            format="csv",
            entry_count=3,
            schema_ok=True,
            version=1,
            db_path=tmp_path / "ja-freq" / "index.sqlite",
            language="ja",
        )
    }
    config = dataclasses.replace(
        AnkiMinerConfig(),
        language="ko",
        frequency_chain=(FreqEntry(source_id="ja-freq", enabled=True),),
    )
    assert registry.build_sources(config) == []


def test_frequency_chain_warns_and_keeps_the_matching_slot(tmp_path):
    registry = FrequencySourceRegistry(tmp_path)
    registry._sources = {
        "ja-freq": _freq_meta("ja-freq", "ja", tmp_path),
        "zh-freq": _freq_meta("zh-freq", "zh", tmp_path),
    }
    config = dataclasses.replace(
        AnkiMinerConfig(),
        language="zh",
        frequency_chain=(
            FreqEntry(source_id="ja-freq", enabled=True),
            FreqEntry(source_id="zh-freq", enabled=True),
        ),
    )
    result = ServiceLoadResult()
    sources = registry.build_sources(config, load_result=result)
    assert [s.source_id for s in sources] == ["zh-freq"]
    assert any("ja-freq" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# Pitch
# ---------------------------------------------------------------------------


def test_pitch_chain_drops_other_language_slots(tmp_path, monkeypatch):
    register_stub_profile(monkeypatch, "ko")
    registry = PitchSourceRegistry(tmp_path)
    registry._sources = {
        "ja-pitch": _pitch_meta("ja-pitch", "ja", tmp_path),
        "ko-pitch": _pitch_meta("ko-pitch", "ko", tmp_path),
    }
    config = dataclasses.replace(
        AnkiMinerConfig(),
        language="ko",
        pitch_chain=(
            PitchSourceEntry(source_id="ja-pitch", enabled=True),
            PitchSourceEntry(source_id="ko-pitch", enabled=True),
        ),
    )
    result = ServiceLoadResult()
    sources = registry.build_sources(config, load_result=result)
    assert [s.source_id for s in sources] == ["ko-pitch"]
    assert any("ja-pitch" in w for w in result.warnings)


def test_pitch_chain_keeps_every_slot_for_ja(tmp_path):
    registry = PitchSourceRegistry(tmp_path)
    registry._sources = {"legacy-pitch": _pitch_meta("legacy-pitch", "ja", tmp_path)}
    config = dataclasses.replace(
        AnkiMinerConfig(),
        pitch_chain=(PitchSourceEntry(source_id="legacy-pitch", enabled=True),),
    )
    assert [s.source_id for s in registry.build_sources(config)] == ["legacy-pitch"]


# ---------------------------------------------------------------------------
# Audio packs
# ---------------------------------------------------------------------------


def test_audio_pack_chain_drops_other_language_slots(tmp_path):
    registry = AudioPackRegistry(tmp_path)
    registry._packs = {
        "ja-pack": _pack_meta("ja-pack", "ja", tmp_path),
        "zh-pack": _pack_meta("zh-pack", "zh", tmp_path),
    }
    config = dataclasses.replace(
        AnkiMinerConfig(),
        language="zh",
        expression_audio_chain=(
            AudioSourceEntry(kind="pack", pack_id="ja-pack", enabled=True),
            AudioSourceEntry(kind="pack", pack_id="zh-pack", enabled=True),
        ),
    )
    result = ServiceLoadResult()
    chain = registry.build_fetcher_chain(config, tmp_path / "cache", load_result=result)
    assert [f.pack_id for f in chain] == ["zh-pack"]
    assert any("ja-pack" in w for w in result.warnings)


def test_audio_pack_chain_keeps_every_pack_for_ja(tmp_path):
    registry = AudioPackRegistry(tmp_path)
    registry._packs = {"legacy-pack": _pack_meta("legacy-pack", "ja", tmp_path)}
    config = dataclasses.replace(
        AnkiMinerConfig(),
        expression_audio_chain=(AudioSourceEntry(kind="pack", pack_id="legacy-pack", enabled=True),),
    )
    chain = registry.build_fetcher_chain(config, tmp_path / "cache")
    assert [f.pack_id for f in chain] == ["legacy-pack"]


# ---------------------------------------------------------------------------
# The factory hands its sink down (omit-when-ja, like _lookup_kwarg)
# ---------------------------------------------------------------------------


class _RecordingDictRegistry:
    """Registry double recording the exact build_provider_chain call shape."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def build_provider_chain(self, config, **kwargs):
        self.calls.append(kwargs)
        return []


def test_factory_passes_the_sink_for_a_non_ja_config(test_config):
    from anki_miner.gui.utils.service_factory import build_definition_service

    registry = _RecordingDictRegistry()
    load_result = ServiceLoadResult()
    config = dataclasses.replace(test_config, language="zh", dictionary_chain=())
    service = build_definition_service(config, load_result, registry=registry)
    service.close()
    assert registry.calls == [{"load_result": load_result}]


def test_factory_keeps_the_ja_call_shape(test_config):
    """A ja run calls build_provider_chain exactly as it did pre-transition."""
    from anki_miner.gui.utils.service_factory import build_definition_service

    registry = _RecordingDictRegistry()
    config = dataclasses.replace(test_config, dictionary_chain=())
    service = build_definition_service(config, ServiceLoadResult(), registry=registry)
    service.close()
    assert registry.calls == [{}]
