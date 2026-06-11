"""Tests for DeckBuilderWorker — the deck-builder aggregate→preview→build flow.

The worker is driven synchronously by calling ``run()`` directly on the test
thread (not via ``QThread.start()``), matching the existing worker-test style
(see ``test_episode_worker.py``). The confirm gate is pre-set via ``confirm()``
or ``reject()`` so ``run()`` does not block.
"""

from __future__ import annotations

import collections
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.workers import deck_builder_worker as dbw_module
from anki_miner.gui.workers.deck_builder_worker import DeckBuilderWorker
from anki_miner.models.deck_build import DeckBuildRequest, DeckSelectionMode
from anki_miner.models.word import TokenizedWord
from anki_miner.utils.file_pairing import FilePair


@pytest.fixture
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _make_word(lemma: str) -> TokenizedWord:
    """Minimal TokenizedWord; only ``.lemma`` matters for curation."""
    return TokenizedWord(
        surface=lemma,
        lemma=lemma,
        reading=lemma,
        sentence=f"{lemma}。",
        start_time=0.0,
        end_time=1.0,
        duration=1.0,
    )


def _make_pair(stem: str) -> FilePair:
    return FilePair(video=Path(f"/fake/{stem}.mkv"), subtitle=Path(f"/fake/{stem}.ass"))


def _fake_processor(counts: collections.Counter[str], known: set[str] | None = None) -> MagicMock:
    """Build a fake EpisodeProcessor with the attributes the worker reads."""
    proc = MagicMock(name="EpisodeProcessor")
    proc.subtitle_parser.count_lemmas.return_value = counts
    if known is None:
        # No known-words DB; fall back to anki_service.get_existing_vocabulary().
        proc.known_word_db = None
        proc.anki_service.get_existing_vocabulary.return_value = set()
    else:
        proc.known_word_db.is_available.return_value = True
        proc.known_word_db.get_known_words.return_value = known
        # source='user' ignore list is folded into the preview estimate (T-24);
        # default to empty so the estimate equals the known set in these tests.
        proc.known_word_db.get_words_by_source.return_value = set()
    proc.process_episode.return_value = MagicMock(cards_created=1)
    return proc


def _make_request(pairs, *, mode=DeckSelectionMode.ALL, value=0.0, collection_filter=False) -> DeckBuildRequest:
    return DeckBuildRequest(
        pairs=pairs,
        deck_name="My Deck",
        mode=mode,
        value=value,
        collection_filter=collection_filter,
    )


def _make_worker(qapp, request, *, processors, config_kwargs=None) -> tuple[DeckBuilderWorker, MagicMock]:
    """Construct a worker whose ``create_episode_processor`` is patched.

    ``processors`` is a list returned in order on each factory call (Phase 1
    base processor first, then one per episode). ``config_kwargs`` overrides
    extra config fields (e.g. ``use_known_words_db``). Returns
    ``(worker, factory)``.
    """
    factory = MagicMock(side_effect=processors)
    patcher = patch.object(dbw_module, "create_episode_processor", factory)
    patcher.start()
    # Real config so dataclasses.replace(...) works (it requires a real dataclass).
    config = AnkiMinerConfig(anki_deck_name="original_deck", include_known_words=False, **(config_kwargs or {}))
    presenter = MagicMock(name="presenter")
    worker = DeckBuilderWorker(
        request=request,
        config=config,
        presenter=presenter,
        progress_callback=MagicMock(name="ProgressCallback"),
        stats_service=None,
    )
    worker._stop_patch = patcher  # so the test can stop it in teardown
    return worker, factory


def _collect(signal) -> list:
    """Attach a list-appending slot to a signal and return the backing list."""
    received: list = []
    signal.connect(lambda *args: received.append(args if len(args) != 1 else args[0]))
    return received


# --------------------------------------------------------------------------- #
# Phase 1: preview
# --------------------------------------------------------------------------- #


def test_phase1_emits_preview(qapp):
    """Phase 1 emits preview_ready with a DeckBuildPreview, then proceeds on confirm."""
    counts = collections.Counter({"a": 3, "b": 1})
    base = _fake_processor(counts)
    ep = _fake_processor(counts)
    worker, _ = _make_worker(qapp, _make_request([_make_pair("ep1")]), processors=[base, ep])
    try:
        previews = _collect(worker.preview_ready)
        worker.confirm()  # pre-set the gate so run() does not block
        worker.run()
        assert len(previews) == 1
        from anki_miner.models.deck_build import DeckBuildPreview

        assert isinstance(previews[0], DeckBuildPreview)
        assert previews[0].unique_lemmas == 2
        assert previews[0].total_tokens == 4
    finally:
        worker._stop_patch.stop()


# --------------------------------------------------------------------------- #
# Phase 2: build wiring
# --------------------------------------------------------------------------- #


def test_build_ensures_deck_and_processes_each_pair(qapp):
    """On confirm: ensure_deck called once; process_episode once per pair, routed to deck."""
    counts = collections.Counter({"a": 1, "b": 1})
    base = _fake_processor(counts)
    ep1 = _fake_processor(counts)
    ep2 = _fake_processor(counts)
    worker, factory = _make_worker(
        qapp, _make_request([_make_pair("ep1"), _make_pair("ep2")]), processors=[base, ep1, ep2]
    )
    try:
        worker.confirm()
        worker.run()

        base.anki_service.ensure_deck.assert_called_once_with("My Deck")
        ep1.process_episode.assert_called_once()
        ep2.process_episode.assert_called_once()

        # The per-episode processors are built from a replaced config routed to the deck.
        # factory.call_args_list[0] is the Phase-1 base processor; [1:] are per-episode.
        per_episode_cfgs = [call.args[0] for call in factory.call_args_list[1:]]
        for cfg in per_episode_cfgs:
            assert cfg.anki_deck_name == "My Deck"
            # collection_filter False -> include everything.
            assert cfg.include_known_words is True
            # Deck Builder always bypasses reduction filters and allows dups.
            assert cfg.bypass_optional_filters is True
            assert cfg.allow_duplicate_cards is True

        # series/episode identity overrides routed.
        _, kwargs = ep1.process_episode.call_args
        assert kwargs["series_name_override"] == "My Deck"
        assert kwargs["episode_name_override"] == "ep1"
    finally:
        worker._stop_patch.stop()


def test_phase2_reuses_base_parser_for_cross_phase_cache(qapp):
    """Each Phase-2 processor must reuse the Phase-1 base processor's parser.

    The per-file tokenization cache is filled in Phase 1 (aggregate →
    count_lemmas) on ``base.subtitle_parser``. For the cache to HIT in Phase 2,
    the per-episode processor must parse through that SAME parser instance, not
    its own freshly-constructed one. We assert the worker rebinds each episode
    processor's ``subtitle_parser`` to ``base.subtitle_parser`` before mining.
    """
    counts = collections.Counter({"a": 1, "b": 1})
    base = _fake_processor(counts)
    ep1 = _fake_processor(counts)
    ep2 = _fake_processor(counts)
    # Distinct sentinel so the assertion can't pass by coincidence.
    base_parser = base.subtitle_parser
    assert ep1.subtitle_parser is not base_parser
    assert ep2.subtitle_parser is not base_parser

    worker, _ = _make_worker(qapp, _make_request([_make_pair("ep1"), _make_pair("ep2")]), processors=[base, ep1, ep2])
    try:
        worker.confirm()
        worker.run()

        # After the build, every per-episode processor shares the base parser.
        assert ep1.subtitle_parser is base_parser
        assert ep2.subtitle_parser is base_parser
    finally:
        worker._stop_patch.stop()


def test_cross_episode_dedup(qapp):
    """A selected lemma is carded only at its first occurrence across episodes."""
    # Corpus has lemma 'a' (selected via ALL). Two episodes both contain 'a'.
    counts = collections.Counter({"a": 2})
    base = _fake_processor(counts)
    ep1 = _fake_processor(counts)
    ep2 = _fake_processor(counts)
    worker, _ = _make_worker(qapp, _make_request([_make_pair("ep1"), _make_pair("ep2")]), processors=[base, ep1, ep2])
    try:
        worker.confirm()
        worker.run()

        # Extract the curation_callback handed to each episode and feed it the same word.
        cb1 = ep1.process_episode.call_args.kwargs["curation_callback"]
        cb2 = ep2.process_episode.call_args.kwargs["curation_callback"]

        kept_ep1 = cb1([_make_word("a")])
        assert [w.lemma for w in kept_ep1] == ["a"]  # carded on first occurrence

        kept_ep2 = cb2([_make_word("a")])
        assert kept_ep2 == []  # dropped: already carded in ep1
    finally:
        worker._stop_patch.stop()


def test_collection_filter_false_includes_everything(qapp):
    """collection_filter False -> include_known_words True; known_lemmas empty so known_skipped == 0."""
    counts = collections.Counter({"a": 1, "b": 1})
    # known source returns a known word, but it must NOT be consulted when filter is off.
    base = _fake_processor(counts, known={"a"})
    ep = _fake_processor(counts)
    worker, factory = _make_worker(
        qapp, _make_request([_make_pair("ep1")], collection_filter=False), processors=[base, ep]
    )
    try:
        previews = _collect(worker.preview_ready)
        worker.confirm()
        worker.run()

        # Known source not consulted -> known_skipped 0.
        base.known_word_db.get_known_words.assert_not_called()
        assert previews[0].known_skipped == 0

        cfg = factory.call_args_list[1].args[0]
        assert cfg.include_known_words is True
    finally:
        worker._stop_patch.stop()


def test_collection_filter_true_fetches_known(qapp):
    """collection_filter True -> include_known_words False; known lemmas fetched from the DB cache.

    The DB-cache branch is gated on use_known_words_db (T-24), so enable it here.
    """
    counts = collections.Counter({"a": 1, "b": 1})
    base = _fake_processor(counts, known={"a"})
    ep = _fake_processor(counts)
    worker, factory = _make_worker(
        qapp,
        _make_request([_make_pair("ep1")], collection_filter=True),
        processors=[base, ep],
        config_kwargs={"use_known_words_db": True},
    )
    try:
        previews = _collect(worker.preview_ready)
        worker.confirm()
        worker.run()

        base.known_word_db.get_known_words.assert_called_once()
        assert previews[0].known_skipped == 1  # 'a' is both selected and known

        cfg = factory.call_args_list[1].args[0]
        assert cfg.include_known_words is False
        # Filter bypass / dup allowance are independent of the collection checkbox.
        assert cfg.bypass_optional_filters is True
        assert cfg.allow_duplicate_cards is True
    finally:
        worker._stop_patch.stop()


def test_collection_filter_true_falls_back_to_anki(qapp):
    """When no known-words DB, the preview estimate uses anki_service.get_existing_vocabulary()."""
    counts = collections.Counter({"a": 1, "b": 1})
    base = _fake_processor(counts)  # known_word_db is None
    base.anki_service.get_existing_vocabulary.return_value = {"b"}
    ep = _fake_processor(counts)
    worker, _ = _make_worker(qapp, _make_request([_make_pair("ep1")], collection_filter=True), processors=[base, ep])
    try:
        previews = _collect(worker.preview_ready)
        worker.confirm()
        worker.run()
        base.anki_service.get_existing_vocabulary.assert_called_once()
        assert previews[0].known_skipped == 1  # 'b'
    finally:
        worker._stop_patch.stop()


def _worker_with_config(qapp, config) -> DeckBuilderWorker:
    """Construct a worker with a specific config for direct ``_known_lemmas`` tests."""
    return DeckBuilderWorker(
        request=_make_request([_make_pair("ep1")], collection_filter=True),
        config=config,
        presenter=MagicMock(name="presenter"),
        progress_callback=MagicMock(name="ProgressCallback"),
        stats_service=None,
    )


def test_known_lemmas_db_toggle_off_uses_anki_vocab_not_db(qapp):
    """Regression (T-24): use_known_words_db=False + a populated DB file must

    fall back to anki_service.get_existing_vocabulary(), NOT the DB cache —
    matching Phase-2's gate (episode_processor.py). The DB file exists for any
    user who curated a word, but the live-vocab subtraction is what the build
    actually applies, so the preview must use the same source or diverge
    ("promised 2,401, built 51").
    """
    config = AnkiMinerConfig(use_known_words_db=False)
    worker = _worker_with_config(qapp, config)

    base = MagicMock(name="EpisodeProcessor")
    base.known_word_db.is_available.return_value = True
    base.known_word_db.get_known_words.return_value = {"db_cached_word"}
    base.known_word_db.get_words_by_source.return_value = set()
    base.anki_service.get_existing_vocabulary.return_value = {"anki_live_word"}

    result = worker._known_lemmas(base)

    assert result == {"anki_live_word"}
    base.known_word_db.get_known_words.assert_not_called()
    base.anki_service.get_existing_vocabulary.assert_called_once()


def test_known_lemmas_folds_user_ignore_list_into_anki_branch(qapp):
    """Regression (T-24): the source='user' ignore list must be unioned into the

    Anki-vocab branch, mirroring episode_processor.py's always-applied user
    list (Issue #42). Without it the preview omits user-curated words the build
    still subtracts.
    """
    config = AnkiMinerConfig(use_known_words_db=False)
    worker = _worker_with_config(qapp, config)

    base = MagicMock(name="EpisodeProcessor")
    base.known_word_db.is_available.return_value = True
    base.known_word_db.get_words_by_source.return_value = {"user_ignored"}
    base.anki_service.get_existing_vocabulary.return_value = {"anki_live_word"}

    result = worker._known_lemmas(base)

    assert result == {"anki_live_word", "user_ignored"}
    base.known_word_db.get_words_by_source.assert_called_once_with("user")


def test_known_lemmas_db_toggle_on_uses_db_cache(qapp):
    """When use_known_words_db=True and the DB is available, the preview uses the

    DB cache (unioned with the user ignore list), matching Phase-2's enabled path.
    """
    config = AnkiMinerConfig(use_known_words_db=True)
    worker = _worker_with_config(qapp, config)

    base = MagicMock(name="EpisodeProcessor")
    base.known_word_db.is_available.return_value = True
    base.known_word_db.get_known_words.return_value = {"db_cached_word"}
    base.known_word_db.get_words_by_source.return_value = {"user_ignored"}

    result = worker._known_lemmas(base)

    assert result == {"db_cached_word", "user_ignored"}
    base.known_word_db.get_known_words.assert_called_once()
    base.anki_service.get_existing_vocabulary.assert_not_called()


def test_known_lemmas_no_db_file_uses_anki_vocab(qapp):
    """No DB file (is_available False) under use_known_words_db=True still falls

    back to live Anki vocab — the user ignore list is empty (guarded by
    is_available), so the result is just the Anki set.
    """
    config = AnkiMinerConfig(use_known_words_db=True)
    worker = _worker_with_config(qapp, config)

    base = MagicMock(name="EpisodeProcessor")
    base.known_word_db.is_available.return_value = False
    base.anki_service.get_existing_vocabulary.return_value = {"anki_live_word"}

    result = worker._known_lemmas(base)

    assert result == {"anki_live_word"}
    base.known_word_db.get_known_words.assert_not_called()
    base.known_word_db.get_words_by_source.assert_not_called()
    base.anki_service.get_existing_vocabulary.assert_called_once()


# --------------------------------------------------------------------------- #
# Gate: reject / cancel
# --------------------------------------------------------------------------- #


def test_reject_before_confirm_skips_build(qapp):
    """reject() unblocks the gate; run() returns without ensure_deck/process_episode."""
    counts = collections.Counter({"a": 1})
    base = _fake_processor(counts)
    ep = _fake_processor(counts)
    worker, factory = _make_worker(qapp, _make_request([_make_pair("ep1")]), processors=[base, ep])
    try:
        worker.reject()
        worker.run()
        base.anki_service.ensure_deck.assert_not_called()
        # only the Phase-1 base processor was created.
        assert factory.call_count == 1
    finally:
        worker._stop_patch.stop()


def test_cancel_before_confirm_skips_build(qapp):
    """cancel() unblocks the gate and run() returns without building."""
    counts = collections.Counter({"a": 1})
    base = _fake_processor(counts)
    ep = _fake_processor(counts)
    worker, factory = _make_worker(qapp, _make_request([_make_pair("ep1")]), processors=[base, ep])
    try:
        worker.cancel()
        worker.run()
        base.anki_service.ensure_deck.assert_not_called()
        assert factory.call_count == 1
    finally:
        worker._stop_patch.stop()


# --------------------------------------------------------------------------- #
# Finish
# --------------------------------------------------------------------------- #


def test_build_finished_sums_cards_and_reports_coverage(qapp):
    """build_finished emits summed cards_created and the preview coverage pct."""
    counts = collections.Counter({"a": 1})
    base = _fake_processor(counts)
    ep1 = _fake_processor(counts)
    ep1.process_episode.return_value = MagicMock(cards_created=2)
    ep2 = _fake_processor(counts)
    ep2.process_episode.return_value = MagicMock(cards_created=3)
    worker, _ = _make_worker(qapp, _make_request([_make_pair("ep1"), _make_pair("ep2")]), processors=[base, ep1, ep2])
    try:
        previews = _collect(worker.preview_ready)
        finished = _collect(worker.build_finished)
        completed = _collect(worker.item_completed)
        worker.confirm()
        worker.run()

        assert len(finished) == 1
        total, coverage = finished[0]
        assert total == 5
        assert coverage == previews[0].projected_coverage_pct
        # per-item completion signals carried the right card counts.
        assert ("ep1", 2) in completed
        assert ("ep2", 3) in completed
    finally:
        worker._stop_patch.stop()


def test_cancel_mid_build_does_not_emit_build_finished(qapp):
    """A cancel during the build loop must NOT emit build_finished.

    Otherwise the GUI would show a "build complete" summary for a partial,
    cancelled run.
    """
    counts = collections.Counter({"a": 1})
    base = _fake_processor(counts)
    ep1 = _fake_processor(counts)
    ep2 = _fake_processor(counts)
    worker, _ = _make_worker(qapp, _make_request([_make_pair("ep1"), _make_pair("ep2")]), processors=[base, ep1, ep2])

    def cancel_during_ep1(*args, **kwargs):
        worker.cancel()
        return MagicMock(cards_created=1)

    ep1.process_episode.side_effect = cancel_during_ep1
    try:
        finished = _collect(worker.build_finished)
        worker.confirm()
        worker.run()

        # Build was cancelled mid-loop: no completion summary, and ep2 never ran.
        assert finished == []
        ep2.process_episode.assert_not_called()
    finally:
        worker._stop_patch.stop()


def test_cancel_mid_build_propagates_to_processor(qapp):
    """A cancel during process_episode must propagate into the active processor.

    Phase 2 only polls check_cancelled() between episodes, so without
    propagation the current episode runs to completion (ffmpeg + lookups) and
    the GUI's "Cancelling…" state never clears. The worker must call
    proc.cancel() on the running EpisodeProcessor so process_episode returns
    promptly.
    """
    counts = collections.Counter({"a": 1})
    base = _fake_processor(counts)
    ep1 = _fake_processor(counts)
    ep2 = _fake_processor(counts)
    worker, _ = _make_worker(qapp, _make_request([_make_pair("ep1"), _make_pair("ep2")]), processors=[base, ep1, ep2])

    def cancel_during_ep1(*args, **kwargs):
        worker.cancel()
        return MagicMock(cards_created=1)

    ep1.process_episode.side_effect = cancel_during_ep1
    try:
        worker.confirm()
        worker.run()

        # The cancel reached the processor that was mining ep1.
        ep1.cancel.assert_called_once()
        ep2.process_episode.assert_not_called()
    finally:
        worker._stop_patch.stop()


def test_empty_episode_does_not_abort_build(qapp):
    """An episode yielding 0 cards (cancelled-empty curation result) does not stop the loop.

    Simulates process_episode returning a cancelled-empty result (cards_created=0)
    for ep1; ep2 must still be processed and the build still finishes.
    """
    counts = collections.Counter({"a": 1})
    base = _fake_processor(counts)
    ep1 = _fake_processor(counts)
    ep1.process_episode.return_value = MagicMock(cards_created=0)
    ep2 = _fake_processor(counts)
    ep2.process_episode.return_value = MagicMock(cards_created=4)
    worker, _ = _make_worker(qapp, _make_request([_make_pair("ep1"), _make_pair("ep2")]), processors=[base, ep1, ep2])
    try:
        finished = _collect(worker.build_finished)
        worker.confirm()
        worker.run()
        ep2.process_episode.assert_called_once()
        assert finished[0][0] == 4
    finally:
        worker._stop_patch.stop()


def test_error_during_phase1_emits_error(qapp):
    """An exception in run() is caught and surfaced via the inherited error signal."""
    base = _fake_processor(collections.Counter())
    base.subtitle_parser.count_lemmas.side_effect = RuntimeError("boom")
    worker, _ = _make_worker(qapp, _make_request([_make_pair("ep1")]), processors=[base])
    try:
        errors = _collect(worker.error)
        worker.confirm()
        worker.run()
        assert errors == ["boom"]
    finally:
        worker._stop_patch.stop()
