"""The fetched gloss reaches the non-ja render hooks; ja never sees it."""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace
from unittest.mock import MagicMock

from anki_miner.models.word import TokenizedWord
from tests.conftest import build_processor


class _MeasureWordHook:
    def field_names(self):
        return ("measure_word",)

    def render(self, word, *, config):
        return {"measure_word": f"{word.mined_form}:{word.definition_html}"}


def test_tokenized_word_definition_html_defaults_empty():
    word = TokenizedWord(
        surface="食べる",
        lemma="食べる",
        reading="タベル",
        sentence="",
        start_time=0.0,
        end_time=1.0,
        duration=1.0,
    )
    assert word.definition_html == ""


def test_definition_html_is_stashed_for_a_zh_run(test_config, make_tokenized_word):
    config = dataclasses.replace(test_config, language="zh")
    processor = build_processor(config, profile=SimpleNamespace(code="zh", render_hooks=()))
    word = make_tokenized_word(surface="银行", lemma="银行", reading="", sentence="我去银行。")
    processor._apply_render_hooks(word, "bank; CL:家[jia1]", {})
    assert word.definition_html == "bank; CL:家[jia1]"


def test_definition_html_stays_empty_on_a_ja_run(test_config, make_tokenized_word):
    hook = MagicMock()
    processor = build_processor(test_config, profile=SimpleNamespace(code="ja", render_hooks=(hook,)))
    word = make_tokenized_word()
    processor._apply_render_hooks(word, "to eat", {})
    hook.render.assert_not_called()
    assert word.definition_html == ""


def test_a_zh_hook_reads_the_stashed_definition(test_config, make_tokenized_word):
    config = dataclasses.replace(test_config, language="zh")
    processor = build_processor(config, profile=SimpleNamespace(code="zh", render_hooks=(_MeasureWordHook(),)))
    word = make_tokenized_word(surface="银行", lemma="银行", reading="", sentence="我去银行。")
    extra: dict[str, str] = {}
    processor._apply_render_hooks(word, "bank; CL:家[jia1]", extra)
    assert extra["measure_word"] == "银行:bank; CL:家[jia1]"
