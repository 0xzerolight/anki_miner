"""zh anki_fields keys land on the note without disturbing the ja defaults."""

from __future__ import annotations

import dataclasses

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.zh.fields import ZH_CARD_FIELD_DEFAULTS
from anki_miner.models.card_payload import CardPayload
from anki_miner.models.media import MediaData
from anki_miner.services.anki_note_builder import (
    OPTIONAL_FIELD_KEYS,
    REQUIRED_FIELD_KEYS,
    build_note,
)


def test_zh_defaults_answer_every_required_key_and_add_the_zh_keys():
    assert set(ZH_CARD_FIELD_DEFAULTS) >= REQUIRED_FIELD_KEYS
    assert set(AnkiMinerConfig().anki_fields) <= set(ZH_CARD_FIELD_DEFAULTS)
    assert {"measure_word", "expression_traditional", "expression_pinyin"} <= set(ZH_CARD_FIELD_DEFAULTS)
    assert all(ZH_CARD_FIELD_DEFAULTS[k] == "" for k in ("measure_word", "expression_traditional", "expression_pinyin"))
    assert ZH_CARD_FIELD_DEFAULTS["expression_furigana"] == ""


def test_the_zh_keys_never_reach_the_ja_dataclass_default():
    """Pins the design that keeps test_note_presets.py green unedited."""
    assert "measure_word" not in AnkiMinerConfig().anki_fields
    assert "expression_pinyin" not in AnkiMinerConfig().anki_fields


def test_hook_keys_are_written_when_mapped(test_config, make_tokenized_word):
    config = dataclasses.replace(
        test_config,
        anki_fields={
            **test_config.anki_fields,
            "measure_word": "MeasureWord",
            "expression_traditional": "Traditional",
            "expression_pinyin": "Pinyin",
        },
    )
    note = build_note(
        CardPayload(
            word=make_tokenized_word(),
            media=MediaData(),
            definition="d",
            extra_fields={
                "measure_word": "个",
                "expression_traditional": "銀行",
                "expression_pinyin": '<span style="color:#e08a00">yín</span>',
            },
        ),
        config,
        set(),
    )
    fields = note.note["fields"]
    assert fields["MeasureWord"] == "个"
    assert fields["Traditional"] == "銀行"
    # Raw-HTML carve-out: the tone spans must NOT be escaped.
    assert fields["Pinyin"] == '<span style="color:#e08a00">yín</span>'


def test_unmapped_hook_keys_leave_the_ja_note_untouched(test_config, make_tokenized_word):
    note = build_note(
        CardPayload(
            word=make_tokenized_word(),
            media=MediaData(),
            definition="d",
            extra_fields={"measure_word": "个", "expression_pinyin": "<span>x</span>"},
        ),
        test_config,
        set(),
    )
    assert "measure_word" not in note.note["fields"]
    assert all("span" not in value for value in note.note["fields"].values())
    assert {"measure_word", "expression_traditional", "expression_pinyin"} <= OPTIONAL_FIELD_KEYS
