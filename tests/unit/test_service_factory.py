"""Tests for service_factory DI wiring: expression audio and AnkiService injection."""

import dataclasses
from pathlib import Path
from unittest.mock import patch

import pytest

from anki_miner.config import AnkiMinerConfig, ChainEntry
from anki_miner.gui.utils import service_factory
from anki_miner.services.anki_service import AnkiService
from anki_miner.services.definition_service import DefinitionService
from anki_miner.services.expression_audio_fetcher import ChainedExpressionAudioFetcher, JPod101AudioFetcher


@pytest.fixture
def base_config(tmp_path):
    """Config whose on-disk paths live under tmp_path, not ~/.anki_miner."""
    return dataclasses.replace(
        AnkiMinerConfig(),
        dicts_root=tmp_path / "dicts",
        known_words_db_path=tmp_path / "known_words.db",
        history_db_path=tmp_path / "history.db",
        stats_db_path=tmp_path / "stats.db",
    )


def test_create_services_wires_expression_audio_fetcher(base_config):
    """create_services returns a Services whose expression_audio_fetcher is a
    ChainedExpressionAudioFetcher wrapping (for the default jpod101-only chain)
    one JPod101AudioFetcher with _delay == config.expression_audio_delay and
    _cache_dir == ANKI_MINER_HOME / 'audio_cache' / 'jpod101'.
    """
    cfg = dataclasses.replace(base_config, expression_audio_delay=0.5)
    services = service_factory.create_services(cfg)

    fetcher = services.expression_audio_fetcher
    assert isinstance(fetcher, ChainedExpressionAudioFetcher)
    assert len(fetcher._fetchers) == 1
    jpod = fetcher._fetchers[0]
    assert isinstance(jpod, JPod101AudioFetcher)
    assert jpod._delay == 0.5
    assert jpod._cache_dir == service_factory.ANKI_MINER_HOME / "audio_cache" / "jpod101"


def test_create_episode_processor_wires_same_fetcher(base_config):
    """create_episode_processor passes the expression_audio_fetcher from
    create_services onto the EpisodeProcessor unchanged.
    """

    class _NullPresenter:
        def show_info(self, msg: str) -> None:
            pass

        def show_warning(self, msg: str) -> None:
            pass

        def show_error(self, msg: str) -> None:
            pass

        def update_progress(self, current: int, total: int, msg: str = "") -> None:
            pass

        def show_result(self, result: object) -> None:
            pass

    cfg = dataclasses.replace(base_config, expression_audio_delay=0.3)
    processor = service_factory.create_episode_processor(cfg, presenter=_NullPresenter())

    fetcher = processor.expression_audio_fetcher
    assert isinstance(fetcher, ChainedExpressionAudioFetcher)
    assert len(fetcher._fetchers) == 1
    jpod = fetcher._fetchers[0]
    assert isinstance(jpod, JPod101AudioFetcher)
    assert jpod._delay == 0.3
    assert jpod._cache_dir == service_factory.ANKI_MINER_HOME / "audio_cache" / "jpod101"


# ---------------------------------------------------------------------------
# AnkiService DI injection (OVH-011/013)
# ---------------------------------------------------------------------------


def test_create_services_uses_provided_anki_service(base_config):
    """When anki_service is passed to create_services, the same instance is
    returned in Services (identity check — no new AnkiService is built)."""
    shared = AnkiService(base_config)
    services = service_factory.create_services(base_config, anki_service=shared)
    assert services.anki_service is shared


def test_create_services_builds_fresh_anki_service_by_default(base_config):
    """Default path (anki_service=None) builds a new AnkiService per call."""
    s1 = service_factory.create_services(base_config)
    s2 = service_factory.create_services(base_config)
    assert s1.anki_service is not s2.anki_service


class _NullPresenter:
    def show_info(self, msg: str) -> None:
        pass

    def show_warning(self, msg: str) -> None:
        pass

    def show_error(self, msg: str) -> None:
        pass

    def update_progress(self, current: int, total: int, msg: str = "") -> None:
        pass

    def show_result(self, result: object) -> None:
        pass


def test_create_episode_processor_reuses_provided_anki_service(base_config):
    """create_episode_processor(..., anki_service=shared) wires the passed
    instance onto the EpisodeProcessor (identity check)."""
    shared = AnkiService(base_config)
    processor = service_factory.create_episode_processor(base_config, _NullPresenter(), anki_service=shared)
    assert processor.anki_service is shared


def test_create_episode_processor_default_builds_fresh_anki_service(base_config):
    """Default path (anki_service=None) builds a fresh AnkiService per call."""
    p1 = service_factory.create_episode_processor(base_config, _NullPresenter())
    p2 = service_factory.create_episode_processor(base_config, _NullPresenter())
    assert p1.anki_service is not p2.anki_service


# ---------------------------------------------------------------------------
# OVH-048: registry.load() OSError routes into load_result.warnings
# ---------------------------------------------------------------------------


class TestRegistryOSErrorInServiceFactory:
    """When the dicts_root scan raises OSError, build_definition_service and
    create_services must survive and produce a working (Jisho-only) service."""

    def _jisho_config(self, tmp_path: Path) -> AnkiMinerConfig:
        """Config pointing at a non-existent dicts root with a Jisho-only chain."""
        return dataclasses.replace(
            AnkiMinerConfig(),
            dicts_root=tmp_path / "dicts",
            known_words_db_path=tmp_path / "known_words.db",
            history_db_path=tmp_path / "history.db",
            stats_db_path=tmp_path / "stats.db",
            dictionary_chain=(ChainEntry(kind="jisho", dict_id=None, enabled=True),),
        )

    def test_build_definition_service_survives_oserror_scan(self, tmp_path: Path):
        """build_definition_service does not raise when registry.load() hits OSError."""
        cfg = self._jisho_config(tmp_path)
        load_result = service_factory.ServiceLoadResult()

        with patch.object(Path, "iterdir", side_effect=OSError("stale NFS")):
            svc = service_factory.build_definition_service(cfg, load_result)

        assert isinstance(svc, DefinitionService)

    def test_build_definition_service_oserror_routes_warning_via_registry(self, tmp_path: Path):
        """When the OSError is caught by registry.load(), service factory stays alive."""
        cfg = self._jisho_config(tmp_path)
        # Make dicts_root exist so is_dir() passes but iterdir() raises.
        dicts_root = tmp_path / "dicts"
        dicts_root.mkdir()
        load_result = service_factory.ServiceLoadResult()

        with patch.object(Path, "iterdir", side_effect=OSError("permission denied")):
            svc = service_factory.build_definition_service(cfg, load_result)

        # Service is functional (Jisho-only chain).
        assert isinstance(svc, DefinitionService)

    def test_create_services_survives_oserror_scan(self, tmp_path: Path):
        """create_services returns a valid Services bundle even when the dicts
        root scan raises OSError — GUI stays alive with the Jisho-only chain."""
        cfg = self._jisho_config(tmp_path)

        with patch.object(Path, "iterdir", side_effect=OSError("permission denied")):
            services = service_factory.create_services(cfg)

        assert isinstance(services.definition_service, DefinitionService)


# ---------------------------------------------------------------------------
# Multi-source frequency wiring (Multiple Additive Frequency Sources)
# ---------------------------------------------------------------------------


class TestFrequencyServiceWiring:
    """create_services builds a MultiFrequencyService from the freqs_root chain."""

    def _import_source(self, freqs_root: Path, tmp_path: Path) -> str:
        """Build one real on-disk frequency source via the importer; return its id."""
        from anki_miner.services.frequency.source_importer import import_frequency_source

        csv = tmp_path / "ranks.csv"
        csv.write_text("rank,word\n1,猫\n2,犬\n3,食べる\n", encoding="utf-8")
        result = import_frequency_source(csv, freqs_root, source_id="testfreq")
        return result.source_id

    def _config(self, tmp_path: Path, *, chain, use_frequency_data=True) -> AnkiMinerConfig:
        from anki_miner.config import FreqEntry

        return dataclasses.replace(
            AnkiMinerConfig(),
            dicts_root=tmp_path / "dicts",
            known_words_db_path=tmp_path / "known_words.db",
            history_db_path=tmp_path / "history.db",
            stats_db_path=tmp_path / "stats.db",
            freqs_root=tmp_path / "freqs",
            use_frequency_data=use_frequency_data,
            frequency_chain=tuple(FreqEntry(source_id=sid) for sid in chain),
        )

    def test_returns_multi_service_resolving_known_term(self, tmp_path: Path):
        """An enabled chain entry pointing at a real source → a working service."""
        from anki_miner.services.frequency.multi_frequency_service import MultiFrequencyService

        freqs_root = tmp_path / "freqs"
        source_id = self._import_source(freqs_root, tmp_path)
        cfg = self._config(tmp_path, chain=[source_id])

        services = service_factory.create_services(cfg)

        assert isinstance(services.frequency_service, MultiFrequencyService)
        assert services.frequency_service.is_available()
        assert services.frequency_service.lookup_min("食べる") == 3
        # lookup_all reports (display name, rank, display_value); the CSV stem is
        # the source name, and a CSV rank has no display string (None).
        assert services.frequency_service.lookup_all("猫") == [("ranks", 1, None)]
        # Human-readable info line mentions source count + total entries.
        joined = " ".join(services.load_result.info)
        assert "Frequency data loaded" in joined
        assert "3" in joined  # 3 entries

    def test_empty_chain_yields_none(self, tmp_path: Path):
        """use_frequency_data on but no chain entries → no service."""
        # Import a source on disk but reference none of it in the chain.
        freqs_root = tmp_path / "freqs"
        self._import_source(freqs_root, tmp_path)
        cfg = self._config(tmp_path, chain=[])

        services = service_factory.create_services(cfg)

        assert services.frequency_service is None

    def test_use_frequency_data_off_yields_none(self, tmp_path: Path):
        """Toggle off → no service even with a populated chain."""
        freqs_root = tmp_path / "freqs"
        source_id = self._import_source(freqs_root, tmp_path)
        cfg = self._config(tmp_path, chain=[source_id], use_frequency_data=False)

        services = service_factory.create_services(cfg)

        assert services.frequency_service is None

    def test_missing_source_yields_none_without_crash(self, tmp_path: Path):
        """A chain entry whose source is absent on disk → None, no exception."""
        cfg = self._config(tmp_path, chain=["does-not-exist"])

        services = service_factory.create_services(cfg)

        assert services.frequency_service is None

    def test_load_failure_does_not_crash(self, tmp_path: Path):
        """A registry scan that raises is swallowed into a warning, not a crash."""
        source_id = self._import_source(tmp_path / "freqs", tmp_path)
        cfg = self._config(tmp_path, chain=[source_id])

        with patch.object(Path, "iterdir", side_effect=OSError("boom")):
            services = service_factory.create_services(cfg)

        # registry.load() swallows OSError internally → no sources → None.
        assert services.frequency_service is None


class TestCompoundMatchingInjection:
    """term_lookup wiring: injected iff toggle on AND an enabled indexed dict."""

    def test_injected_with_enabled_indexed_entry(self, base_config):
        cfg = dataclasses.replace(
            base_config,
            dictionary_chain=(ChainEntry(kind="indexed", dict_id="jmdict-english", enabled=True),),
        )
        services = service_factory.create_services(cfg)
        matcher = services.subtitle_parser._compound_matcher
        assert matcher is not None
        assert matcher._lookup == services.definition_service.offline_terms_exist

    def test_not_injected_without_indexed_entry(self, base_config):
        cfg = dataclasses.replace(
            base_config,
            dictionary_chain=(ChainEntry(kind="jisho", dict_id=None, enabled=True),),
        )
        services = service_factory.create_services(cfg)
        assert services.subtitle_parser._compound_matcher is None

    def test_not_injected_when_toggle_off(self, base_config):
        cfg = dataclasses.replace(
            base_config,
            compound_matching=False,
            dictionary_chain=(ChainEntry(kind="indexed", dict_id="jmdict-english", enabled=True),),
        )
        services = service_factory.create_services(cfg)
        assert services.subtitle_parser._compound_matcher is None

    def test_not_injected_for_disabled_indexed_entry(self, base_config):
        cfg = dataclasses.replace(
            base_config,
            dictionary_chain=(ChainEntry(kind="indexed", dict_id="jmdict-english", enabled=False),),
        )
        services = service_factory.create_services(cfg)
        assert services.subtitle_parser._compound_matcher is None

    def test_prebuilt_parser_untouched(self, base_config):
        from anki_miner.services.subtitle_parser import SubtitleParserService

        cfg = dataclasses.replace(
            base_config,
            dictionary_chain=(ChainEntry(kind="indexed", dict_id="jmdict-english", enabled=True),),
        )
        prebuilt = SubtitleParserService(cfg)  # no lookup — caller's choice stands
        services = service_factory.create_services(cfg, subtitle_parser=prebuilt)
        assert services.subtitle_parser is prebuilt
        assert services.subtitle_parser._compound_matcher is None

    def test_kana_probe_injected_when_compound_off(self, base_config):
        """mine_kana_only_words wires the probe even with compound matching off."""
        cfg = dataclasses.replace(
            base_config,
            compound_matching=False,
            mine_kana_only_words=True,
            dictionary_chain=(ChainEntry(kind="indexed", dict_id="jmdict-english", enabled=True),),
        )
        services = service_factory.create_services(cfg)
        parser = services.subtitle_parser
        assert parser._compound_matcher is None  # compound feature stays off
        assert parser._kana_probe == services.definition_service.offline_terms_exist

    def test_kana_probe_not_injected_without_indexed_dict(self, base_config):
        cfg = dataclasses.replace(
            base_config,
            compound_matching=False,
            mine_kana_only_words=True,
            dictionary_chain=(ChainEntry(kind="jisho", dict_id=None, enabled=True),),
        )
        services = service_factory.create_services(cfg)
        assert services.subtitle_parser._kana_probe is None

    def test_kana_probe_off_by_default(self, base_config):
        cfg = dataclasses.replace(
            base_config,
            dictionary_chain=(ChainEntry(kind="indexed", dict_id="jmdict-english", enabled=True),),
        )
        services = service_factory.create_services(cfg)
        assert services.subtitle_parser._kana_probe is None
