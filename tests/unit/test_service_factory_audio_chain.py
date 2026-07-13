"""Tests for expression audio chain composition in service_factory."""

from __future__ import annotations

import dataclasses
import json
import logging
from pathlib import Path

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.config.config import AudioSourceEntry
from anki_miner.gui.utils import service_factory
from anki_miner.services.audio_packs.importer import import_audio_pack
from anki_miner.services.custom_audio_fetcher import CustomAudioFetcher
from anki_miner.services.expression_audio_fetcher import (
    ChainedExpressionAudioFetcher,
    JPod101AudioFetcher,
)
from anki_miner.services.google_translate_audio_fetcher import (
    GoogleTranslateAudioFetcher,
)
from anki_miner.services.sentence_tts_fetcher import (
    ChainedSentenceAudioFetcher,
    GoogleSentenceTtsFetcher,
    PapagoSentenceTtsFetcher,
)

# ---------------------------------------------------------------------------
# Helpers (mirror test_audio_pack_registry.py style)
# ---------------------------------------------------------------------------


def _make_ajt_pack(directory: Path, n_entries: int = 2) -> Path:
    media_dir = directory / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    headwords: dict = {}
    files_meta: dict = {}
    words = ["食べる", "飲む", "走る"]
    for i in range(n_entries):
        word = words[i % len(words)]
        fname = f"word_{i}.mp3"
        (media_dir / fname).write_bytes(b"AUDIO:" + fname.encode())
        headwords.setdefault(word, []).append(fname)
        files_meta[fname] = {"kana_reading": f"reading_{i}", "pitch_number": str(i)}
    (directory / "index.json").write_text(
        json.dumps({"headwords": headwords, "files": files_meta}),
        encoding="utf-8",
    )
    return directory


def _import_pack(tmp_path: Path, pack_dir_name: str = "test_pack") -> tuple[Path, str]:
    """Return (packs_root, pack_id) for a freshly imported AJT pack."""
    pack_src = _make_ajt_pack(tmp_path / pack_dir_name)
    packs_root = tmp_path / "audio_packs"
    result = import_audio_pack(pack_src, packs_root)
    return packs_root, result.pack_id


def _map_audio_field(cfg: AnkiMinerConfig) -> dict[str, str]:
    """anki_fields with expression_audio mapped — the on/off switch that gates
    pack-registry construction now that the enable flag is gone."""
    return {**cfg.anki_fields, "expression_audio": "ExpressionAudio"}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def base_config(tmp_path: Path) -> AnkiMinerConfig:
    """Config whose on-disk paths live under tmp_path, not ~/.anki_miner."""
    return dataclasses.replace(
        AnkiMinerConfig(),
        dicts_root=tmp_path / "dicts",
        known_words_db_path=tmp_path / "known_words.db",
        stats_db_path=tmp_path / "stats.db",
        audio_packs_root=tmp_path / "audio_packs",
    )


# ---------------------------------------------------------------------------
# _build_expression_audio_fetcher tests
# ---------------------------------------------------------------------------


class TestBuildExpressionAudioFetcher:
    """Unit tests for the _build_expression_audio_fetcher helper."""

    def test_default_config_returns_chained_fetcher(self, base_config):
        """Default config (jpod101-only) produces a ChainedExpressionAudioFetcher."""
        fetcher = service_factory._build_expression_audio_fetcher(base_config)
        assert isinstance(fetcher, ChainedExpressionAudioFetcher)

    def test_default_config_chain_contains_jpod101(self, base_config):
        """Default config yields exactly one JPod101AudioFetcher in the chain."""
        fetcher = service_factory._build_expression_audio_fetcher(base_config)
        assert isinstance(fetcher, ChainedExpressionAudioFetcher)
        assert len(fetcher._fetchers) == 1
        assert isinstance(fetcher._fetchers[0], JPod101AudioFetcher)

    def test_default_config_no_registry_constructed(self, monkeypatch, base_config):
        """AudioPackRegistry must NOT be constructed when no pack entries exist."""
        constructed = []

        class _TrackingRegistry:
            def __init__(self, *args, **kwargs):
                constructed.append(True)

            def load(self):
                pass

            def build_fetcher_chain(self, *a, **kw):
                return []

        monkeypatch.setattr(service_factory, "AudioPackRegistry", _TrackingRegistry, raising=True)
        service_factory._build_expression_audio_fetcher(base_config)
        assert constructed == [], "AudioPackRegistry must not be constructed for jpod101-only config"

    def test_jpod101_disabled_only_pack_enabled(self, tmp_path, base_config):
        """When jpod101 is disabled and one pack is enabled, chain holds only the pack fetcher."""
        packs_root, pack_id = _import_pack(tmp_path)
        cfg = dataclasses.replace(
            base_config,
            audio_packs_root=packs_root,
            anki_fields=_map_audio_field(base_config),
            expression_audio_chain=(
                AudioSourceEntry(kind="jpod101", enabled=False),
                AudioSourceEntry(kind="pack", pack_id=pack_id, enabled=True),
            ),
        )
        fetcher = service_factory._build_expression_audio_fetcher(cfg)
        assert isinstance(fetcher, ChainedExpressionAudioFetcher)
        assert len(fetcher._fetchers) == 1
        from anki_miner.services.audio_packs.fetcher import LocalAudioPackFetcher

        assert isinstance(fetcher._fetchers[0], LocalAudioPackFetcher)

    def test_pack_before_jpod_chain_order(self, tmp_path, base_config):
        """Config ordering [pack, jpod101] → chain is [LocalAudioPackFetcher, JPod101AudioFetcher]."""
        from anki_miner.services.audio_packs.fetcher import LocalAudioPackFetcher

        packs_root, pack_id = _import_pack(tmp_path)
        cfg = dataclasses.replace(
            base_config,
            audio_packs_root=packs_root,
            anki_fields=_map_audio_field(base_config),
            expression_audio_chain=(
                AudioSourceEntry(kind="pack", pack_id=pack_id, enabled=True),
                AudioSourceEntry(kind="jpod101", enabled=True),
            ),
        )
        fetcher = service_factory._build_expression_audio_fetcher(cfg)
        assert isinstance(fetcher, ChainedExpressionAudioFetcher)
        assert len(fetcher._fetchers) == 2
        assert isinstance(fetcher._fetchers[0], LocalAudioPackFetcher)
        assert fetcher._fetchers[0].pack_id == pack_id
        assert isinstance(fetcher._fetchers[1], JPod101AudioFetcher)

    def test_jpod_before_pack_chain_order(self, tmp_path, base_config):
        """Config ordering [jpod101, pack] → chain is [JPod101AudioFetcher, LocalAudioPackFetcher]."""
        from anki_miner.services.audio_packs.fetcher import LocalAudioPackFetcher

        packs_root, pack_id = _import_pack(tmp_path)
        cfg = dataclasses.replace(
            base_config,
            audio_packs_root=packs_root,
            anki_fields=_map_audio_field(base_config),
            expression_audio_chain=(
                AudioSourceEntry(kind="jpod101", enabled=True),
                AudioSourceEntry(kind="pack", pack_id=pack_id, enabled=True),
            ),
        )
        fetcher = service_factory._build_expression_audio_fetcher(cfg)
        assert isinstance(fetcher, ChainedExpressionAudioFetcher)
        assert len(fetcher._fetchers) == 2
        assert isinstance(fetcher._fetchers[0], JPod101AudioFetcher)
        assert isinstance(fetcher._fetchers[1], LocalAudioPackFetcher)

    def test_pack_entry_disabled_excluded_from_chain(self, tmp_path, base_config):
        """A disabled pack entry is excluded; jpod101 still present."""
        packs_root, pack_id = _import_pack(tmp_path)
        cfg = dataclasses.replace(
            base_config,
            audio_packs_root=packs_root,
            anki_fields=_map_audio_field(base_config),
            expression_audio_chain=(
                AudioSourceEntry(kind="pack", pack_id=pack_id, enabled=False),
                AudioSourceEntry(kind="jpod101", enabled=True),
            ),
        )
        fetcher = service_factory._build_expression_audio_fetcher(cfg)
        assert isinstance(fetcher, ChainedExpressionAudioFetcher)
        assert len(fetcher._fetchers) == 1
        assert isinstance(fetcher._fetchers[0], JPod101AudioFetcher)

    def test_unknown_pack_id_skipped_warns(self, tmp_path, base_config, caplog):
        """Unknown pack_id is skipped; warning surfaces in load_result and logs."""
        packs_root = tmp_path / "audio_packs"
        packs_root.mkdir()
        cfg = dataclasses.replace(
            base_config,
            audio_packs_root=packs_root,
            anki_fields=_map_audio_field(base_config),
            expression_audio_chain=(
                AudioSourceEntry(kind="pack", pack_id="nonexistent_pack", enabled=True),
                AudioSourceEntry(kind="jpod101", enabled=True),
            ),
        )
        load_result = service_factory.ServiceLoadResult()
        with caplog.at_level(logging.WARNING):
            fetcher = service_factory._build_expression_audio_fetcher(cfg, load_result)

        # jpod101 still in chain
        assert isinstance(fetcher, ChainedExpressionAudioFetcher)
        assert len(fetcher._fetchers) == 1
        assert isinstance(fetcher._fetchers[0], JPod101AudioFetcher)

        # Warning surfaced in load_result
        assert any("nonexistent_pack" in w for w in load_result.warnings)

        # Warning also appeared in log records
        assert any("nonexistent_pack" in r.message for r in caplog.records)

    def test_field_unmapped_pack_entries_no_registry_io(self, monkeypatch, tmp_path, base_config):
        """Unmapped expression_audio field → no registry construction even with pack entries."""
        constructed = []

        class _TrackingRegistry:
            def __init__(self, *args, **kwargs):
                constructed.append(True)

            def load(self):
                pass

            def build_fetcher_chain(self, *a, **kw):
                return []

        monkeypatch.setattr(service_factory, "AudioPackRegistry", _TrackingRegistry, raising=True)
        cfg = dataclasses.replace(
            base_config,
            audio_packs_root=tmp_path / "audio_packs",
            # expression_audio field left unmapped (base_config default "") → feature off
            expression_audio_chain=(
                AudioSourceEntry(kind="pack", pack_id="some-pack", enabled=True),
                AudioSourceEntry(kind="jpod101", enabled=True),
            ),
        )
        load_result = service_factory.ServiceLoadResult()
        fetcher = service_factory._build_expression_audio_fetcher(cfg, load_result)

        assert constructed == [], "registry must not be constructed when the field is unmapped"
        # Pack entry skipped silently; jpod101 still present for type uniformity.
        assert isinstance(fetcher, ChainedExpressionAudioFetcher)
        assert len(fetcher._fetchers) == 1
        assert isinstance(fetcher._fetchers[0], JPod101AudioFetcher)
        assert load_result.warnings == [], "unmapped feature must surface no pack warnings"

    def test_all_disabled_empty_chain_fetch_returns_none(self, base_config):
        """All entries disabled → empty chain; fetch returns None without crash."""
        cfg = dataclasses.replace(
            base_config,
            expression_audio_chain=(AudioSourceEntry(kind="jpod101", enabled=False),),
        )
        fetcher = service_factory._build_expression_audio_fetcher(cfg)
        assert isinstance(fetcher, ChainedExpressionAudioFetcher)
        assert len(fetcher._fetchers) == 0
        assert fetcher.fetch("食べる", "たべる") is None

    def test_two_packs_interleaved_with_jpod_chain_order(self, tmp_path, base_config):
        """[pack_a, jpod101, pack_b] → chain order is [pack_a, jpod101, pack_b]."""
        from anki_miner.services.audio_packs.fetcher import LocalAudioPackFetcher

        packs_root_a, pack_a_id = _import_pack(tmp_path, pack_dir_name="pack_a")
        pack_b_src = _make_ajt_pack(tmp_path / "pack_b_files")
        result_b = import_audio_pack(pack_b_src, packs_root_a)
        pack_b_id = result_b.pack_id

        cfg = dataclasses.replace(
            base_config,
            audio_packs_root=packs_root_a,
            anki_fields=_map_audio_field(base_config),
            expression_audio_chain=(
                AudioSourceEntry(kind="pack", pack_id=pack_a_id, enabled=True),
                AudioSourceEntry(kind="jpod101", enabled=True),
                AudioSourceEntry(kind="pack", pack_id=pack_b_id, enabled=True),
            ),
        )
        fetcher = service_factory._build_expression_audio_fetcher(cfg)
        assert isinstance(fetcher, ChainedExpressionAudioFetcher)
        assert len(fetcher._fetchers) == 3
        assert isinstance(fetcher._fetchers[0], LocalAudioPackFetcher)
        assert fetcher._fetchers[0].pack_id == pack_a_id
        assert isinstance(fetcher._fetchers[1], JPod101AudioFetcher)
        assert isinstance(fetcher._fetchers[2], LocalAudioPackFetcher)
        assert fetcher._fetchers[2].pack_id == pack_b_id

    def test_googletts_enabled_in_chain_after_jpod101(self, base_config):
        """Enabled googletts entry yields a GoogleTranslateAudioFetcher after jpod101."""
        cfg = dataclasses.replace(
            base_config,
            expression_audio_chain=(
                AudioSourceEntry(kind="jpod101", enabled=True),
                AudioSourceEntry(kind="googletts", enabled=True),
            ),
        )
        fetcher = service_factory._build_expression_audio_fetcher(cfg)
        assert isinstance(fetcher, ChainedExpressionAudioFetcher)
        assert len(fetcher._fetchers) == 2
        assert isinstance(fetcher._fetchers[0], JPod101AudioFetcher)
        assert isinstance(fetcher._fetchers[1], GoogleTranslateAudioFetcher)

    def test_googletts_before_jpod101_chain_order(self, base_config):
        """Config ordering [googletts, jpod101] is preserved in the chain."""
        cfg = dataclasses.replace(
            base_config,
            expression_audio_chain=(
                AudioSourceEntry(kind="googletts", enabled=True),
                AudioSourceEntry(kind="jpod101", enabled=True),
            ),
        )
        fetcher = service_factory._build_expression_audio_fetcher(cfg)
        assert isinstance(fetcher, ChainedExpressionAudioFetcher)
        assert len(fetcher._fetchers) == 2
        assert isinstance(fetcher._fetchers[0], GoogleTranslateAudioFetcher)
        assert isinstance(fetcher._fetchers[1], JPod101AudioFetcher)

    def test_googletts_disabled_excluded_from_chain(self, base_config):
        """A disabled googletts entry is skipped; jpod101 still present."""
        cfg = dataclasses.replace(
            base_config,
            expression_audio_chain=(
                AudioSourceEntry(kind="jpod101", enabled=True),
                AudioSourceEntry(kind="googletts", enabled=False),
            ),
        )
        fetcher = service_factory._build_expression_audio_fetcher(cfg)
        assert isinstance(fetcher, ChainedExpressionAudioFetcher)
        assert len(fetcher._fetchers) == 1
        assert isinstance(fetcher._fetchers[0], JPod101AudioFetcher)

    def test_googletts_construction_no_disk_io(self, tmp_path, base_config):
        """Building a chain with a googletts entry creates no cache dir at build time."""
        googletts_cache = tmp_path / "audio_cache" / "googletts"
        # Point ANKI_MINER_HOME at tmp_path so the googletts cache would land here.
        import anki_miner.gui.utils.service_factory as sf

        original_home = sf.ANKI_MINER_HOME
        try:
            sf.ANKI_MINER_HOME = tmp_path
            cfg = dataclasses.replace(
                base_config,
                expression_audio_chain=(
                    AudioSourceEntry(kind="jpod101", enabled=True),
                    AudioSourceEntry(kind="googletts", enabled=True),
                ),
            )
            fetcher = sf._build_expression_audio_fetcher(cfg)
        finally:
            sf.ANKI_MINER_HOME = original_home

        assert isinstance(fetcher, ChainedExpressionAudioFetcher)
        assert any(isinstance(f, GoogleTranslateAudioFetcher) for f in fetcher._fetchers)
        assert not googletts_cache.exists(), "googletts cache dir must not be created at build time"

    def test_custom_entry_builds_custom_fetcher(self, base_config):
        """A custom URL entry yields a CustomAudioFetcher after jpod101."""
        cfg = dataclasses.replace(
            base_config,
            expression_audio_chain=(
                AudioSourceEntry(kind="jpod101", enabled=True),
                AudioSourceEntry(kind="custom", url="http://localhost:5050/?t={term}&r={reading}", enabled=True),
            ),
        )
        fetcher = service_factory._build_expression_audio_fetcher(cfg)
        assert len(fetcher._fetchers) == 2
        assert isinstance(fetcher._fetchers[0], JPod101AudioFetcher)
        assert isinstance(fetcher._fetchers[1], CustomAudioFetcher)
        assert fetcher._fetchers[1]._kind == "custom"

    def test_custom_json_entry_builds_custom_fetcher(self, base_config):
        """A custom_json entry yields a CustomAudioFetcher with kind custom_json."""
        cfg = dataclasses.replace(
            base_config,
            expression_audio_chain=(AudioSourceEntry(kind="custom_json", url="http://h/list?t={term}", enabled=True),),
        )
        fetcher = service_factory._build_expression_audio_fetcher(cfg)
        assert len(fetcher._fetchers) == 1
        assert isinstance(fetcher._fetchers[0], CustomAudioFetcher)
        assert fetcher._fetchers[0]._kind == "custom_json"

    def test_custom_entry_missing_url_skipped_warns(self, base_config):
        """A custom entry with no URL is skipped and surfaces a warning."""
        cfg = dataclasses.replace(
            base_config,
            expression_audio_chain=(
                AudioSourceEntry(kind="custom", url=None, enabled=True),
                AudioSourceEntry(kind="jpod101", enabled=True),
            ),
        )
        load_result = service_factory.ServiceLoadResult()
        fetcher = service_factory._build_expression_audio_fetcher(cfg, load_result)
        assert len(fetcher._fetchers) == 1
        assert isinstance(fetcher._fetchers[0], JPod101AudioFetcher)
        assert any("no URL" in w for w in load_result.warnings)

    def test_custom_cache_dir_is_per_url_slug(self, base_config):
        """Two custom entries with distinct URLs get distinct per-slug cache dirs."""
        cfg = dataclasses.replace(
            base_config,
            expression_audio_chain=(
                AudioSourceEntry(kind="custom", url="http://a/?t={term}", enabled=True),
                AudioSourceEntry(kind="custom", url="http://b/?t={term}", enabled=True),
            ),
        )
        fetcher = service_factory._build_expression_audio_fetcher(cfg)
        dir_a = fetcher._fetchers[0]._cache_dir
        dir_b = fetcher._fetchers[1]._cache_dir
        assert dir_a != dir_b
        assert dir_a.name.startswith("custom_")

    def test_duplicate_pack_id_two_fetchers(self, tmp_path, base_config):
        """Two enabled entries with the same pack_id → chain has 2 fetchers (same object twice)."""
        from anki_miner.services.audio_packs.fetcher import LocalAudioPackFetcher

        packs_root, pack_id = _import_pack(tmp_path)
        cfg = dataclasses.replace(
            base_config,
            audio_packs_root=packs_root,
            anki_fields=_map_audio_field(base_config),
            expression_audio_chain=(
                AudioSourceEntry(kind="pack", pack_id=pack_id, enabled=True),
                AudioSourceEntry(kind="pack", pack_id=pack_id, enabled=True),
            ),
        )
        fetcher = service_factory._build_expression_audio_fetcher(cfg)
        assert isinstance(fetcher, ChainedExpressionAudioFetcher)
        assert len(fetcher._fetchers) == 2
        assert isinstance(fetcher._fetchers[0], LocalAudioPackFetcher)
        assert isinstance(fetcher._fetchers[1], LocalAudioPackFetcher)
        # same object queried twice — accepted behavior per factory comment
        assert fetcher._fetchers[0] is fetcher._fetchers[1]


# ---------------------------------------------------------------------------
# create_services integration: expression_audio_fetcher is always a
# ChainedExpressionAudioFetcher (Services.expression_audio_fetcher is non-Optional)
# ---------------------------------------------------------------------------


class TestCreateServicesAudioChain:
    def test_default_config_services_fetcher_is_chained(self, base_config):
        """create_services with default config yields a ChainedExpressionAudioFetcher."""
        services = service_factory.create_services(base_config)
        assert isinstance(services.expression_audio_fetcher, ChainedExpressionAudioFetcher)
        # Default: single jpod101 entry
        assert len(services.expression_audio_fetcher._fetchers) == 1
        assert isinstance(services.expression_audio_fetcher._fetchers[0], JPod101AudioFetcher)

    def test_default_config_no_registry_io(self, monkeypatch, base_config):
        """create_services with default config never constructs AudioPackRegistry."""
        constructed = []

        class _TrackingRegistry:
            def __init__(self, *args, **kwargs):
                constructed.append(True)

            def load(self):
                pass

            def build_fetcher_chain(self, *a, **kw):
                return []

        monkeypatch.setattr(service_factory, "AudioPackRegistry", _TrackingRegistry, raising=True)
        service_factory.create_services(base_config)
        assert constructed == [], "AudioPackRegistry must not be I/O-accessed for default config"

    def test_pack_entry_config_chain_respected(self, tmp_path, base_config):
        """create_services with a pack entry produces the pack fetcher in the chain."""
        from anki_miner.services.audio_packs.fetcher import LocalAudioPackFetcher

        packs_root, pack_id = _import_pack(tmp_path)
        cfg = dataclasses.replace(
            base_config,
            audio_packs_root=packs_root,
            anki_fields=_map_audio_field(base_config),
            expression_audio_chain=(
                AudioSourceEntry(kind="pack", pack_id=pack_id, enabled=True),
                AudioSourceEntry(kind="jpod101", enabled=True),
            ),
        )
        services = service_factory.create_services(cfg)
        fetcher = services.expression_audio_fetcher
        assert isinstance(fetcher, ChainedExpressionAudioFetcher)
        assert len(fetcher._fetchers) == 2
        assert isinstance(fetcher._fetchers[0], LocalAudioPackFetcher)
        assert isinstance(fetcher._fetchers[1], JPod101AudioFetcher)


class TestBuildSentenceAudioFetcher:
    """Sentence-TTS chain composition (reading sources)."""

    def test_master_off_returns_empty_chain_no_session(self, monkeypatch, base_config):
        """Disabled feature: empty chain, and no requests.Session constructed."""
        import anki_miner.services.sentence_tts_fetcher as stf

        sessions = []
        monkeypatch.setattr(stf, "_new_browser_session", lambda: sessions.append(True) or object(), raising=True)
        fetcher = service_factory._build_sentence_audio_fetcher(base_config)
        assert isinstance(fetcher, ChainedSentenceAudioFetcher)
        assert fetcher._fetchers == []
        assert sessions == [], "no Session may be constructed for a disabled feature"

    def test_enabled_default_order_google_then_papago(self, base_config):
        cfg = dataclasses.replace(base_config, reading_tts_enabled=True)
        fetcher = service_factory._build_sentence_audio_fetcher(cfg)
        assert isinstance(fetcher, ChainedSentenceAudioFetcher)
        assert len(fetcher._fetchers) == 2
        assert isinstance(fetcher._fetchers[0], GoogleSentenceTtsFetcher)
        assert isinstance(fetcher._fetchers[1], PapagoSentenceTtsFetcher)
        fetcher.close()

    def test_google_only(self, base_config):
        cfg = dataclasses.replace(base_config, reading_tts_enabled=True, reading_tts_papago_enabled=False)
        fetcher = service_factory._build_sentence_audio_fetcher(cfg)
        assert len(fetcher._fetchers) == 1
        assert isinstance(fetcher._fetchers[0], GoogleSentenceTtsFetcher)

    def test_papago_only(self, base_config):
        cfg = dataclasses.replace(base_config, reading_tts_enabled=True, reading_tts_google_enabled=False)
        fetcher = service_factory._build_sentence_audio_fetcher(cfg)
        assert len(fetcher._fetchers) == 1
        assert isinstance(fetcher._fetchers[0], PapagoSentenceTtsFetcher)
        fetcher.close()

    def test_both_providers_off_yields_empty_chain(self, base_config):
        cfg = dataclasses.replace(
            base_config,
            reading_tts_enabled=True,
            reading_tts_google_enabled=False,
            reading_tts_papago_enabled=False,
        )
        fetcher = service_factory._build_sentence_audio_fetcher(cfg)
        assert fetcher._fetchers == []

    def test_construction_no_disk_io(self, tmp_path, base_config):
        """Enabled chain creates no cache dir at build time (lazy mkdir on fetch)."""
        import anki_miner.gui.utils.service_factory as sf

        original_home = sf.ANKI_MINER_HOME
        try:
            sf.ANKI_MINER_HOME = tmp_path
            cfg = dataclasses.replace(base_config, reading_tts_enabled=True)
            fetcher = sf._build_sentence_audio_fetcher(cfg)
        finally:
            sf.ANKI_MINER_HOME = original_home

        assert not (tmp_path / "audio_cache" / "sentence_tts").exists()
        fetcher.close()

    def test_create_services_wires_sentence_fetcher(self, base_config):
        services = service_factory.create_services(base_config)
        assert isinstance(services.sentence_audio_fetcher, ChainedSentenceAudioFetcher)
        assert services.sentence_audio_fetcher._fetchers == []  # default config: inert
