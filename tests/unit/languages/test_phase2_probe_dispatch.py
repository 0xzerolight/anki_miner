"""Phase-2 offline pre-filter probe terms come from the processor's profile.

Task 1A.9, discharging the deferral task 1A.4 left at the probe site: the
unbound ``DefinitionService._fallback_candidates(...)`` call in
``_phase2_filter`` becomes ``self.profile.lookup.candidates(...)``.

The seam's owner is the **profile the processor holds**, not
``definition_service`` — a pre-existing test stubs that service with a bare
``MagicMock`` and asserts on the probe's contents, so routing the dispatch
through it would starve the probe (controller ruling on mock-pinned
pre-existing tests). ``JaLookupStrategy`` is a pure delegate to the same
static, so the ja probe terms stay byte-identical.

Non-ja profiles are stubbed with ``dataclasses.replace`` of the ja profile
(controller ruling R6): ``get_profile("zh")`` raises until Stage 2A.
"""

from __future__ import annotations

import dataclasses
from typing import Any
from unittest.mock import MagicMock

import pytest

from anki_miner.languages.profile import LanguageProfile
from anki_miner.languages.registry import get_profile
from anki_miner.models import TokenizedWord
from anki_miner.services.definition_service import DefinitionService
from tests.conftest import build_processor


class _StubLookup:
    """A LookupStrategy answering fixed pairs, recording every call."""

    def __init__(self, answer: list[tuple[str, int]] | None = None) -> None:
        self.answer = [("STUB", 0)] if answer is None else answer
        self.calls: list[tuple[str, str, str | None]] = []

    def candidates(self, word: str, orth_base: str, ctype: str | None) -> list[tuple[str, int]]:
        self.calls.append((word, orth_base, ctype))
        return list(self.answer)


def _profile_with(lookup: Any, *, code: str = "zh", script: Any = None) -> LanguageProfile:
    changes: dict[str, Any] = {"code": code, "lookup": lookup}
    if script is not None:
        changes["script"] = script
    return dataclasses.replace(get_profile("ja"), **changes)


class _RecordingScript:
    """A ScriptSupport that records the phase-2 script-filter derivation."""

    def __init__(self) -> None:
        self.filter_options_calls = 0

    def filter_options(self) -> tuple[()]:
        self.filter_options_calls += 1
        return ()

    def matches(self, option_id: str, form: str) -> bool:
        return False

    def contains_target_script(self, text: str) -> bool:
        return True


def _word(lemma: str = "帰る", orth_base: str = "帰れる") -> TokenizedWord:
    word = TokenizedWord(
        surface="帰れ",
        lemma=lemma,
        reading="カエレ",
        sentence=f"{lemma}のテスト",
        start_time=1.0,
        end_time=3.0,
        duration=2.0,
        pos="動詞",
    )
    word.orth_base = orth_base
    return word


@pytest.fixture
def services():
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


def _run_phase2(config, services, words, *, profile=None, tmp_path):
    """Run the pipeline as far as the curation gate, then cancel."""
    services["subtitle_parser"].parse_subtitle_file.return_value = words
    services["subtitle_parser"].parse_subtitle_file_with_index.side_effect = lambda f, offset=None: (words, [])
    services["word_filter"].filter_unknown.return_value = list(words)
    kwargs: dict[str, Any] = dict(services)
    if profile is not None:
        kwargs["profile"] = profile
    proc = build_processor(config, **kwargs)
    proc.process_episode(tmp_path / "ep.mkv", tmp_path / "ep.ass", curation_callback=lambda _words: None)
    return proc


def _probed_candidates(services) -> list[tuple[str, int]]:
    call = services["definition_service"].offline_deinflection_terms_exist.call_args
    assert call is not None, "the deinflection probe never ran"
    return list(call.args[0])


def test_the_probe_terms_come_from_the_processors_profile(test_config, services, tmp_path):
    lookup = _StubLookup([("电影", 0), ("電影", 0)])
    _run_phase2(test_config, services, [_word()], profile=_profile_with(lookup), tmp_path=tmp_path)

    assert _probed_candidates(services) == [("电影", 0), ("電影", 0)]
    assert lookup.calls == [("帰れる", "帰る", None)]


def test_a_zh_config_probes_with_its_own_strategy(test_config, services, tmp_path):
    """The real non-ja shape: a zh config whose profile is the zh profile.

    No registry stub is needed: every phase-2 site — the probe AND the later
    script-type filter — reads the profile the processor holds, so nothing
    re-resolves ``get_profile("zh")``, which has no builder until Stage 2A.
    """
    lookup = _StubLookup([("电影", 0)])
    script = _RecordingScript()
    profile = _profile_with(lookup, script=script)
    config = dataclasses.replace(test_config, language="zh")

    proc = _run_phase2(config, services, [_word()], profile=profile, tmp_path=tmp_path)

    assert proc.profile is profile
    assert _probed_candidates(services) == [("电影", 0)]
    # Both phase-2 seams read the HELD profile: the script-type filter derives
    # its options from this profile's ScriptSupport rather than re-resolving
    # get_profile("zh"), which has no builder and would raise.
    assert script.filter_options_calls == 1


def test_the_probe_does_not_route_through_definition_service(test_config, services, tmp_path):
    """The pre-existing tests stub that service; the seam's owner is the profile."""
    lookup = _StubLookup()
    _run_phase2(test_config, services, [_word()], profile=_profile_with(lookup), tmp_path=tmp_path)

    assert lookup.calls
    services["definition_service"].fallback_candidates.assert_not_called()


def test_the_ja_default_probes_the_unchanged_static_ladder(test_config, services, tmp_path):
    word = _word()
    _run_phase2(test_config, services, [word], tmp_path=tmp_path)

    # The okurigana-only lemma is the ``orth_base`` argument the probe passes.
    expected = DefinitionService._fallback_candidates(word.mined_form, word.lemma, None)
    assert expected, "fixture must produce a non-empty JA ladder"
    assert _probed_candidates(services) == expected


def test_the_safe_lemma_alternate_still_reaches_the_strategy(test_config, services, tmp_path):
    """Okurigana-only lemma alternates are passed as ``orth_base``, as before."""
    lookup = _StubLookup()
    word = _word(lemma="表わす", orth_base="表せる")
    services["definition_service"].has_offline_definitions.side_effect = lambda terms: dict.fromkeys(terms, False)
    _run_phase2(test_config, services, [word], profile=_profile_with(lookup), tmp_path=tmp_path)

    assert lookup.calls == [("表せる", "表わす", None)]
