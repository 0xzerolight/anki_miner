"""R36: the ``form_lookup`` builder beside ``term_lookup`` in ``gui/utils/service_factory.py``.

Gated on the same indexed-dictionary probe as its five siblings, and built for every language --
the builder is deliberately language-blind, which is why the default-identity proof lives on the
post-passes (``tests/unit/test_subtitle_parser_form_lookup.py``) rather than here.
"""

from __future__ import annotations

import dataclasses

import pytest

from anki_miner.config.config import AnkiMinerConfig, ChainEntry
from anki_miner.gui.utils import service_factory
from anki_miner.languages.switching import switch_language
from anki_miner.services.subtitle_parser import SubtitleParserService


@pytest.fixture
def indexed(test_config) -> AnkiMinerConfig:
    return dataclasses.replace(
        test_config,
        dictionary_chain=(ChainEntry(kind="indexed", dict_id="jmdict-english", enabled=True),),
    )


@pytest.fixture
def jisho_only(test_config) -> AnkiMinerConfig:
    return dataclasses.replace(
        test_config,
        dictionary_chain=(ChainEntry(kind="jisho", dict_id=None, enabled=True),),
    )


def test_the_builder_is_the_definition_services_row_probe(indexed):
    services = service_factory.create_services(indexed)
    assert services.subtitle_parser._form_lookup == services.definition_service.offline_term_rows


def test_no_indexed_dictionary_means_no_lookup(jisho_only):
    services = service_factory.create_services(jisho_only)
    assert services.subtitle_parser._form_lookup is None


def test_a_disabled_indexed_entry_means_no_lookup(test_config):
    cfg = dataclasses.replace(
        test_config,
        dictionary_chain=(ChainEntry(kind="indexed", dict_id="jmdict-english", enabled=False),),
    )
    assert service_factory.create_services(cfg).subtitle_parser._form_lookup is None


def test_a_prebuilt_parser_keeps_the_callers_choice(indexed):
    prebuilt = SubtitleParserService(indexed)
    services = service_factory.create_services(indexed, subtitle_parser=prebuilt)
    assert services.subtitle_parser is prebuilt
    assert prebuilt._form_lookup is None


@pytest.mark.parametrize("code", ["ja", "ko", "zh"])
def test_the_three_shipped_languages_build_the_same_parser_type(code, indexed):
    """Type identity: the factories inject policies, they do not subclass."""
    cfg = indexed if code == "ja" else switch_language(indexed, code)
    services = service_factory.create_services(cfg)
    assert type(services.subtitle_parser) is SubtitleParserService
    # The lookup is wired for them too -- and unreachable, because they inject
    # no post-pass. That pairing IS the default-identity argument.
    assert services.subtitle_parser._token_post_pass is None


@pytest.mark.parametrize("code", ["ja", "ko", "zh"])
def test_a_full_parse_never_reaches_the_lookup_for_a_shipped_language(code, indexed):
    """The end-to-end half: a real parse of that language's own smoke line calls it zero times."""
    from anki_miner.languages.registry import get_profile

    cfg = indexed if code == "ja" else switch_language(indexed, code)
    services = service_factory.create_services(cfg)
    calls: list[list[str]] = []

    def spy(terms):
        calls.append(list(terms))
        return {}

    services.subtitle_parser._form_lookup = spy
    line = get_profile(code).smoke_sentence or "テスト"
    services.subtitle_parser._build_line_state(line, 0.0, 1.0)
    assert calls == []
