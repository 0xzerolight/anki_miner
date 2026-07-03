"""Unit tests for anki_note_builder.build_note optional-field wiring.

Covers the Phase-3 opt-in card fields: cloze split fields (3.1) and the
conjugation-chain provenance field (3.2). All default-off via unmapped
anki_fields keys, so the default wire stays byte-identical.
"""

from __future__ import annotations

from anki_miner.config import AnkiMinerConfig
from anki_miner.models import CardPayload, MediaData, TokenizedWord
from anki_miner.services.anki_note_builder import (
    _get_root_deck_name,
    build_cloze_fields,
    build_duplicate_scope_options,
    build_note,
)


def _word(**overrides) -> TokenizedWord:
    """A verb TokenizedWord with target offsets carried, for cloze tests."""
    defaults = {
        "surface": "帰っ",
        "lemma": "帰る",
        "reading": "カエッ",
        "sentence": "家に帰った。",
        "start_time": 1.0,
        "end_time": 3.0,
        "duration": 2.0,
        "orth_base": "帰る",
        "expression_furigana": "帰[かえ]る",
        "expression_reading": "かえる",
        "sentence_furigana": "",
        "sentence_reading": "",
        "pos": "動詞",
        # 家に = 2 chars, target 帰っ starts at index 2; full inflected 帰った
        # spans [2, 5) via highlight_end.
        "surface_start": 2,
        "surface_end": 4,
        "highlight_end": 5,
    }
    defaults.update(overrides)
    return TokenizedWord(**defaults)


def _payload(word: TokenizedWord, extra_fields=None) -> CardPayload:
    return CardPayload(
        word=word,
        media=MediaData(),
        definition="to return home",
        extra_fields=extra_fields,
    )


def _config(**field_overrides) -> AnkiMinerConfig:
    """Default config with the given anki_fields keys mapped to real names."""
    fields = dict(AnkiMinerConfig().anki_fields)
    fields.update(field_overrides)
    return AnkiMinerConfig(anki_fields=fields, manage_card_styling=False)


class TestClozeFieldsBuilder:
    def test_slices_prefix_body_suffix_by_offset(self):
        out = build_cloze_fields(_word())
        assert out["cloze_prefix"] == "家に"
        assert out["cloze_body"] == "帰った"
        assert out["cloze_suffix"] == "。"

    def test_body_kana_for_inflected_verb(self):
        # 帰った → かえった (inflected-body reading via distributeFuriganaInflected).
        assert build_cloze_fields(_word())["cloze_body_kana"] == "かえった"

    def test_uses_offsets_not_string_search_on_repeated_word(self):
        # 帰る appears twice; the SECOND occurrence is the mined target. Offsets,
        # not str.find, must pick it so prefix carries the first occurrence.
        word = _word(
            sentence="帰って、また帰った。",
            surface_start=6,  # 帰って、また = 6 chars
            surface_end=8,
            highlight_end=9,
        )
        out = build_cloze_fields(word)
        assert out["cloze_prefix"] == "帰って、また"
        assert out["cloze_body"] == "帰った"
        assert out["cloze_suffix"] == "。"

    def test_html_escaped(self):
        word = _word(sentence="<b>家</b>に帰った。", surface_start=9, surface_end=11, highlight_end=12)
        out = build_cloze_fields(word)
        assert "&lt;b&gt;" in out["cloze_prefix"]
        assert "<" not in out["cloze_prefix"]

    def test_untracked_offset_falls_back_to_empty(self):
        out = build_cloze_fields(_word(surface_start=-1, surface_end=-1, highlight_end=-1))
        assert out == {
            "cloze_prefix": "",
            "cloze_body": "",
            "cloze_body_kana": "",
            "cloze_suffix": "",
        }


class TestClozeFieldsInNote:
    def test_unmapped_omits_cloze_fields(self):
        note = build_note(_payload(_word()), AnkiMinerConfig(manage_card_styling=False), set()).note
        for key in ("Cloze", "ClozePrefix", "ClozeBody", "ClozeBodyKana", "ClozeSuffix"):
            assert key not in note["fields"]

    def test_mapped_populates_cloze_fields(self):
        config = _config(
            cloze_prefix="ClozePrefix",
            cloze_body="ClozeBody",
            cloze_body_kana="ClozeBodyKana",
            cloze_suffix="ClozeSuffix",
        )
        fields = build_note(_payload(_word()), config, set()).note["fields"]
        assert fields["ClozePrefix"] == "家に"
        assert fields["ClozeBody"] == "帰った"
        assert fields["ClozeBodyKana"] == "かえった"
        assert fields["ClozeSuffix"] == "。"

    def test_empty_prefix_still_written_when_mapped(self):
        # Target at sentence start → empty prefix, but the mapped field is still
        # written (not dropped like frequency), so a cloze template stays valid.
        word = _word(sentence="帰った。", surface_start=0, surface_end=2, highlight_end=3)
        config = _config(cloze_prefix="ClozePrefix", cloze_body="ClozeBody")
        fields = build_note(_payload(word), config, set()).note["fields"]
        assert fields["ClozePrefix"] == ""
        assert fields["ClozeBody"] == "帰った"

    def test_default_config_wire_unchanged(self):
        # Default config maps no cloze fields → byte-identical to a config whose
        # anki_fields never contained the cloze keys at all.
        word = _word()
        default_note = build_note(_payload(word), AnkiMinerConfig(manage_card_styling=False), set()).note
        legacy_fields = {
            k: v
            for k, v in AnkiMinerConfig().anki_fields.items()
            if k not in ("cloze_prefix", "cloze_body", "cloze_body_kana", "cloze_suffix")
        }
        legacy_note = build_note(
            _payload(word),
            AnkiMinerConfig(anki_fields=legacy_fields, manage_card_styling=False),
            set(),
        ).note
        assert default_note["fields"] == legacy_note["fields"]


class TestConjugationField:
    def test_unmapped_omits_field(self):
        word = _word(inflection_chain=("-ます", "negative", "-た"))
        note = build_note(
            _payload(word, extra_fields={"conjugation": "-ます « negative « -た"}),
            AnkiMinerConfig(manage_card_styling=False),
            set(),
        ).note
        assert "Conjugation" not in note["fields"]

    def test_mapped_writes_joined_chain(self):
        config = _config(conjugation="Conjugation")
        word = _word()
        fields = build_note(
            _payload(word, extra_fields={"conjugation": "-ます « negative « -た"}),
            config,
            set(),
        ).note["fields"]
        assert fields["Conjugation"] == "-ます « negative « -た"

    def test_mapped_but_empty_chain_omits_field(self):
        # extra_fields carries no "conjugation" key when the chain is empty
        # (episode_processor gates on word.inflection_chain), so the mapped
        # field is simply not written.
        config = _config(conjugation="Conjugation")
        fields = build_note(_payload(_word()), config, set()).note["fields"]
        assert "Conjugation" not in fields


class TestGetRootDeckName:
    def test_unnested_returns_self(self):
        assert _get_root_deck_name("Mining") == "Mining"

    def test_nested_returns_root(self):
        assert _get_root_deck_name("Mining::Anime::ShowA") == "Mining"

    def test_single_level_nesting(self):
        assert _get_root_deck_name("Mining::ShowA") == "Mining"

    def test_empty_string(self):
        assert _get_root_deck_name("") == ""


class TestDuplicateScopeOptions:
    def test_default_config_omits_options_key(self):
        # WIRE-FORMAT REGRESSION (omit-at-default): default config (collection
        # scope, check_all_models off) emits NO options key on the note dict.
        note = build_note(_payload(_word()), AnkiMinerConfig(manage_card_styling=False), set()).note
        assert "options" not in note

    def test_deck_builder_object_unchanged(self):
        # allow_duplicate_cards takes precedence and keeps the pre-7.3 hardcoded
        # object byte-for-byte.
        config = AnkiMinerConfig(manage_card_styling=False, allow_duplicate_cards=True)
        note = build_note(_payload(_word()), config, set()).note
        assert note["options"] == {"allowDuplicate": True, "duplicateScope": "deck"}

    def test_deck_builder_precedence_over_duplicate_scope(self):
        # Both allow_duplicate_cards and a non-default duplicate_scope set:
        # allow_duplicate_cards wins (hardcoded Deck Builder object).
        config = AnkiMinerConfig(
            manage_card_styling=False,
            allow_duplicate_cards=True,
            duplicate_scope="deck-root",
            duplicate_check_all_models=True,
        )
        note = build_note(_payload(_word()), config, set()).note
        assert note["options"] == {"allowDuplicate": True, "duplicateScope": "deck"}

    def test_scope_deck_emits_options(self):
        config = AnkiMinerConfig(manage_card_styling=False, duplicate_scope="deck")
        note = build_note(_payload(_word()), config, set()).note
        assert note["options"] == {
            "allowDuplicate": False,
            "duplicateScope": "deck",
            "duplicateScopeOptions": {
                "deckName": None,
                "checkChildren": False,
                "checkAllModels": False,
            },
        }

    def test_scope_deck_root_synthesizes_root_and_check_children(self):
        config = AnkiMinerConfig(
            manage_card_styling=False,
            anki_deck_name="Mining::Anime::ShowA",
            duplicate_scope="deck-root",
        )
        note = build_note(_payload(_word()), config, set()).note
        assert note["options"] == {
            "allowDuplicate": False,
            "duplicateScope": "deck",
            "duplicateScopeOptions": {
                "deckName": "Mining",
                "checkChildren": True,
                "checkAllModels": False,
            },
        }

    def test_check_all_models_alone_emits_collection_scope(self):
        # check_all_models on but scope still collection: an explicit off-default
        # choice, so the object is emitted with duplicateScope="collection".
        config = AnkiMinerConfig(manage_card_styling=False, duplicate_check_all_models=True)
        note = build_note(_payload(_word()), config, set()).note
        assert note["options"] == {
            "allowDuplicate": False,
            "duplicateScope": "collection",
            "duplicateScopeOptions": {
                "deckName": None,
                "checkChildren": False,
                "checkAllModels": True,
            },
        }

    def test_check_all_models_with_deck_scope(self):
        config = AnkiMinerConfig(
            manage_card_styling=False,
            duplicate_scope="deck",
            duplicate_check_all_models=True,
        )
        opts = build_duplicate_scope_options(config)
        assert opts["duplicateScope"] == "deck"
        assert opts["duplicateScopeOptions"]["checkAllModels"] is True

    def test_deck_root_at_root_deck_uses_full_name(self):
        # Un-nested deck name: deck-root resolves to the deck itself.
        config = AnkiMinerConfig(
            manage_card_styling=False,
            anki_deck_name="Mining",
            duplicate_scope="deck-root",
        )
        opts = build_duplicate_scope_options(config)
        assert opts["duplicateScopeOptions"]["deckName"] == "Mining"
        assert opts["duplicateScopeOptions"]["checkChildren"] is True
