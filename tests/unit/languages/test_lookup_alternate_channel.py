"""The mined-form policy may hand the lookup ladder the token surface (en plan D7a); ja/ko/zh are unchanged.

Both lookup-miss sites read one helper: the phase-2 offline probe (the
strategy's ``orth_base``) and the phase-5 ``fallback_context`` handed to
``get_definitions_batch``.
"""

from __future__ import annotations

import dataclasses
from unittest.mock import MagicMock

import pytest

from anki_miner.languages._spaced.morphology import SpacedMinedForm
from anki_miner.languages.registry import get_profile
from anki_miner.models import TokenizedWord
from tests.conftest import build_processor
from tests.unit.languages.test_phase2_probe_dispatch import _profile_with, _run_phase2, _StubLookup


@pytest.fixture
def services():
    """The phase-2 dispatch file's service shape (not imported: a re-imported fixture trips F811)."""
    definition_service = MagicMock()
    definition_service.has_offline_definitions.side_effect = lambda terms: dict.fromkeys(terms, False)
    definition_service.offline_deinflection_terms_exist.return_value = set()
    word_filter = MagicMock()
    word_filter.deduplicate_by_sentence.side_effect = lambda words: words
    anki_service = MagicMock()
    anki_service.get_existing_vocabulary.return_value = set()
    return {
        "subtitle_parser": MagicMock(),
        "word_filter": word_filter,
        "media_extractor": MagicMock(),
        "definition_service": definition_service,
        "anki_service": anki_service,
    }


def _word(surface: str, lemma: str, orth_base: str, pos: str, *, front: str = "") -> TokenizedWord:
    word = TokenizedWord(
        surface=surface,
        lemma=lemma,
        reading="",
        sentence=f"{surface}.",
        start_time=0.0,
        end_time=1.0,
        duration=1.0,
        pos=pos,
    )
    word.orth_base = orth_base
    if front:
        word.mined_form_override = front  # what the parser stores for a policy front
    return word


def _latin_word() -> TokenizedWord:
    return _word("Dámelo", "dámelir", "dámelir", "VERB", front="dámelir")


def _okurigana_word() -> TokenizedWord:
    return _word("表せ", "表わす", "表せる", "動詞")


def _kanji_swap_word() -> TokenizedWord:
    return _word("帰れ", "返る", "帰れる", "動詞")


@pytest.mark.parametrize("code", ["ja", "ko", "zh"])
def test_no_shipped_policy_defines_the_channel(code):
    assert getattr(get_profile(code).mined_form, "lookup_alternate", None) is None


def test_the_policy_surface_reaches_the_phase2_strategy(test_config, services, tmp_path):
    lookup = _StubLookup()
    profile = dataclasses.replace(_profile_with(lookup), mined_form=SpacedMinedForm())
    _run_phase2(test_config, services, [_latin_word()], profile=profile, tmp_path=tmp_path)

    assert lookup.calls == [("dámelir", "Dámelo", None)]


class _StopAfterDefinitions(Exception):
    """Raised by the recording service: phase 5 needs nothing after the call."""


def _phase5_context(test_config, services, words, profile=None) -> dict[str, tuple[str, str | None]]:
    recorded: dict[str, tuple[str, str | None]] = {}

    def get_definitions_batch(pairs, progress_callback=None, fallback_context=None, **_kwargs):
        recorded.update(fallback_context or {})
        raise _StopAfterDefinitions

    services["definition_service"].get_definitions_batch = get_definitions_batch
    kwargs = dict(services)
    if profile is not None:
        kwargs["profile"] = profile
    proc = build_processor(test_config, **kwargs)
    with pytest.raises(_StopAfterDefinitions):
        proc._phase4_lookup(MagicMock(), [(word, MagicMock()) for word in words], None)
    return recorded


def test_the_policy_surface_reaches_the_phase5_fallback_context(test_config, services):
    profile = dataclasses.replace(get_profile("ja"), mined_form=SpacedMinedForm())
    assert _phase5_context(test_config, services, [_latin_word()], profile) == {"dámelir": ("Dámelo", None)}


def test_the_ja_fallback_context_is_unchanged(test_config, services):
    assert _phase5_context(test_config, services, [_okurigana_word()]) == {"表せる": ("表わす", None)}
    assert _phase5_context(test_config, services, [_kanji_swap_word()]) == {"帰れる": ("", None)}


def test_the_helper_is_the_single_source_for_both_sites(test_config, services):
    spaced = build_processor(
        test_config, **services, profile=dataclasses.replace(get_profile("ja"), mined_form=SpacedMinedForm())
    )
    ja = build_processor(test_config, **services)

    assert spaced._lookup_alternate(_latin_word()) == "Dámelo"
    assert ja._lookup_alternate(_latin_word()) == "dámelir"  # the old expression: the lemma equals the front
    assert ja._lookup_alternate(_okurigana_word()) == "表わす"
    assert ja._lookup_alternate(_kanji_swap_word()) == ""
