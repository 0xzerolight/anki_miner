"""DeckBuilderWorker: one season item, a counted preview, a Build gate, then the mine pass."""

from __future__ import annotations

import copy
import dataclasses
import logging
import sqlite3
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions import AnkiConnectionError
from anki_miner.gui.workers.deck_builder_worker import DeckBuilderWorker, deck_build_config
from anki_miner.models.batch_queue import QueueItemStatus
from anki_miner.models.deck_build import DeckBuildRequest, DeckCorpus, DeckSelectionMode
from anki_miner.models.processing import ProcessingResult
from anki_miner.models.word import TokenizedWord
from anki_miner.services.anki_service import AnkiService
from anki_miner.services.stats_service import StatsService
from anki_miner.services.word_pool import CaptureCurationCallback

EP1 = Path("/tmp/ep1.mkv")
EP2 = Path("/tmp/ep2.mkv")
SUB1 = Path("/tmp/ep1.ass")
SUB2 = Path("/tmp/ep2.ass")
PAIRS = [
    SimpleNamespace(video=EP1, subtitle=SUB1, secondary=None),
    SimpleNamespace(video=EP2, subtitle=SUB2, secondary=None),
]

# 私 is counted but never reaches the pool (a known word).
COUNTS = {SUB1: {"私": 3, "猫": 2, "犬": 1}, SUB2: {"私": 2, "猫": 1, "鳥": 1}}


@pytest.fixture(autouse=True)
def anki_calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Records ensure_deck and the card-target preflight, in call order."""
    calls: list[tuple[str, str]] = []

    def _verify_card_target(service: AnkiService) -> None:
        calls.append(("verify", service.config.anki_deck_name))

    def _ensure_deck(_service: AnkiService, deck_name: str) -> None:
        calls.append(("ensure_deck", deck_name))

    monkeypatch.setattr(AnkiService, "verify_card_target", _verify_card_target)
    monkeypatch.setattr(AnkiService, "ensure_deck", _ensure_deck)
    return calls


def _word(lemma: str, *candidate_lemmas: str) -> TokenizedWord:
    word = TokenizedWord(
        surface=lemma,
        lemma=lemma,
        reading="よみ",
        sentence="文",
        start_time=1.0,
        end_time=3.0,
        duration=2.0,
        occurrence_count=1,
    )
    word.sentence_candidates = [dataclasses.replace(word, lemma=c, surface=c) for c in candidate_lemmas]
    return word


def _words() -> dict[Path, list[TokenizedWord]]:
    return {EP1: [_word("猫", "猫", "猫"), _word("犬")], EP2: [_word("猫"), _word("鳥")]}


class _FakeProcessor:
    """Stands in for EpisodeProcessor the way the season tests' fake does.

    Each ``process_episode`` call tokenizes afresh (new word objects), records
    a difficulty row on whatever stats service the worker handed the factory,
    and runs the curation callback exactly as production does: the pre-pass
    capture returns ``[]`` and the mine pass returns its fixed subset.
    """

    def __init__(self, words_by_video, counts_by_subtitle=None, order=None):
        self.words_by_video = words_by_video
        self.stats_service = None
        self.factory_configs: list[AnkiMinerConfig] = []
        self.subtitle_parser = MagicMock()
        self.subtitle_parser.count_lemmas.side_effect = lambda sub: Counter((counts_by_subtitle or COUNTS)[sub])
        self.process_episode = MagicMock(side_effect=self._process)
        self.prepass_candidates: list[list[list[TokenizedWord]]] = []
        self.mined: list[tuple[Path, list[str]]] = []
        self.difficulty_writers: list[object] = []
        self.order = order

    def _process(self, video, subtitle, progress_callback=None, curation_callback=None, **kwargs):
        if self.order is not None:
            self.order.append(("process", video.name))
        if self.stats_service is not None:
            self.difficulty_writers.append(self.stats_service)
            self.stats_service.record_difficulty("Show", video.name, 10, 5)
        words = copy.deepcopy(self.words_by_video.get(video, []))
        curated = curation_callback(words) if curation_callback is not None else words
        if isinstance(curation_callback, CaptureCurationCallback):
            self.prepass_candidates.append([w.sentence_candidates for w in words])
        elif curated:
            self.mined.append((video, [w.mined_form for w in curated]))
        return ProcessingResult(total_words_found=10, new_words_found=len(curated), cards_created=len(curated))

    def factory(self, config, presenter, stats_service=None, *args, **kwargs):
        # The worker passes stats as the third positional argument.
        self.factory_configs.append(config)
        self.stats_service = stats_service
        return self

    def cancel(self) -> None:
        pass

    def close(self) -> None:
        pass


def _request(**overrides) -> DeckBuildRequest:
    fields = {
        "video_folder": Path("/tmp/video"),
        "subtitle_folder": Path("/tmp/subs"),
        "deck_name": "Show",
        "skip_known": True,
        "review": False,
    }
    fields.update(overrides)
    return DeckBuildRequest(**fields)


def _worker(request=None, *, stats_service=None, curation_callback=None) -> DeckBuilderWorker:
    return DeckBuilderWorker(
        request or _request(),
        AnkiMinerConfig(),
        MagicMock(name="Presenter"),
        stats_service=stats_service,
        curation_callback=curation_callback,
    )


def _patched(proc: _FakeProcessor):
    return (
        patch("anki_miner.gui.workers.batch_queue_worker.create_episode_processor", side_effect=proc.factory),
        patch(
            "anki_miner.utils.file_pairing.FilePairMatcher.find_pairs_by_episode_number",
            return_value=PAIRS,
        ),
    )


def _run(worker: DeckBuilderWorker, proc: _FakeProcessor) -> dict[str, list]:
    seen: dict[str, list] = {"previews": [], "errors": [], "finished": []}
    worker.preview_ready.connect(seen["previews"].append)
    worker.error.connect(seen["errors"].append)
    worker.queue_finished.connect(lambda cards, _coverage: seen["finished"].append(cards))
    factory_patch, pairs_patch = _patched(proc)
    with factory_patch, pairs_patch:
        worker.run()
    return seen


def test_build_config_forces_bypass_duplicates_and_known_toggle():
    config = AnkiMinerConfig(
        anki_deck_name="Mining",
        include_known_words=False,
        bypass_optional_filters=False,
        allow_duplicate_cards=False,
    )

    skip = deck_build_config(config, _request(deck_name="Core", skip_known=True))
    keep = deck_build_config(config, _request(deck_name="Core", skip_known=False))

    assert skip.anki_deck_name == keep.anki_deck_name == "Core"
    assert skip.include_known_words is False
    assert keep.include_known_words is True
    for built in (skip, keep):
        assert built.bypass_optional_filters is True
        assert built.allow_duplicate_cards is True
    assert config.anki_deck_name == "Mining"  # the caller's config is untouched


def test_ensure_deck_and_config_receive_the_request_name_verbatim(anki_calls):
    worker = _worker(_request(deck_name="Show::S1 "))
    proc = _FakeProcessor(_words())
    worker.confirm(DeckSelectionMode.ALL, 0)

    _run(worker, proc)

    assert ("ensure_deck", "Show::S1 ") in anki_calls
    assert ("verify", "Show::S1 ") in anki_calls
    assert worker.config.anki_deck_name == "Show::S1 "
    assert [config.anki_deck_name for config in proc.factory_configs] == ["Show::S1 "]
    assert worker.item.display_name == "Show::S1 "


def test_ensure_deck_runs_before_any_episode(anki_calls):
    worker = _worker()
    proc = _FakeProcessor(_words(), order=anki_calls)
    worker.confirm(DeckSelectionMode.ALL, 0)

    _run(worker, proc)

    # The deck exists before the card-target preflight and every episode.
    assert anki_calls[0] == ("ensure_deck", "Show")
    assert anki_calls[1] == ("verify", "Show")
    assert [kind for kind, _ in anki_calls[2:]] == ["process"] * 4


def test_stale_dictionaries_skip_ensure_deck(anki_calls):
    worker = _worker()
    proc = _FakeProcessor(_words())
    worker.confirm(DeckSelectionMode.ALL, 0)

    with (
        patch(
            "anki_miner.gui.workers.deck_builder_worker.stale_resource_reimport_error",
            return_value="Reimport the dictionary.",
        ),
        patch(
            "anki_miner.gui.workers.batch_queue_worker.stale_resource_reimport_error",
            return_value="Reimport the dictionary.",
        ),
    ):
        seen = _run(worker, proc)

    assert anki_calls == []
    assert seen["errors"] == ["Reimport the dictionary."]
    proc.process_episode.assert_not_called()
    assert seen["previews"] == []


def test_ensure_deck_failure_emits_error_and_mines_nothing(monkeypatch, anki_calls):
    def _refuse(_service, _deck_name):
        raise AnkiConnectionError("Anki is not running.")

    monkeypatch.setattr(AnkiService, "ensure_deck", _refuse)
    worker = _worker()
    proc = _FakeProcessor(_words())
    worker.confirm(DeckSelectionMode.ALL, 0)

    seen = _run(worker, proc)

    assert seen["errors"] == ["Anki is not running."]
    assert anki_calls == []  # the preflight never ran
    proc.process_episode.assert_not_called()
    assert seen["previews"] == []
    assert seen["finished"] == [0]
    assert worker.item.status == QueueItemStatus.PENDING


def test_preview_ready_carries_counts_and_row_lemmas():
    worker = _worker()
    proc = _FakeProcessor(_words())
    worker.confirm(DeckSelectionMode.ALL, 0)

    seen = _run(worker, proc)

    assert len(seen["previews"]) == 1
    corpus = seen["previews"][0]
    assert isinstance(corpus, DeckCorpus)
    assert corpus is worker.corpus
    assert corpus.counts == {"私": 5, "猫": 3, "犬": 1, "鳥": 1}
    assert corpus.episodes == 2
    # One row per merged mined_form; 私 never reached the pool.
    assert sorted(corpus.row_lemmas, key=sorted) == [
        frozenset({"犬"}),
        frozenset({"猫"}),
        frozenset({"鳥"}),
    ]


def test_confirm_top_n_mines_only_selected_lemmas():
    worker = _worker()
    proc = _FakeProcessor(_words())
    # Confirmed from the preview slot, the way the screen's Build does it.
    worker.preview_ready.connect(lambda _corpus: worker.confirm(DeckSelectionMode.TOP_N, 2))

    seen = _run(worker, proc)

    # Top 2 is {私, 猫}; 私 has no row, so 猫 is the one card, in its first episode.
    assert proc.mined == [(EP1, ["猫"])]
    assert seen["finished"] == [1]
    assert worker.item.status == QueueItemStatus.COMPLETED
    assert len(worker.item.committed_pair_keys) == 2


def test_preconfirm_before_gate_runs_straight_through():
    worker = _worker()
    proc = _FakeProcessor(_words())
    worker.confirm(DeckSelectionMode.ALL, 0)

    seen = _run(worker, proc)

    assert len(seen["previews"]) == 1
    assert proc.mined == [(EP1, ["猫", "犬"]), (EP2, ["鳥"])]
    assert seen["finished"] == [3]
    assert worker.item.status == QueueItemStatus.COMPLETED


def test_cancel_at_gate_unblocks(qtbot):
    worker = _worker()
    proc = _FakeProcessor(_words())
    factory_patch, pairs_patch = _patched(proc)

    with factory_patch, pairs_patch:
        with qtbot.waitSignal(worker.preview_ready, timeout=5000):
            worker.start()
        assert worker.isRunning()  # parked at the Build gate
        with qtbot.waitSignal(worker.finished, timeout=5000):
            worker.cancel()
        assert worker.wait(5000)

    assert proc.process_episode.call_count == 2  # the pre-pass only
    assert proc.mined == []
    assert worker.item.status == QueueItemStatus.PENDING
    assert worker.item.committed_pair_keys == set()


def test_cancel_during_counting_returns_without_preview():
    worker = _worker()
    proc = _FakeProcessor(_words())

    def _count_then_cancel(subtitle):
        worker.cancel()
        return Counter(COUNTS[subtitle])

    proc.subtitle_parser.count_lemmas.side_effect = _count_then_cancel

    seen = _run(worker, proc)

    # Cancel lands between files: the second subtitle is never counted.
    assert proc.subtitle_parser.count_lemmas.call_count == 1
    assert seen["previews"] == []
    assert worker.corpus is None
    assert proc.mined == []
    assert worker.item.status == QueueItemStatus.PENDING


def test_cancel_during_counting_clears_the_capture():
    """Finding 2: a cancel mid-counting must not keep the per-episode capture
    (with its per-episode pools) alive past the run."""
    worker = _worker()
    proc = _FakeProcessor(_words())

    def _count_then_cancel(subtitle):
        worker.cancel()
        return Counter(COUNTS[subtitle])

    proc.subtitle_parser.count_lemmas.side_effect = _count_then_cancel

    _run(worker, proc)

    assert worker._capture is None


def test_review_off_capture_drops_sentence_candidates():
    curator = MagicMock()
    worker = _worker(_request(review=False), curation_callback=curator)
    proc = _FakeProcessor(_words())
    pools_at_preview: list[list] = []

    def _on_preview(_corpus):
        capture = proc.process_episode.call_args_list[0].kwargs["curation_callback"]
        pools_at_preview.append(list(capture.pools))

    worker.preview_ready.connect(_on_preview)
    worker.confirm(DeckSelectionMode.ALL, 0)

    _run(worker, proc)

    assert proc.prepass_candidates == [[[], []], [[], []]]
    assert pools_at_preview == [[]]
    assert proc.process_episode.call_count == 4  # pre-pass and mine, per pair
    curator.assert_not_called()


def test_review_on_routes_the_pool_through_the_curation_callback():
    curator = MagicMock(side_effect=lambda pool: [w for w in pool if w.mined_form == "猫"])
    worker = _worker(_request(review=True), curation_callback=curator)
    proc = _FakeProcessor(_words())
    worker.confirm(DeckSelectionMode.TOP_N, 3)

    seen = _run(worker, proc)

    # Top 3 is {私, 猫, 犬}: the curator sees only the selected rows.
    curator.assert_called_once()
    pool = curator.call_args.args[0]
    assert sorted(w.mined_form for w in pool) == ["犬", "猫"]
    cat = next(w for w in pool if w.mined_form == "猫")
    # Episode 1's two lines plus episode 2's one: kept for the sentence picker.
    assert len(cat.sentence_candidates) == 3
    assert proc.mined == [(EP1, ["猫"])]
    assert seen["finished"] == [1]


@pytest.mark.parametrize("build", [False, True], ids=["preview-then-cancel", "preview-then-build"])
def test_no_difficulty_rows_recorded(tmp_path, build):
    db_path = tmp_path / "stats.db"
    stats = StatsService(db_path)
    assert stats.load()
    worker = _worker(stats_service=stats)
    proc = _FakeProcessor(_words())
    if build:
        worker.confirm(DeckSelectionMode.ALL, 0)
    else:
        worker.preview_ready.connect(lambda _corpus: worker.cancel())

    seen = _run(worker, proc)

    assert len(seen["previews"]) == 1
    assert len(proc.difficulty_writers) == proc.process_episode.call_count == (4 if build else 2)
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM series_difficulty").fetchone()[0] == 0


def test_all_known_still_previews_and_completes_with_zero_cards():
    curator = MagicMock()
    worker = _worker(_request(review=True), curation_callback=curator)
    proc = _FakeProcessor({EP1: [], EP2: []})
    worker.confirm(DeckSelectionMode.ALL, 0)

    seen = _run(worker, proc)

    assert len(seen["previews"]) == 1
    corpus = seen["previews"][0]
    assert corpus.counts == {"私": 5, "猫": 3, "犬": 1, "鳥": 1}
    assert corpus.row_lemmas == ()
    curator.assert_not_called()
    assert proc.process_episode.call_count == 2
    assert seen["finished"] == [0]
    assert worker.item.status == QueueItemStatus.COMPLETED
    assert len(worker.item.committed_pair_keys) == 2


def test_run_logs_one_item(caplog):
    worker = _worker(_request(deck_name="Show"))
    proc = _FakeProcessor(_words())
    worker.confirm(DeckSelectionMode.TOP_N, 2)

    with caplog.at_level(logging.INFO, logger="anki_miner.gui.workers.deck_builder_worker"):
        _run(worker, proc)

    messages = [record.getMessage() for record in caplog.records]
    start = next(m for m in messages if m.startswith("BatchQueueWorkerThread started:"))
    assert "items=1" in start
    preview = next(m for m in messages if m.startswith("DeckBuilderWorker preview:"))
    assert "deck=Show" in preview
    assert "episodes=2" in preview
    assert "unique_lemmas=4" in preview
    assert "rows=3" in preview
    build = next(m for m in messages if m.startswith("DeckBuilderWorker build:"))
    assert "mode=top_n" in build
    assert "value=2.0" in build
    assert "cards=1" in build
