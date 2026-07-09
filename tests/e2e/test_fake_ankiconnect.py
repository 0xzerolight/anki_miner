"""Round-trip tests for the FakeAnkiConnect server.

Exercises the fake through the real client helper (``post_action``) so the
envelope contract — ``{"result": ..., "error": ...}``, error propagation as
``AnkiConnectionError`` — is verified end to end, not just the dispatch table.

All tests are ``network``-marked: the fake is a real loopback HTTP server and
the suite's socket tripwire blocks unmarked TCP connects (loopback included).
"""

import pytest

from anki_miner.exceptions import AnkiConnectionError
from anki_miner.services._ankiconnect import post_action, post_multi
from anki_miner.services.anki_service import _DUPLICATE_ERROR_SUBSTRING

pytestmark = pytest.mark.network


def _note(model: str, front: str, deck: str = "TestDeck", **options) -> dict:
    note = {
        "deckName": deck,
        "modelName": model,
        "fields": {"Front": front, "Back": "back"},
        "tags": ["e2e"],
    }
    if options:
        note["options"] = options
    return note


class TestLifecycleAndBasics:
    def test_version_round_trip(self, fake_anki):
        assert post_action(fake_anki.url, "version") == 6

    def test_unsupported_action_raises_via_post_action(self, fake_anki):
        with pytest.raises(AnkiConnectionError, match="unsupported action"):
            post_action(fake_anki.url, "guiBrowse")

    def test_create_deck_and_deck_names(self, fake_anki):
        post_action(fake_anki.url, "createDeck", {"deck": "TestDeck"})
        assert "TestDeck" in post_action(fake_anki.url, "deckNames")

    def test_fixture_preseeds_e2e_model(self, fake_anki):
        from tests.e2e.config import E2EConfig

        note_type = E2EConfig().note_type
        assert note_type in post_action(fake_anki.url, "modelNames")
        assert post_action(fake_anki.url, "modelFieldNames", {"modelName": note_type}) == ["Front", "Back"]

    def test_model_field_names_unknown_model_errors(self, fake_anki):
        with pytest.raises(AnkiConnectionError, match="model was not found"):
            post_action(fake_anki.url, "modelFieldNames", {"modelName": "NoSuchModel"})

    def test_create_model_registers_fields(self, fake_anki):
        post_action(
            fake_anki.url,
            "createModel",
            {"modelName": "Custom", "inOrderFields": ["A", "B", "C"]},
        )
        assert post_action(fake_anki.url, "modelFieldNames", {"modelName": "Custom"}) == ["A", "B", "C"]


class TestNotes:
    def test_add_find_info_round_trip(self, fake_anki):
        post_action(fake_anki.url, "createDeck", {"deck": "TestDeck"})
        post_action(fake_anki.url, "createModel", {"modelName": "M", "inOrderFields": ["Front", "Back"]})
        ids = post_action(fake_anki.url, "addNotes", {"notes": [_note("M", "犬"), _note("M", "猫")]})
        assert len(ids) == 2 and all(isinstance(i, int) for i in ids)

        found = post_action(fake_anki.url, "findNotes", {"query": 'deck:"TestDeck"'})
        assert sorted(found) == sorted(ids)

        infos = post_action(fake_anki.url, "notesInfo", {"notes": ids})
        assert {i["fields"]["Front"]["value"] for i in infos} == {"犬", "猫"}
        assert all(i["modelName"] == "M" for i in infos)

    def test_find_notes_whole_collection_vocab_query(self, fake_anki):
        """The ``deck:*`` query from AnkiService._build_vocab_query sees everything."""
        post_action(fake_anki.url, "createModel", {"modelName": "M", "inOrderFields": ["Front"]})
        ids = post_action(
            fake_anki.url,
            "addNotes",
            {"notes": [_note("M", "犬", deck="A"), _note("M", "猫", deck="B")]},
        )
        found = post_action(fake_anki.url, "findNotes", {"query": 'deck:* -deck:"Excluded"'})
        assert sorted(found) == sorted(ids)

    def test_notes_info_deleted_note_is_empty_dict(self, fake_anki):
        assert post_action(fake_anki.url, "notesInfo", {"notes": [999999]}) == [{}]

    def test_delete_notes(self, fake_anki):
        post_action(fake_anki.url, "createModel", {"modelName": "M", "inOrderFields": ["Front"]})
        ids = post_action(fake_anki.url, "addNotes", {"notes": [_note("M", "犬")]})
        post_action(fake_anki.url, "deleteNotes", {"notes": ids})
        assert post_action(fake_anki.url, "findNotes", {"query": 'deck:"TestDeck"'}) == []

    def test_delete_decks_cards_too(self, fake_anki):
        post_action(fake_anki.url, "createDeck", {"deck": "TestDeck"})
        post_action(fake_anki.url, "createModel", {"modelName": "M", "inOrderFields": ["Front"]})
        post_action(fake_anki.url, "addNotes", {"notes": [_note("M", "犬")]})
        post_action(fake_anki.url, "deleteDecks", {"decks": ["TestDeck"], "cardsToo": True})
        assert "TestDeck" not in post_action(fake_anki.url, "deckNames")
        assert post_action(fake_anki.url, "findNotes", {"query": "deck:*"}) == []


class TestDuplicates:
    def test_duplicate_detected_with_exact_error_literal(self, fake_anki):
        post_action(fake_anki.url, "createModel", {"modelName": "M", "inOrderFields": ["Front", "Back"]})
        post_action(fake_anki.url, "addNotes", {"notes": [_note("M", "犬")]})

        detail = post_action(
            fake_anki.url,
            "canAddNotesWithErrorDetail",
            {"notes": [_note("M", "犬", allowDuplicate=False), _note("M", "猫", allowDuplicate=False)]},
        )
        assert detail[0]["canAdd"] is False
        assert _DUPLICATE_ERROR_SUBSTRING in detail[0]["error"]
        assert detail[1] == {"canAdd": True, "error": None}

    def test_can_add_notes_honors_allow_duplicate(self, fake_anki):
        post_action(fake_anki.url, "createModel", {"modelName": "M", "inOrderFields": ["Front"]})
        post_action(fake_anki.url, "addNotes", {"notes": [_note("M", "犬")]})
        assert post_action(fake_anki.url, "canAddNotes", {"notes": [_note("M", "犬", allowDuplicate=True)]}) == [True]
        assert post_action(fake_anki.url, "canAddNotes", {"notes": [_note("M", "犬")]}) == [False]

    def test_add_notes_null_slot_for_duplicate(self, fake_anki):
        post_action(fake_anki.url, "createModel", {"modelName": "M", "inOrderFields": ["Front"]})
        post_action(fake_anki.url, "addNotes", {"notes": [_note("M", "犬")]})
        ids = post_action(fake_anki.url, "addNotes", {"notes": [_note("M", "犬"), _note("M", "猫")]})
        assert ids[0] is None
        assert isinstance(ids[1], int)

    def test_duplicate_is_per_model(self, fake_anki):
        post_action(fake_anki.url, "createModel", {"modelName": "M1", "inOrderFields": ["Front"]})
        post_action(fake_anki.url, "createModel", {"modelName": "M2", "inOrderFields": ["Front"]})
        post_action(fake_anki.url, "addNotes", {"notes": [_note("M1", "犬")]})
        ids = post_action(fake_anki.url, "addNotes", {"notes": [_note("M2", "犬")]})
        assert isinstance(ids[0], int)


class TestMediaAndMulti:
    def test_store_media_file_returns_filename(self, fake_anki):
        assert post_action(fake_anki.url, "storeMediaFile", {"filename": "a.jpg", "data": "aGk="}) == "a.jpg"

    def test_multi_wraps_per_action_results(self, fake_anki):
        results = post_multi(
            fake_anki.url,
            [
                {"action": "storeMediaFile", "version": 6, "params": {"filename": "b.mp3", "data": "aGk="}},
                {"action": "guiBrowse", "version": 6, "params": {}},
            ],
        )
        assert results[0] == {"result": "b.mp3", "error": None}
        assert results[1]["result"] is None
        assert "unsupported action" in results[1]["error"]


class TestGatewayCompatibility:
    def test_real_gateway_full_cycle_against_fake(self, fake_anki, tmp_path):
        """AnkiGateway (the real harness gateway) runs its whole lifecycle."""
        from tests.e2e.anki_gateway import AnkiGateway
        from tests.e2e.config import E2EConfig

        e2e = E2EConfig(test_home=tmp_path, ankiconnect_url=fake_anki.url)
        gateway = AnkiGateway(e2e)
        assert gateway.ping() == "6"
        gateway.ensure_test_deck()
        gateway.ensure_test_model()
        assert gateway.deck_card_count() == 0

        ids = post_action(
            fake_anki.url,
            "addNotes",
            {"notes": [_note(e2e.note_type, "犬", deck=e2e.deck_name)]},
        )
        assert gateway.deck_card_count() == 1
        assert gateway.notes_info(ids)[0]["fields"]["Front"]["value"] == "犬"

        assert gateway.delete_test_deck_notes() == 1
        gateway.delete_test_deck()
        assert e2e.deck_name not in post_action(fake_anki.url, "deckNames")
