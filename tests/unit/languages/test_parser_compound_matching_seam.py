"""S7: a profile can keep the dictionary compound matcher off its parser."""

from __future__ import annotations

import inspect

from anki_miner.languages.registry import get_profile
from anki_miner.services.subtitle_parser import SubtitleParserService


def _attest_nothing(surfaces: list[str]) -> set[str]:
    return set()


def test_matcher_is_built_by_default_when_a_dictionary_is_wired(test_config):
    parser = SubtitleParserService(test_config, term_lookup=_attest_nothing)
    assert parser._compound_matcher is not None


def test_compound_matching_false_builds_no_matcher(test_config):
    parser = SubtitleParserService(test_config, term_lookup=_attest_nothing, compound_matching=False)
    assert parser._compound_matcher is None
    # The merge gate keeps its probe: only the matcher is profile-controlled.
    assert parser._attest is not None


def test_non_ja_stub_factory_forwards_the_keyword(make_eu_parser):
    parser = make_eu_parser(term_lookup=_attest_nothing, compound_matching=False)
    assert parser._compound_matcher is None


def test_zh_closes_it_and_ko_leaves_the_default_alone():
    """zh: every merge it could propose dies on the synthetic's UniDic POS stamp.

    ko keeps it: its own ``token_merger`` runs beside the matcher, not instead
    of it. The zh half is proved on output in ``test_zh_parser_seams``.
    """
    assert "compound_matching" in inspect.getsource(get_profile("zh").create_parser)
    assert "compound_matching" not in inspect.getsource(get_profile("ko").create_parser)
