"""F-01 in the pipeline: i+1 counts unreviewed words as unknown; mining never re-mines them.

The exclusion (every card in Anki) still decides what is mined. Only the
snapshot i+1 and the curator's "Unknowns in line" column read (ctx.unknown_*)
follows the Word Filters box.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from unittest.mock import MagicMock

from anki_miner.exceptions import AnkiConnectionError
from anki_miner.models import TokenizedWord
from anki_miner.orchestration.episode_processor import _EpisodeContext
from anki_miner.services.word_filter import WordFilterService
from tests.conftest import build_processor


def _noun(form: str, start: float) -> TokenizedWord:
    return TokenizedWord(
        surface=form,
        lemma=form,
        reading="",
        sentence=f"{form}。",
        start_time=start,
        end_time=start + 1.0,
        duration=1.0,
        pos="名詞",
    )


def _run(config, *, every, reviewed=frozenset(), reviewed_error=None):
    anki = MagicMock(name="anki_service")
    anki.get_existing_vocabulary.return_value = set(every)
    if reviewed_error is not None:
        anki.get_reviewed_vocabulary.side_effect = reviewed_error
    else:
        anki.get_reviewed_vocabulary.return_value = set(reviewed)
    # bypass skips the dictionary gate and the coverage filters, which need services this test does not build.
    cfg = replace(config, bypass_optional_filters=True)
    definitions = MagicMock(name="definition_service")
    definitions.offline_term_identities.return_value = {}
    processor = build_processor(
        cfg, anki_service=anki, word_filter=WordFilterService(cfg), definition_service=definitions
    )
    ctx = _EpisodeContext(0.0, "", "", "episode", "series", "")
    kept = processor._phase2_filter(ctx, [_noun("猫", 0.0), _noun("犬", 2.0), _noun("鳥", 4.0)], None, None)
    return {word.mined_form for word in kept}, ctx, anki


def test_off_the_snapshot_is_the_mining_set(test_config):
    kept, ctx, anki = _run(test_config, every={"猫", "犬"})

    assert kept == {"鳥"}
    assert ctx.unknown_lemmas == {"鳥"}
    assert ctx.unknown_fronts == {"鳥"}
    anki.get_reviewed_vocabulary.assert_not_called()


def test_on_an_unreviewed_card_counts_as_unknown_but_is_never_mined(test_config):
    config = replace(test_config, known_from_reviewed_cards=True)

    kept, ctx, _ = _run(config, every={"猫", "犬"}, reviewed={"猫"})

    assert kept == {"鳥"}
    assert ctx.unknown_lemmas == {"犬", "鳥"}
    assert ctx.unknown_fronts == {"犬", "鳥"}


def test_on_a_failed_review_query_falls_back_to_every_card(test_config, caplog):
    config = replace(test_config, known_from_reviewed_cards=True)

    with caplog.at_level(logging.WARNING, logger="anki_miner.services.known_word_db"):
        kept, ctx, _ = _run(
            config, every={"猫", "犬"}, reviewed_error=AnkiConnectionError("Anki sent an unusable reply.")
        )

    assert kept == {"鳥"}
    assert ctx.unknown_lemmas == {"鳥"}
    assert any(r.getMessage().startswith("Known from reviews unavailable:") for r in caplog.records)


def test_include_everything_mode_ignores_the_setting(test_config):
    config = replace(test_config, known_from_reviewed_cards=True, include_known_words=True)

    kept, ctx, anki = _run(config, every={"猫"}, reviewed=set())

    assert kept == {"猫", "犬", "鳥"}
    assert ctx.unknown_lemmas == {"猫", "犬", "鳥"}
    anki.get_reviewed_vocabulary.assert_not_called()
