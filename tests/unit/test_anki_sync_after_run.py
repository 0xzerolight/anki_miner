"""AI-03 (ruling R6): one AnkiConnect sync after a run that added cards; failure is a warning."""

from __future__ import annotations

import logging
from dataclasses import replace
from unittest.mock import MagicMock, call, patch

import pytest

from anki_miner.exceptions import AnkiBusyError, AnkiConnectionError
from anki_miner.services.anki_service import AnkiService, sync_after_run

_POST = "anki_miner.services.anki_service.post_action"
_HTTP = "anki_miner.services._ankiconnect.requests.post"
_LOGGER = "anki_miner.services.anki_service"
_BUSY = "Collection lifecycle operation is active."


class _Clock:
    """A fake monotonic clock that ``sleep`` advances, so a two-minute wait runs instantly."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    fake = _Clock()
    monkeypatch.setattr("anki_miner.services.anki_service.time.monotonic", fake.monotonic)
    monkeypatch.setattr("anki_miner.services.anki_service.time.sleep", fake.sleep)
    return fake


def _replies(*errors):
    """AnkiConnect HTTP replies in order: ``None`` answers, a string is that error."""
    responses = []
    for error in errors:
        response = MagicMock()
        response.content = b"{}"
        response.json.return_value = {"result": [] if error is None else None, "error": error}
        responses.append(response)
    return responses


def _actions(http) -> list[str]:
    return [c.kwargs["json"]["action"] for c in http.call_args_list]


def test_sync_sends_the_sync_action(test_config):
    with patch(_POST, return_value=None) as post:
        AnkiService(test_config).sync()

    assert post.call_args_list[0] == call(test_config.ankiconnect_url, "sync", timeout=120)


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

    assert [c.args[1] for c in post.call_args_list].count("sync") == 1
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


def test_sync_waits_until_anki_has_finished_syncing(test_config, clock, caplog):
    """AnkiConnect answers ``sync`` at once and refuses everything until the sync ends."""
    with (
        patch(_HTTP, side_effect=_replies(None, _BUSY, _BUSY, None)) as http,
        caplog.at_level(logging.INFO, logger=_LOGGER),
    ):
        AnkiService(test_config).sync()

    assert _actions(http) == ["sync", "deckNames", "deckNames", "deckNames"]
    waits = [r for r in caplog.records if r.getMessage().startswith("Anki sync running, waiting")]
    assert len(waits) == 1


def test_sync_against_a_free_anki_does_not_wait(test_config, clock, caplog):
    with patch(_HTTP, side_effect=_replies(None, None)) as http, caplog.at_level(logging.INFO, logger=_LOGGER):
        AnkiService(test_config).sync()

    assert _actions(http) == ["sync", "deckNames"]
    assert clock.sleeps == []
    assert not any(r.getMessage().startswith("Anki sync running") for r in caplog.records)


def test_the_wait_is_bounded(test_config, clock):
    started = clock.now
    with patch(_HTTP, side_effect=_replies(None, *[_BUSY] * 500)), pytest.raises(AnkiBusyError):
        AnkiService(test_config).sync()

    assert clock.now - started == pytest.approx(120)


def test_a_cancel_ends_the_wait(test_config, clock):
    checks = iter([False, True])
    with patch(_HTTP, side_effect=_replies(None, _BUSY, _BUSY, _BUSY)) as http:
        AnkiService(test_config).sync(lambda: next(checks))

    assert _actions(http) == ["sync", "deckNames", "deckNames"]


def test_a_sync_still_running_when_the_wait_ends_is_a_warning(test_config, clock, caplog):
    config = replace(test_config, sync_anki_after_run=True)
    with (
        patch(_HTTP, side_effect=_replies(None, *[_BUSY] * 500)),
        caplog.at_level(logging.WARNING, logger=_LOGGER),
    ):
        assert sync_after_run(config, 4) is False

    [line] = [r for r in caplog.records if r.getMessage().startswith("Anki sync after run failed:")]
    assert "AnkiBusyError" in line.getMessage()


def test_sync_after_run_passes_the_cancel_check_to_the_wait(test_config):
    cancelled = MagicMock(return_value=False)
    with patch.object(AnkiService, "sync") as sync:
        assert sync_after_run(replace(test_config, sync_anki_after_run=True), 1, cancelled) is True

    sync.assert_called_once_with(cancelled)
