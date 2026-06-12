"""Tests for expression audio DI wiring in service_factory."""

import dataclasses

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.config.paths import ANKI_MINER_HOME
from anki_miner.gui.utils import service_factory
from anki_miner.services.expression_audio_fetcher import JPod101AudioFetcher


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
    JPod101AudioFetcher with _delay == config.expression_audio_delay and
    _cache_dir == ANKI_MINER_HOME / 'audio_cache' / 'jpod101'.
    """
    cfg = dataclasses.replace(base_config, expression_audio_delay=0.5)
    services = service_factory.create_services(cfg)

    fetcher = services.expression_audio_fetcher
    assert isinstance(fetcher, JPod101AudioFetcher)
    assert fetcher._delay == 0.5
    assert fetcher._cache_dir == ANKI_MINER_HOME / "audio_cache" / "jpod101"


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
    assert isinstance(fetcher, JPod101AudioFetcher)
    assert fetcher._delay == 0.3
    assert fetcher._cache_dir == ANKI_MINER_HOME / "audio_cache" / "jpod101"
