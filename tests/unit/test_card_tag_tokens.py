"""F-03: {series}, {episode} and {volume} in the card tags render per source."""

from __future__ import annotations

from dataclasses import replace

from anki_miner.models import CardPayload, MediaData
from anki_miner.services.anki_note_builder import CARD_TAGS_KEY, build_note, has_tag_tokens, render_card_tags

_FRIEREN = {"series": "Frieren", "episode": "03", "volume": ""}


def test_tokens_render_from_the_source():
    assert render_card_tags("mined am::{series}::{episode}", _FRIEREN) == ["mined", "am::Frieren::03"]


def test_spaces_become_underscores_and_characters_anki_rejects_go():
    values = {"series": 'Sousou no "Frieren"\tS1', "episode": "", "volume": ""}

    assert render_card_tags("{series}", values) == ["Sousou_no_Frieren_S1"]


def test_an_empty_token_drops_its_component_and_an_empty_tag():
    values = {"series": "Frieren", "episode": "", "volume": ""}

    assert render_card_tags("{volume} am::{series}::{episode}", values) == ["am::Frieren"]


def test_a_token_in_the_middle_leaves_no_double_separator():
    values = {"series": "", "episode": "03", "volume": ""}

    assert render_card_tags("am::{series}::{episode}", values) == ["am::03"]


def test_unknown_tokens_stay_verbatim():
    assert render_card_tags("{season} x::{series}", _FRIEREN) == ["{season}", "x::Frieren"]


def test_a_string_without_tokens_splits_exactly_as_before():
    assert render_card_tags("auto-mined  extra auto-mined", {}) == ["auto-mined", "extra", "auto-mined"]


def test_has_tag_tokens():
    assert has_tag_tokens("mined {series}")
    assert has_tag_tokens("{volume}")
    assert not has_tag_tokens("{season} plain")
    assert not has_tag_tokens("")


def _payload(make_tokenized_word, extra=None) -> CardPayload:
    return CardPayload(word=make_tokenized_word(), media=MediaData(), definition="to eat", extra_fields=extra)


def test_build_note_takes_the_rendered_tags_from_the_payload(test_config, make_tokenized_word):
    config = replace(test_config, anki_tags="am::{series}::{episode} mined")
    payload = _payload(make_tokenized_word, {CARD_TAGS_KEY: "am::Frieren::03 mined"})

    note = build_note(payload, config, set()).note

    assert note["tags"] == ["am::Frieren::03", "mined"]
    assert "am::Frieren::03" not in "".join(note["fields"].values())


def test_build_note_without_payload_tags_drops_unfilled_tokens(test_config, make_tokenized_word):
    note = build_note(_payload(make_tokenized_word), replace(test_config, anki_tags="mined {series}"), set()).note

    assert note["tags"] == ["mined"]


def test_build_note_legacy_tags_unchanged(test_config, make_tokenized_word):
    note = build_note(_payload(make_tokenized_word), replace(test_config, anki_tags="auto-mined"), set()).note

    assert note["tags"] == ["auto-mined"]
