"""S12: a lemma-fronted language never rebuilds its front from a swapped surface."""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace

from anki_miner.gui.workers import deck_filter_worker
from anki_miner.models import LineLemmas, TokenizedWord
from anki_miner.services.word_filter import WordFilterService


class _NoTokens:
    def __call__(self, text):
        return []


def _verb() -> TokenizedWord:
    return TokenizedWord(
        surface="читала",
        lemma="читать",
        reading="",
        sentence="Она читала книгу.",
        start_time=0.0,
        end_time=1.0,
        duration=1.0,
        pos="VERB",
        expression_reading="чита́ть",
        mined_form_override="читать",
    )


def _line() -> LineLemmas:
    return LineLemmas(
        line_text="Он читал газету.",
        lemmas=frozenset({"читать"}),
        start_time=2.0,
        end_time=3.0,
        duration=1.0,
        lemma_spans=(("читать", "читал", 3, 8, 8),),
    )


def test_lemma_fronted_policy_keeps_the_front_and_reading(test_config):
    service = WordFilterService(test_config, tagger=_NoTokens(), expression_tracks_surface=lambda word: False)

    assert service._line_preserves_mined_form(_verb(), _line()) is True
    swapped = service._swap_word_to_line(_verb(), _line())

    assert swapped.surface == "читал"
    assert swapped.expression_reading == "чита́ть"


def test_default_keeps_the_japanese_literal(test_config):
    """A non-動詞/形容詞 POS tracks its surface: the reading is regenerated."""
    service = WordFilterService(test_config, tagger=_NoTokens())

    swapped = service._swap_word_to_line(_verb(), _line())

    assert swapped.expression_reading == ""  # regenerated from the new surface by the (empty) tagger


def test_deck_filter_bundle_reads_the_policy_attribute(test_config, monkeypatch):
    def tracks(word):
        return False

    policy = SimpleNamespace(mined_form=lambda *a, **k: "", expression_tracks_surface=tracks)
    profile = SimpleNamespace(mined_form=policy, script=SimpleNamespace(), dedup_fold=None)
    monkeypatch.setattr(deck_filter_worker, "get_profile", lambda code: profile)
    monkeypatch.setattr(deck_filter_worker, "KnownWordDB", lambda *a, **k: None)
    monkeypatch.setattr("anki_miner.services.tagger.get_shared_tagger", lambda: None)

    bundle = deck_filter_worker._build_filter_bundle(dataclasses.replace(test_config, use_blacklist=False), None)

    assert bundle.word_filter._tracks_surface is tracks
