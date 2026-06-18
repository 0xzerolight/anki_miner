"""Mocked unit tests for the safety-gated E2E AnkiGateway.

No live Anki / no network: ``post_action`` is patched at its point of use
(``tests.e2e.anki_gateway.post_action``). These tests assert the SAFETY GUARDS
(loopback-only host, deck-name invariant, foreign-deck refusal) and that the
mutating helpers send the exact AnkiConnect action names + param shapes.
"""

import dataclasses
from unittest.mock import patch

import pytest
import requests

from anki_miner.exceptions import AnkiConnectionError
from tests.e2e.anki_gateway import AnkiGateway, AnkiUnreachableError, ForeignDeckError
from tests.e2e.config import E2EConfig

GATEWAY_PA = "tests.e2e.anki_gateway.post_action"


def _config(**overrides) -> E2EConfig:
    return dataclasses.replace(E2EConfig(), **overrides)


def _gateway(**overrides) -> AnkiGateway:
    """Build a gateway with post_action stubbed during construction.

    ``__init__`` only runs the loopback guard (no network), but stub anyway so a
    future construction-time call can't hit the wire.
    """
    with patch(GATEWAY_PA):
        return AnkiGateway(_config(**overrides))


# --- loopback guard --------------------------------------------------------


@pytest.mark.parametrize("url", ["http://127.0.0.1:8765", "http://localhost:8765", "http://[::1]:8765"])
def test_loopback_hosts_allowed(url):
    with patch(GATEWAY_PA):
        AnkiGateway(_config(ankiconnect_url=url))  # must not raise


@pytest.mark.parametrize(
    "url",
    [
        "http://192.168.1.50:8765",
        "http://anki.example.com:8765",
        "http://10.0.0.1:8765",
    ],
)
def test_non_loopback_host_refused(url):
    with patch(GATEWAY_PA), pytest.raises(ValueError, match="non-loopback"):
        AnkiGateway(_config(ankiconnect_url=url))


# --- deck-name invariant ---------------------------------------------------


def test_assert_test_deck_passes_for_configured_name():
    gw = _gateway()
    gw._assert_test_deck(gw.config.deck_name)  # no raise


def test_assert_test_deck_raises_for_other_deck():
    gw = _gateway()
    with pytest.raises(AssertionError, match="own test deck"):
        gw._assert_test_deck("Some Real Deck::Japanese")


def test_mutating_queries_only_interpolate_deck_name():
    """findNotes (the only mutating-by-query path) must use exactly the configured deck."""
    gw = _gateway(deck_name="AnkiMiner E2E TEST")
    with patch(GATEWAY_PA, return_value=[]) as pa:
        gw.notes_in_deck()
    # post_action(url, "findNotes", params, timeout) — positional in _call.
    args = pa.call_args.args
    assert args[1] == "findNotes"
    assert args[2] == {"query": 'deck:"AnkiMiner E2E TEST"'}


# --- foreign-deck refusal --------------------------------------------------


def test_ensure_test_deck_refuses_preexisting_populated_deck():
    gw = _gateway()
    # findNotes (via deck_card_count) returns pre-existing notes.
    with patch(GATEWAY_PA, return_value=[111, 222, 333]) as pa, pytest.raises(ForeignDeckError, match="3 note"):
        gw.ensure_test_deck()
    # Must have bailed BEFORE issuing createDeck.
    actions = [c.args[1] for c in pa.call_args_list]
    assert "createDeck" not in actions
    assert actions == ["findNotes"]


def test_ensure_test_deck_succeeds_when_empty():
    gw = _gateway()
    with patch(GATEWAY_PA, return_value=[]) as pa:
        gw.ensure_test_deck()
    actions = [c.args[1] for c in pa.call_args_list]
    assert actions == ["findNotes", "createDeck"]
    create = next(c for c in pa.call_args_list if c.args[1] == "createDeck")
    assert create.args[2] == {"deck": gw.config.deck_name}


def test_ensure_test_deck_adopts_with_allow_existing():
    gw = _gateway()
    with patch(GATEWAY_PA, return_value=[1, 2]) as pa:
        gw.ensure_test_deck(allow_existing=True)  # no raise
    actions = [c.args[1] for c in pa.call_args_list]
    # Foreign check skipped: goes straight to createDeck (no findNotes probe).
    assert actions == ["createDeck"]


def test_reensure_after_first_skips_foreign_check():
    """A deck the harness itself populated must not trip the foreign guard later."""
    gw = _gateway()
    with patch(GATEWAY_PA, return_value=[]):
        gw.ensure_test_deck()  # first ensure: empty, ok, sets _deck_ensured
    # Now the deck has notes the harness added; re-ensure must not raise.
    with patch(GATEWAY_PA, return_value=[9, 8, 7]) as pa:
        gw.ensure_test_deck()
    actions = [c.args[1] for c in pa.call_args_list]
    assert actions == ["createDeck"]  # no foreign probe


# --- deleteNotes uses exactly the found ids --------------------------------


def test_delete_test_deck_notes_deletes_exact_found_ids():
    gw = _gateway()

    def fake(url, action, params=None, timeout=30):
        if action == "findNotes":
            return [101, 202, 303]
        return None  # deleteNotes

    with patch(GATEWAY_PA, side_effect=fake) as pa:
        count = gw.delete_test_deck_notes()

    assert count == 3
    delete = next(c for c in pa.call_args_list if c.args[1] == "deleteNotes")
    assert delete.args[2] == {"notes": [101, 202, 303]}


def test_delete_test_deck_notes_noop_when_empty():
    gw = _gateway()
    with patch(GATEWAY_PA, return_value=[]) as pa:
        count = gw.delete_test_deck_notes()
    assert count == 0
    # findNotes only; no deleteNotes when there is nothing to delete.
    actions = [c.args[1] for c in pa.call_args_list]
    assert actions == ["findNotes"]


# --- deleteDecks contract --------------------------------------------------


def test_delete_test_deck_sends_correct_params():
    gw = _gateway()
    with patch(GATEWAY_PA, return_value=None) as pa:
        gw.delete_test_deck()
    call = pa.call_args
    assert call.args[1] == "deleteDecks"
    assert call.args[2] == {"decks": [gw.config.deck_name], "cardsToo": True}


# --- read helpers ----------------------------------------------------------


def test_ping_returns_version_string():
    gw = _gateway()
    with patch(GATEWAY_PA, return_value=6) as pa:
        assert gw.ping() == "6"
    assert pa.call_args.args[1] == "version"


def test_notes_info_contract_and_empty_short_circuit():
    gw = _gateway()
    with patch(GATEWAY_PA, return_value=[{"noteId": 1}]) as pa:
        out = gw.notes_info([1])
    assert out == [{"noteId": 1}]
    assert pa.call_args.args[1] == "notesInfo"
    assert pa.call_args.args[2] == {"notes": [1]}

    # Empty input must NOT hit AnkiConnect.
    with patch(GATEWAY_PA) as pa2:
        assert gw.notes_info([]) == []
    pa2.assert_not_called()


# --- unreachable mapping ---------------------------------------------------


def test_connection_failure_maps_to_unreachable():
    gw = _gateway()
    err = AnkiConnectionError("Cannot connect to AnkiConnect. Is Anki running?")
    # Mirror post_action's real wrapping: it raises `from` a
    # requests.exceptions.ConnectionError on a refused socket (NOT the builtin
    # ConnectionError). This is the exact type the gateway keys "unreachable" on.
    err.__cause__ = requests.exceptions.ConnectionError("refused")
    with patch(GATEWAY_PA, side_effect=err), pytest.raises(AnkiUnreachableError):
        gw.ping()


def test_ankiconnect_error_payload_still_propagates():
    """An AnkiConnect server-side error (no socket cause) is NOT 'unreachable'."""
    gw = _gateway()
    err = AnkiConnectionError("AnkiConnect error in 'createDeck': bad name")
    # AnkiUnreachableError is a RuntimeError, NOT an AnkiConnectionError, so
    # raises(AnkiConnectionError) succeeding here proves the error was re-raised
    # unchanged rather than remapped to unreachable.
    with (
        patch(GATEWAY_PA, side_effect=err),
        patch.object(gw, "deck_card_count", return_value=0),
        pytest.raises(AnkiConnectionError),
    ):
        gw.ensure_test_deck()


def test_non_connection_transport_error_not_unreachable():
    """A Timeout/parse failure (wrapped, but not a ConnectionError) propagates as-is."""
    gw = _gateway()
    err = AnkiConnectionError("AnkiConnect call 'version' failed: timed out")
    err.__cause__ = requests.exceptions.Timeout("timed out")
    # Same reasoning: must stay an AnkiConnectionError, not become unreachable.
    with patch(GATEWAY_PA, side_effect=err), pytest.raises(AnkiConnectionError):
        gw.ping()


# --- config ----------------------------------------------------------------


def test_config_from_env_overrides(monkeypatch):
    monkeypatch.setenv("ANKI_MINER_E2E_HOME", "/tmp/e2e_home_xyz")
    monkeypatch.setenv("ANKI_MINER_E2E_ANKICONNECT_URL", "http://localhost:9999")
    cfg = E2EConfig.from_env()
    from pathlib import Path

    assert cfg.test_home == Path("/tmp/e2e_home_xyz")
    assert cfg.ankiconnect_url == "http://localhost:9999"
    # runs_root tracks the overridden home.
    assert cfg.runs_root == Path("/tmp/e2e_home_xyz") / "runs"


def test_config_defaults(monkeypatch):
    monkeypatch.delenv("ANKI_MINER_E2E_HOME", raising=False)
    monkeypatch.delenv("ANKI_MINER_E2E_ANKICONNECT_URL", raising=False)
    cfg = E2EConfig.from_env()
    assert cfg.deck_name == "AnkiMiner E2E TEST"
    assert cfg.ankiconnect_url == "http://127.0.0.1:8765"
    assert cfg.note_type == "AnkiMiner E2E Basic"
    assert cfg.curation_policy == "all"
    assert cfg.runs_root == cfg.test_home / "runs"


def test_config_is_frozen():
    cfg = E2EConfig()
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.deck_name = "hacked"  # type: ignore[misc]


def test_config_explicit_runs_root_preserved():
    from pathlib import Path

    cfg = E2EConfig(runs_root=Path("/custom/runs"))
    assert cfg.runs_root == Path("/custom/runs")


# --- ensure_test_model --------------------------------------------------------


def test_ensure_test_model_creates_when_absent():
    """When the model is not in modelNames, createModel must be issued."""
    gw = _gateway()

    def fake(url, action, params=None, timeout=30):
        if action == "modelNames":
            return ["Lapis", "Senren"]
        return None  # createModel

    with patch(GATEWAY_PA, side_effect=fake) as pa:
        gw.ensure_test_model()

    actions = [c.args[1] for c in pa.call_args_list]
    assert "modelNames" in actions
    assert "createModel" in actions

    create = next(c for c in pa.call_args_list if c.args[1] == "createModel")
    params = create.args[2]
    assert params["modelName"] == gw.config.note_type
    assert params["inOrderFields"] == ["Front", "Back"]
    assert len(params["cardTemplates"]) == 1
    assert params["cardTemplates"][0]["Front"] == "{{Front}}"
    assert params["cardTemplates"][0]["Back"] == "{{FrontSide}}<hr id=answer>{{Back}}"


def test_ensure_test_model_skips_when_present():
    """When the model is already in modelNames, createModel must NOT be issued."""
    gw = _gateway()

    with patch(GATEWAY_PA, return_value=[gw.config.note_type, "Lapis"]) as pa:
        gw.ensure_test_model()

    actions = [c.args[1] for c in pa.call_args_list]
    assert "modelNames" in actions
    assert "createModel" not in actions
