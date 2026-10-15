"""AI-03 (ruling R6): one AnkiConnect sync after a run that added cards; failure is a warning."""

from __future__ import annotations

import logging
from dataclasses import replace
from unittest.mock import patch

from anki_miner.exceptions import AnkiConnectionError
from anki_miner.services.anki_service import AnkiService, sync_after_run

_POST = "anki_miner.services.anki_service.post_action"
_LOGGER = "anki_miner.services.anki_service"


def test_sync_sends_the_sync_action(test_config):
    with patch(_POST, return_value=None) as post:
        AnkiService(test_config).sync()

    post.assert_called_once_with(test_config.ankiconnect_url, "sync", timeout=120)


def test_off_does_nothing(test_config):
    with patch(_POST) as post:
        assert sync_after_run(test_config, 5) is False

    post.assert_not_called()


def test_a_run_without_cards_does_nothing(test_config):
    with patch(_POST) as post:
        assert sync_after_run(replace(test_config, sync_anki_after_run=True), 0) is False

    post.assert_not_called()


def test_on_with_cards_syncs_once(test_config, caplog):
    with patch(_POST, return_value=None) as post, caplog.at_level(logging.INFO, logger=_LOGGER):
        assert sync_after_run(replace(test_config, sync_anki_after_run=True), 3) is True

    assert [call.args[1] for call in post.call_args_list] == ["sync"]
    assert any(r.getMessage() == "Anki sync after run: cards=3" for r in caplog.records)


def test_a_failed_sync_is_a_warning_not_an_error(test_config, caplog):
    config = replace(test_config, sync_anki_after_run=True)
    failure = AnkiConnectionError("AnkiConnect error in 'sync': auth not configured")
    with patch(_POST, side_effect=failure), caplog.at_level(logging.WARNING, logger=_LOGGER):
        assert sync_after_run(config, 2) is False

    lines = [r for r in caplog.records if r.getMessage().startswith("Anki sync after run failed:")]
    assert len(lines) == 1
    assert lines[0].levelno == logging.WARNING
    assert "auth not configured" in lines[0].getMessage()
