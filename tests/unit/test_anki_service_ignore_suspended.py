"""Ignore suspended cards: all-suspended notes drop out of the known-words scan, as an excluded deck does."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import patch

from anki_miner.models import CardPayload, MediaData
from anki_miner.services.anki_service import AnkiService


def test_off_by_default_the_scan_keeps_suspended_cards(test_config):
    assert AnkiService(test_config)._build_vocab_query() == "deck:*"


def test_on_the_scan_drops_suspended_cards(test_config):
    service = AnkiService(replace(test_config, ignore_suspended_cards=True))

    assert service._build_vocab_query() == "deck:* -is:suspended"


def test_on_it_follows_the_deck_exclusions(test_config):
    service = AnkiService(replace(test_config, excluded_decks=("Core 2k",), ignore_suspended_cards=True))

    assert service._build_vocab_query() == 'deck:* -deck:"Core 2k" -is:suspended'


def test_the_reviewed_scan_drops_them_too(test_config):
    service = AnkiService(replace(test_config, ignore_suspended_cards=True))

    assert service._build_reviewed_query() == "deck:* -is:suspended -is:new"


def test_a_word_whose_only_card_is_suspended_is_carded_again(test_config, make_tokenized_word):
    """Anki would refuse 猫 as a collection duplicate; the scan says it is not known, so it is admitted."""
    service = AnkiService(replace(test_config, ignore_suspended_cards=True))
    service._existing_vocab_cache = {service._dedup_key("犬")}
    cat = make_tokenized_word(surface="猫", lemma="猫", sentence="猫だ。", pos="名詞")
    dog = make_tokenized_word(surface="犬", lemma="犬", sentence="犬だ。", pos="名詞")
    items = [
        CardPayload(word=cat, media=MediaData(), definition="cat"),
        CardPayload(word=dog, media=MediaData(), definition="dog"),
    ]

    with (
        patch.object(service, "_store_media_files_batch", return_value=set()),
        patch.object(service, "_upload_dict_media_batch"),
        patch(
            "anki_miner.services.anki_service.post_action",
            side_effect=[[{"canAdd": True, "error": None}], [10]],
        ) as post,
    ):
        assert service.create_cards_batch(items) == [10]

    assert [call.args[1] for call in post.call_args_list] == ["canAddNotesWithErrorDetail", "addNotes"]
    submitted = post.call_args_list[1].kwargs["params"]["notes"]
    assert [note["fields"][test_config.anki_fields["word"]] for note in submitted] == ["猫"]
    assert submitted[0]["options"] == {"allowDuplicate": True}
    assert service.last_not_created == {"犬": "duplicate"}


def test_the_dry_run_duplicate_check_takes_the_same_branch(test_config, monkeypatch, make_tokenized_word):
    service = AnkiService(replace(test_config, ignore_suspended_cards=True))
    service._existing_vocab_cache = {service._dedup_key("犬")}

    def no_probe(notes):
        raise AssertionError("probed although the scan is narrowed")

    monkeypatch.setattr(service, "_probe_duplicates", no_probe)
    items = [
        CardPayload(word=make_tokenized_word(surface=front, lemma=front, pos="名詞"), media=MediaData(), definition="")
        for front in ("猫", "犬")
    ]

    assert service.duplicate_fronts(items) == {"犬"}


def test_allowed_duplicates_skip_the_admission(test_config):
    service = AnkiService(replace(test_config, ignore_suspended_cards=True, allow_duplicate_cards=True))

    assert not service._scan_is_narrowed()
