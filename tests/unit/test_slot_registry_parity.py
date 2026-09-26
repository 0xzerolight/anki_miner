"""Chain-assembly parity for the four indexed-resource registries.

Pins, per family, exactly what ``build_*`` hands back (every provider
constructor call, in chain order), the user-facing ``load_result`` warnings,
the registry-module log records (level and text, in order), and the
``unlisted`` / ``stale_enabled`` / ``usable_enabled`` answers — from one fixed
set of slots that walks every gate: disabled, missing on disk, null or empty
slot id, schema-stale, source-missing (audio), other-language, zero entries,
and the non-slot kinds (jisho, jpod101, googletts) interleaved in chain order.

The slots enter through each module's ``scan_index_root`` seam and the
provider classes are swapped for call recorders, so the test reads only public
methods and survives the registries sharing a base.
"""

from __future__ import annotations

import dataclasses
import logging
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

import anki_miner.services.audio_packs.registry as audio_reg
import anki_miner.services.dictionary.registry as dict_reg
import anki_miner.services.frequency.registry as freq_reg
import anki_miner.services.pitch_accent.registry as pitch_reg
from anki_miner.config import (
    AnkiMinerConfig,
    AudioSourceEntry,
    ChainEntry,
    FreqEntry,
    PitchSourceEntry,
)
from anki_miner.gui.utils.service_factory import ServiceLoadResult
from anki_miner.languages.registry import get_profile, language_display_name

_ZH = language_display_name("zh")


def _record_ctor(monkeypatch: pytest.MonkeyPatch, module: ModuleType, name: str) -> None:
    """Swap *module*'s provider class for a recorder returning its call."""

    def ctor(*args: Any, **kwargs: Any) -> tuple[str, tuple[Any, ...], dict[str, Any]]:
        return (name, args, kwargs)

    monkeypatch.setattr(module, name, ctor)


def _loaded(monkeypatch, module: ModuleType, registry_cls: type, root: Path, slots: dict[str, Any]):
    monkeypatch.setattr(module, "scan_index_root", lambda *args, **kwargs: dict(slots))
    registry = registry_cls(root)
    registry.load()
    return registry


def _records(caplog: pytest.LogCaptureFixture, module: ModuleType) -> list[tuple[str, str]]:
    records = [r for r in caplog.records if r.name == module.__name__]
    # The log format is %(name)s:%(lineno)d, so every line must point into the
    # registry's own module, not into shared code it calls.
    assert {r.pathname for r in records} <= {module.__file__}
    return [(r.levelname, r.getMessage()) for r in records]


# ---------------------------------------------------------------------------
# Frequency and pitch: same shape, different noun, meta and provider
# ---------------------------------------------------------------------------


def _freq_meta(root: Path, source_id: str, **overrides: Any) -> freq_reg.FreqSourceMeta:
    fields: dict[str, Any] = {
        "source_id": source_id,
        "source_name": source_id.upper(),
        "format": "csv",
        "entry_count": 3,
        "schema_ok": True,
        "version": 3,
        "db_path": root / source_id / "index.sqlite",
    }
    fields.update(overrides)
    return freq_reg.FreqSourceMeta(**fields)


def _pitch_meta(root: Path, source_id: str, **overrides: Any) -> pitch_reg.PitchSourceMeta:
    fields: dict[str, Any] = {
        "source_id": source_id,
        "source_name": source_id.upper(),
        "format": "csv",
        "entry_count": 3,
        "schema_ok": True,
        "version": 1,
        "db_path": root / source_id / "index.sqlite",
    }
    fields.update(overrides)
    return pitch_reg.PitchSourceMeta(**fields)


def _source_slots(make, root: Path) -> dict[str, Any]:
    stale = {"schema_ok": False, "version": 1}
    return {
        "ok": make(root, "ok"),
        "stale": make(root, "stale", **stale),
        "stale-off": make(root, "stale-off", **stale),
        "zh": make(root, "zh", language="zh"),
        # Fails two gates: the schema gate runs first, so no user warning.
        "stale-zh": make(root, "stale-zh", language="zh", **stale),
        "empty": make(root, "empty", entry_count=0),
        "off": make(root, "off"),
        "late": make(root, "late"),
        "spare-b": make(root, "spare-b"),
        "spare-a": make(root, "spare-a"),
        "spare-stale": make(root, "spare-stale", **stale),
    }


_SOURCE_CHAIN = (
    ("ok", True),
    ("gone", True),
    ("stale", True),
    ("off", False),
    ("stale-off", False),
    ("zh", True),
    ("stale-zh", True),
    ("empty", True),
    ("late", True),
    ("", True),
)


def test_frequency_registry_parity(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG, logger=freq_reg.__name__)
    slots = _source_slots(_freq_meta, tmp_path)
    slots["late"] = _freq_meta(tmp_path, "late", is_categorical=True)
    registry = _loaded(monkeypatch, freq_reg, freq_reg.FrequencySourceRegistry, tmp_path, slots)
    _record_ctor(monkeypatch, freq_reg, "IndexedFreqProvider")
    config = dataclasses.replace(
        AnkiMinerConfig(),
        frequency_chain=tuple(FreqEntry(source_id=sid, enabled=on) for sid, on in _SOURCE_CHAIN),
    )
    keys = get_profile("ja").dict_keys
    caplog.clear()

    result = ServiceLoadResult()
    built = registry.build_sources(config, load_result=result)

    def provider(sid: str, name: str, *, categorical: bool = False):
        return (
            "IndexedFreqProvider",
            (),
            {
                "source_id": sid,
                "db_path": tmp_path / sid / "index.sqlite",
                "display_name": name,
                "is_categorical": categorical,
                "keys": keys,
            },
        )

    expected = [provider("ok", "OK"), provider("empty", "EMPTY"), provider("late", "LATE", categorical=True)]
    assert built == expected
    assert result.warnings == [f"Frequency source 'ZH' is indexed for {_ZH} and was skipped."]
    assert _records(caplog, freq_reg) == [
        ("WARNING", f"Frequency source 'gone' referenced in config but not found in {tmp_path}"),
        ("WARNING", "Frequency source 'stale' has unsupported schema_version 1; needs reimport"),
        ("WARNING", "Frequency source 'zh' is indexed for 'zh'; skipped for 'ja'"),
        ("WARNING", "Frequency source 'stale-zh' has unsupported schema_version 1; needs reimport"),
        ("WARNING", f"Frequency source '' referenced in config but not found in {tmp_path}"),
    ]
    assert registry.build_sources(config) == expected
    assert [m.source_id for m in registry.unlisted(config)] == ["spare-a", "spare-b"]
    assert [m.source_id for m in registry.stale_enabled(config)] == ["stale", "stale-zh"]
    assert [m.source_id for m in registry.usable_enabled(config)] == ["late", "ok"]


def test_pitch_registry_parity(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG, logger=pitch_reg.__name__)
    registry = _loaded(
        monkeypatch, pitch_reg, pitch_reg.PitchSourceRegistry, tmp_path, _source_slots(_pitch_meta, tmp_path)
    )
    _record_ctor(monkeypatch, pitch_reg, "IndexedPitchProvider")
    config = dataclasses.replace(
        AnkiMinerConfig(),
        pitch_chain=tuple(PitchSourceEntry(source_id=sid, enabled=on) for sid, on in _SOURCE_CHAIN),
    )
    caplog.clear()

    result = ServiceLoadResult()
    built = registry.build_sources(config, load_result=result)

    def provider(sid: str, name: str):
        return (
            "IndexedPitchProvider",
            (),
            {"source_id": sid, "db_path": tmp_path / sid / "index.sqlite", "display_name": name},
        )

    expected = [provider("ok", "OK"), provider("empty", "EMPTY"), provider("late", "LATE")]
    assert built == expected
    assert result.warnings == [f"Pitch source 'ZH' is indexed for {_ZH} and was skipped."]
    assert _records(caplog, pitch_reg) == [
        ("WARNING", f"Pitch source 'gone' referenced in config but not found in {tmp_path}"),
        ("WARNING", "Pitch source 'stale' has unsupported schema_version 1; needs reimport"),
        ("WARNING", "Pitch source 'zh' is indexed for 'zh'; skipped for 'ja'"),
        ("WARNING", "Pitch source 'stale-zh' has unsupported schema_version 1; needs reimport"),
        ("WARNING", f"Pitch source '' referenced in config but not found in {tmp_path}"),
    ]
    assert registry.build_sources(config) == expected
    assert [m.source_id for m in registry.unlisted(config)] == ["spare-a", "spare-b"]
    assert [m.source_id for m in registry.stale_enabled(config)] == ["stale", "stale-zh"]
    assert [m.source_id for m in registry.usable_enabled(config)] == ["late", "ok"]


# ---------------------------------------------------------------------------
# Dictionary: DEBUG for a missing slot, jisho interleaved, null dict_id
# ---------------------------------------------------------------------------


def _dict_meta(root: Path, dict_id: str, **overrides: Any) -> dict_reg.DictMeta:
    fields: dict[str, Any] = {
        "dict_id": dict_id,
        "source_name": dict_id.upper(),
        "format": "yomitan",
        "entry_count": 3,
        "schema_ok": True,
        "db_path": root / dict_id / "index.sqlite",
    }
    fields.update(overrides)
    return dict_reg.DictMeta(**fields)


def test_dictionary_registry_parity(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG, logger=dict_reg.__name__)
    slots = {
        "ok": _dict_meta(tmp_path, "ok"),
        "stale": _dict_meta(tmp_path, "stale", schema_ok=False),
        "stale-off": _dict_meta(tmp_path, "stale-off", schema_ok=False),
        "zh": _dict_meta(tmp_path, "zh", language="zh"),
        "stale-zh": _dict_meta(tmp_path, "stale-zh", schema_ok=False, language="zh"),
        "empty": _dict_meta(tmp_path, "empty", entry_count=0),
        "off": _dict_meta(tmp_path, "off"),
        "spare-b": _dict_meta(tmp_path, "spare-b"),
        "spare-a": _dict_meta(tmp_path, "spare-a"),
        "spare-stale": _dict_meta(tmp_path, "spare-stale", schema_ok=False),
    }
    registry = _loaded(monkeypatch, dict_reg, dict_reg.DictionaryRegistry, tmp_path, slots)
    _record_ctor(monkeypatch, dict_reg, "IndexedDictProvider")
    _record_ctor(monkeypatch, dict_reg, "JishoProvider")
    config = dataclasses.replace(
        AnkiMinerConfig(),
        dictionary_chain=(
            ChainEntry(kind="indexed", dict_id="ok"),
            ChainEntry(kind="indexed", dict_id="gone"),
            ChainEntry(kind="jisho"),
            ChainEntry(kind="indexed", dict_id=None),
            ChainEntry(kind="indexed", dict_id="stale"),
            ChainEntry(kind="indexed", dict_id="off", enabled=False),
            ChainEntry(kind="jisho", enabled=False),
            ChainEntry(kind="indexed", dict_id="stale-off", enabled=False),
            ChainEntry(kind="indexed", dict_id="zh"),
            ChainEntry(kind="indexed", dict_id="stale-zh"),
            ChainEntry(kind="indexed", dict_id="empty"),
            ChainEntry(kind="indexed", dict_id=""),
        ),
    )
    keys = get_profile("ja").dict_keys
    caplog.clear()

    result = ServiceLoadResult()
    built = registry.build_provider_chain(config, load_result=result)

    def provider(dict_id: str, name: str):
        return (
            "IndexedDictProvider",
            (),
            {"dict_id": dict_id, "db_path": tmp_path / dict_id / "index.sqlite", "display_name": name, "keys": keys},
        )

    expected = [
        provider("ok", "OK"),
        ("JishoProvider", (config.jisho_api_url, config.jisho_delay), {}),
        provider("empty", "EMPTY"),
    ]
    assert built == expected
    assert result.warnings == [f"Dictionary 'ZH' is indexed for {_ZH} and was skipped."]
    assert _records(caplog, dict_reg) == [
        ("DEBUG", f"Dictionary 'gone' referenced in config but not found in {tmp_path}"),
        ("WARNING", "Skipping indexed ChainEntry with null dict_id"),
        ("WARNING", "Dictionary 'stale' has wrong schema_version; needs reimport"),
        ("WARNING", "Dictionary 'zh' is indexed for 'zh'; skipped for 'ja'"),
        ("WARNING", "Dictionary 'stale-zh' has wrong schema_version; needs reimport"),
        ("DEBUG", f"Dictionary '' referenced in config but not found in {tmp_path}"),
    ]
    assert registry.build_provider_chain(config) == expected
    assert [m.dict_id for m in registry.unlisted(config)] == ["spare-a", "spare-b"]
    assert [m.dict_id for m in registry.stale_enabled(config)] == ["stale", "stale-zh"]
    assert [m.dict_id for m in registry.usable_enabled(config)] == ["ok"]


# ---------------------------------------------------------------------------
# Audio packs: the source_available gate, network kinds, meta.pack_id != folder
# ---------------------------------------------------------------------------


def _pack_meta(root: Path, pack_id: str, **overrides: Any) -> audio_reg.AudioPackMeta:
    fields: dict[str, Any] = {
        "pack_id": pack_id,
        "source": pack_id.upper(),
        "format": "folder",
        "entry_count": 3,
        "schema_ok": True,
        "pack_dir": root / "media" / pack_id,
        "pack_dir_exists": True,
        "db_path": root / pack_id / "index.sqlite",
    }
    fields.update(overrides)
    return audio_reg.AudioPackMeta(**fields)


def test_audio_pack_registry_parity(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG, logger=audio_reg.__name__)
    blob_db = tmp_path / "droid.db"
    blob_db.write_bytes(b"")
    lost_db = tmp_path / "lost.db"
    slots = {
        "ok": _pack_meta(tmp_path, "ok"),
        "droid": _pack_meta(tmp_path, "droid", format="android_db", source_db=blob_db),
        "droid-gone": _pack_meta(tmp_path, "droid-gone", format="android_db", source_db=lost_db),
        "moved": _pack_meta(tmp_path, "moved", pack_dir_exists=False),
        "stale": _pack_meta(tmp_path, "stale", schema_ok=False),
        "stale-off": _pack_meta(tmp_path, "stale-off", schema_ok=False),
        # An empty ``source`` falls back to the pack id in the user warning.
        "zh": _pack_meta(tmp_path, "zh", source="", language="zh"),
        # Each fails two gates; the earlier gate wins, so no user warning.
        "stale-zh": _pack_meta(tmp_path, "stale-zh", schema_ok=False, language="zh"),
        "moved-zh": _pack_meta(tmp_path, "moved-zh", pack_dir_exists=False, language="zh"),
        "empty": _pack_meta(tmp_path, "empty", entry_count=0),
        "off": _pack_meta(tmp_path, "off"),
        # Folder renamed after import: keyed by folder, identified by meta.
        "renamed-dir": _pack_meta(tmp_path, "renamed"),
        "spare-b": _pack_meta(tmp_path, "spare-b"),
        "spare-a": _pack_meta(tmp_path, "spare-a"),
        "spare-stale": _pack_meta(tmp_path, "spare-stale", schema_ok=False),
    }
    registry = _loaded(monkeypatch, audio_reg, audio_reg.AudioPackRegistry, tmp_path, slots)
    _record_ctor(monkeypatch, audio_reg, "LocalAudioPackFetcher")
    config = dataclasses.replace(
        AnkiMinerConfig(),
        expression_audio_chain=(
            AudioSourceEntry(kind="pack", pack_id="ok"),
            AudioSourceEntry(kind="jpod101"),
            AudioSourceEntry(kind="pack", pack_id="gone"),
            AudioSourceEntry(kind="pack", pack_id=None),
            AudioSourceEntry(kind="pack", pack_id="stale"),
            AudioSourceEntry(kind="pack", pack_id="moved"),
            AudioSourceEntry(kind="pack", pack_id="droid-gone"),
            AudioSourceEntry(kind="pack", pack_id="zh"),
            AudioSourceEntry(kind="pack", pack_id="stale-zh"),
            AudioSourceEntry(kind="pack", pack_id="moved-zh"),
            AudioSourceEntry(kind="pack", pack_id="off", enabled=False),
            AudioSourceEntry(kind="pack", pack_id="stale-off", enabled=False),
            AudioSourceEntry(kind="pack", pack_id="droid"),
            AudioSourceEntry(kind="pack", pack_id="empty"),
            AudioSourceEntry(kind="googletts"),
            AudioSourceEntry(kind="pack", pack_id="renamed"),
            AudioSourceEntry(kind="pack", pack_id=""),
        ),
    )
    cache_dir = tmp_path / "cache"
    caplog.clear()

    result = ServiceLoadResult()
    built = registry.build_fetcher_chain(config, cache_dir, load_result=result)

    def fetcher(pack_id: str, blob: Path | None = None):
        return (
            "LocalAudioPackFetcher",
            (),
            {
                "db_path": tmp_path / pack_id / "index.sqlite",
                "pack_dir": tmp_path / "media" / pack_id,
                "pack_id": pack_id,
                "cache_dir": cache_dir,
                "blob_db_path": blob,
            },
        )

    expected = [fetcher("ok"), fetcher("droid", blob_db), fetcher("empty")]
    assert built == expected
    assert result.warnings == [f"Audio pack 'zh' is indexed for {_ZH} and was skipped."]
    moved_dir = tmp_path / "media" / "moved"
    assert _records(caplog, audio_reg) == [
        ("WARNING", f"Audio pack 'gone' referenced in config but not found in {tmp_path}"),
        ("WARNING", "Skipping audio pack ChainEntry with null pack_id"),
        ("WARNING", "Audio pack 'stale' has wrong schema_version; needs reimport"),
        ("WARNING", f"Audio pack 'moved' source missing ({moved_dir}); skipping — moved or deleted?"),
        ("WARNING", f"Audio pack 'droid-gone' source missing ({lost_db}); skipping — moved or deleted?"),
        ("WARNING", "Audio pack 'zh' is indexed for 'zh'; skipped for 'ja'"),
        ("WARNING", "Audio pack 'stale-zh' has wrong schema_version; needs reimport"),
        (
            "WARNING",
            f"Audio pack 'moved-zh' source missing ({tmp_path / 'media' / 'moved-zh'}); skipping — moved or deleted?",
        ),
        ("WARNING", f"Audio pack 'renamed' referenced in config but not found in {tmp_path}"),
        ("WARNING", f"Audio pack '' referenced in config but not found in {tmp_path}"),
    ]
    assert registry.build_fetcher_chain(config, cache_dir) == expected
    assert [m.pack_id for m in registry.unlisted(config)] == ["spare-a", "spare-b"]
    assert [m.pack_id for m in registry.stale_enabled(config)] == ["stale", "stale-zh"]
