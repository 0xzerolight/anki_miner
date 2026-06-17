"""Tests for service_factory DI wiring: expression audio and AnkiService injection."""

import dataclasses

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.utils import service_factory
from anki_miner.services.anki_service import AnkiService
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
