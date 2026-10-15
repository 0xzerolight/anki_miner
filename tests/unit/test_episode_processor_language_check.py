"""MS-28: the Activity Log warns when a run's first words mostly lack a dictionary entry."""

from __future__ import annotations

import logging
from dataclasses import replace
from unittest.mock import MagicMock

from anki_miner.languages.switching import switch_language
from anki_miner.models import TokenizedWord
from tests.conftest import build_processor

_LOGGER = "anki_miner.orchestration.episode_processor"


def _message(percent: int, sampled: int) -> str:
    return (
        f"Only {percent}% of the first {sampled} words have an entry in your dictionaries. "
        "Is this file in the language you are mining?"
    )


def _words(count: int, *, language: str = "es") -> list[TokenizedWord]:
    """``count`` distinct nouns in first-appearance order; outside ja the front is pinned to the lemma."""
    prefix = "語" if language == "ja" else "palabra"
    return [
        TokenizedWord(
            surface=f"{prefix}{i}",
            lemma=f"{prefix}{i}",
            reading="",
            sentence=f"{prefix}{i}。",
            start_time=float(i),
            end_time=float(i) + 1.0,
            duration=1.0,
            pos="名詞" if language == "ja" else "NOUN",
            mined_form_override="" if language == "ja" else f"{prefix}{i}",
        )
        for i in range(count)
    ]


def _processor(config, defined: set[str], *, provider: bool = True):
    """A processor whose offline dictionaries define exactly ``defined``."""
    definitions = MagicMock(name="definition_service")
    definitions.has_usable_offline_provider.return_value = provider
    definitions.has_offline_definitions.side_effect = lambda terms: {term: term in defined for term in terms}
    definitions.offline_deinflection_terms_exist.return_value = set()
    presenter = MagicMock(name="presenter")
    processor = build_processor(config, presenter=presenter, definition_service=definitions)
    return processor, presenter, definitions


def _warnings(presenter: MagicMock) -> list[str]:
    return [call.args[0] for call in presenter.show_warning.call_args_list]


def test_es_warns_when_few_words_have_an_entry(test_config):
    """Review Focus 3: a Spanish run whose file is not Spanish."""
    words = _words(40)
    processor, presenter, _definitions = _processor(
        switch_language(test_config, "es"), {word.mined_form for word in words[:10]}
    )

    processor._warn_if_wrong_language(words)

    assert _warnings(presenter) == [_message(25, 40)]


def test_a_correct_language_run_stays_quiet_and_logs_the_count(test_config, caplog):
    words = _words(40)
    processor, presenter, _definitions = _processor(
        switch_language(test_config, "es"), {word.mined_form for word in words[:38]}
    )

    with caplog.at_level(logging.INFO, logger=_LOGGER):
        processor._warn_if_wrong_language(words)

    presenter.show_warning.assert_not_called()
    assert "Language check: sampled=40 hits=38 floor=0.5" in caplog.text


def test_exactly_half_is_not_a_warning(test_config):
    words = _words(40, language="ja")
    processor, presenter, _definitions = _processor(test_config, {word.mined_form for word in words[:20]})

    processor._warn_if_wrong_language(words)

    presenter.show_warning.assert_not_called()


def test_only_the_first_200_words_are_sampled(test_config):
    words = _words(250)
    processor, presenter, definitions = _processor(
        switch_language(test_config, "es"), {word.mined_form for word in words[:200]}
    )

    processor._warn_if_wrong_language(words)

    probed = set(definitions.has_offline_definitions.call_args.args[0])
    assert probed == {word.mined_form for word in words[:200]}
    presenter.show_warning.assert_not_called()


def test_too_few_words_are_not_judged(test_config):
    processor, presenter, definitions = _processor(switch_language(test_config, "es"), set())

    processor._warn_if_wrong_language(_words(29))

    definitions.has_offline_definitions.assert_not_called()
    presenter.show_warning.assert_not_called()


def test_no_usable_dictionary_skips_the_check(test_config):
    processor, presenter, definitions = _processor(switch_language(test_config, "es"), set(), provider=False)

    processor._warn_if_wrong_language(_words(40))

    definitions.has_offline_definitions.assert_not_called()
    presenter.show_warning.assert_not_called()


def test_a_bypass_run_that_skips_the_dictionary_gates_skips_the_check(test_config):
    config = replace(switch_language(test_config, "es"), bypass_optional_filters=True)
    processor, presenter, definitions = _processor(config, set())

    processor._warn_if_wrong_language(_words(40))

    definitions.has_offline_definitions.assert_not_called()
    presenter.show_warning.assert_not_called()


def test_deck_builder_keeps_the_check(test_config):
    """Deck Builder bypasses the optional filters but keeps the dictionary gates (R2), so it checks too."""
    config = replace(switch_language(test_config, "es"), bypass_optional_filters=True)
    processor, presenter, _definitions = _processor(config, set())
    processor.bypass_skips_dictionary_gates = False

    processor._warn_if_wrong_language(_words(40))

    assert _warnings(presenter) == [_message(0, 40)]
