"""Curator sentence edits: the intent model and the one resolver that reads it."""

from __future__ import annotations

import dataclasses

import pytest

from anki_miner.models import SentenceEdit, TokenizedWord


def _word(**kwargs) -> TokenizedWord:
    base = {
        "surface": "時給",
        "lemma": "時給",
        "reading": "ジキュウ",
        "sentence": "時給系のスポーツは本当に苦手",
        "start_time": 5.0,
        "end_time": 7.0,
        "duration": 2.0,
        "pos": "名詞",
        "surface_start": 0,
        "surface_end": 2,
        "mined_form_override": "時給",
    }
    base.update(kwargs)
    return TokenizedWord(**base)


class TestModel:
    def test_default_is_untouched(self):
        assert _word().sentence_edit is None

    def test_edit_is_frozen(self):
        edit = SentenceEdit(text="持久系のスポーツは本当に苦手", target_start=0, target_end=2)
        with pytest.raises(dataclasses.FrozenInstanceError):
            edit.text = "x"  # type: ignore[misc]

    def test_replace_carries_the_edit(self):
        edit = SentenceEdit(text="持久系のスポーツは本当に苦手", target_start=0, target_end=2)
        assert dataclasses.replace(_word(), sentence_edit=edit).sentence_edit == edit
