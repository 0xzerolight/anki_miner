"""The expression-audio ladder is the profile's, with the ja one as default."""

from __future__ import annotations

from pathlib import Path

from anki_miner.models import TokenizedWord
from anki_miner.orchestration import audio_stage
from anki_miner.services.audio_fetch_common import expression_audio_candidates
from anki_miner.services.expression_audio_fetcher import ChainedExpressionAudioFetcher


def _word() -> TokenizedWord:
    return TokenizedWord(
        surface="学生",
        lemma="学生",
        reading="がくせい",
        sentence="学生です。",
        start_time=0.0,
        end_time=1.0,
        duration=1.0,
        expression_reading="がくせい",
        lemma_reading="がくせい",
    )


class _NoHit:
    def fetch(self, mined_form, reading, cancelled_check=None) -> Path | None:
        return None

    def fetch_candidates(self, candidates, cancelled_check=None) -> Path | None:
        return None


def test_default_ladder_is_the_ja_one():
    chain = ChainedExpressionAudioFetcher([_NoHit()])
    word = _word()
    assert chain.candidates_for(word) == expression_audio_candidates(word)


def test_profile_callable_replaces_the_ladder():
    chain = ChainedExpressionAudioFetcher([_NoHit()], candidates=lambda w: [(w.surface, "")])
    assert chain.candidates_for(_word()) == [("学生", "")]


def test_stage_falls_back_to_the_ja_ladder_for_a_duck_fetcher():
    word = _word()
    # Existing suites inject fetcher fakes with no candidates_for; they must keep
    # the ja ladder rather than raising AttributeError mid-run.
    assert audio_stage._candidate_ladder(_NoHit(), word) == expression_audio_candidates(word)
    chain = ChainedExpressionAudioFetcher([_NoHit()], candidates=lambda w: [("x", "y")])
    assert audio_stage._candidate_ladder(chain, word) == [("x", "y")]


def test_a_magicmock_fetcher_keeps_the_ja_ladder():
    """A mock fetcher auto-grows any attribute; the stage must not believe it.

    tests/unit/test_audio_stage.py passes a bare MagicMock as the fetcher and
    asserts the exact ja pairs reach fetch_candidates.
    """
    from unittest.mock import MagicMock

    word = _word()
    assert audio_stage._candidate_ladder(MagicMock(), word) == expression_audio_candidates(word)


def test_factory_passes_the_profile_candidates(monkeypatch):
    import dataclasses

    from anki_miner.config import AnkiMinerConfig, AudioSourceEntry
    from anki_miner.gui.utils import service_factory
    from anki_miner.languages.registry import get_profile

    ja = get_profile("ja")
    ladder = [("学生", "がくせい"), ("学生", "ガクセイ")]
    fake_audio = dataclasses.replace(ja.audio, candidates=lambda word: ladder)
    monkeypatch.setattr(service_factory, "get_profile", lambda code: dataclasses.replace(ja, audio=fake_audio))
    config = dataclasses.replace(AnkiMinerConfig(), expression_audio_chain=(AudioSourceEntry(kind="googletts"),))
    chain = service_factory.create_expression_audio_fetcher(config)
    assert chain.candidates_for(_word()) == ladder
