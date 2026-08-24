"""WordCurationDialog season-curation media switching (lazy per-episode swap)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from anki_miner.gui.utils.phrase_wrap import WORD_JOINER
from anki_miner.gui.widgets.dialogs import word_curation_dialog as wcd
from anki_miner.gui.widgets.dialogs.word_curation_dialog import (
    CurationMediaContext,
    WordCurationDialog,
)
from anki_miner.models import TokenizedWord


def _word(
    lemma: str,
    start_time: float = 1.0,
    video: Path | None = None,
    sentence: str | None = None,
) -> TokenizedWord:
    return TokenizedWord(
        surface=lemma,
        lemma=lemma,
        reading="よみ",
        sentence=sentence or f"{lemma}の文",
        start_time=start_time,
        end_time=start_time + 2.0,
        duration=2.0,
        video_file=video,
    )


@pytest.fixture()
def ep1(tmp_path):
    p = tmp_path / "ep1.mkv"
    p.write_bytes(b"")
    return p


@pytest.fixture()
def ep2(tmp_path):
    p = tmp_path / "ep2.mkv"
    p.write_bytes(b"")
    return p


def _ctx_for(video: Path, resolver=None) -> CurationMediaContext:
    return CurationMediaContext(
        video_file=video,
        subtitle_entries=[(1.0, 3.0, "文")],
        offset=0.0,
        context_resolver=resolver,
    )


def _build_dialog(qtbot, words, ctx):
    from PyQt6.QtWidgets import QWidget

    real_stub = QWidget()
    with patch.object(WordCurationDialog, "_create_player_widget", return_value=real_stub):
        dlg = WordCurationDialog(words, media_context=ctx)
    qtbot.addWidget(dlg)
    mock_player = MagicMock()
    dlg.player_widget = mock_player
    return dlg, mock_player


def _focus_row(dlg, row):
    dlg.table.setCurrentCell(row, 0)
    dlg._on_row_focus_changed()
    dlg._focus_timer.stop()
    dlg._on_focus_timer_fired()


@pytest.fixture()
def sync_off_thread(monkeypatch):
    def fake_run_off_thread(parent, work, on_done, on_error=None, **kwargs):
        try:
            result = work()
        except Exception as exc:  # noqa: BLE001 — mirrors SingleCallWorker's error path
            if on_error is not None:
                on_error(str(exc))
            return MagicMock()
        on_done(result)
        return MagicMock()

    monkeypatch.setattr(wcd, "run_off_thread", fake_run_off_thread)


@pytest.fixture()
def deferred_off_thread(monkeypatch):
    pending: list[tuple] = []

    def fake_run_off_thread(parent, work, on_done, on_error=None, **kwargs):
        pending.append((work, on_done, on_error))
        return MagicMock()

    monkeypatch.setattr(wcd, "run_off_thread", fake_run_off_thread)
    return pending


class TestLazyEpisodeSwitch:
    def test_same_episode_focus_seeks_without_swap(self, qtbot, ep1, sync_off_thread):
        resolver = MagicMock()
        words = [_word("猫", video=ep1), _word("犬", start_time=5.0, video=ep1)]
        dlg, player = _build_dialog(qtbot, words, _ctx_for(ep1, resolver))
        _focus_row(dlg, 0)
        resolver.assert_not_called()
        player.set_source.assert_not_called()
        player.seek_seconds.assert_called_with(words[0].start_time)

    def test_cross_episode_focus_resolves_and_swaps(self, qtbot, ep1, ep2, sync_off_thread):
        ep2_ctx = _ctx_for(ep2)
        resolver = MagicMock(return_value=ep2_ctx)
        words = [_word("猫", video=ep1), _word("犬", start_time=7.0, video=ep2)]
        dlg, player = _build_dialog(qtbot, words, _ctx_for(ep1, resolver))
        _focus_row(dlg, 1)
        resolver.assert_called_once_with(ep2)
        player.set_source.assert_called_once_with(
            ep2, ep2_ctx.subtitle_entries, ep2_ctx.offset, audio_track_override=None
        )
        # Seek re-fired after the swap; seek_seconds self-defers until loaded.
        player.seek_seconds.assert_called_with(7.0)
        assert dlg._displayed_media_video == ep2

    def test_second_visit_uses_cache(self, qtbot, ep1, ep2, sync_off_thread):
        resolver = MagicMock(return_value=_ctx_for(ep2))
        words = [_word("猫", video=ep1), _word("犬", start_time=7.0, video=ep2)]
        dlg, player = _build_dialog(qtbot, words, _ctx_for(ep1, resolver))
        _focus_row(dlg, 1)
        _focus_row(dlg, 0)
        _focus_row(dlg, 1)
        # ep1's context was seeded at construction; ep2 resolved exactly once.
        resolver.assert_called_once_with(ep2)
        assert player.set_source.call_count == 3  # ep2, back to ep1, ep2 again

    def test_stale_swap_dropped(self, qtbot, ep1, ep2, tmp_path, deferred_off_thread):
        ep3 = tmp_path / "ep3.mkv"
        ep3.write_bytes(b"")
        resolver = MagicMock(side_effect=lambda v: _ctx_for(v))
        words = [
            _word("猫", video=ep1),
            _word("犬", start_time=7.0, video=ep2),
            _word("鳥", start_time=9.0, video=ep3),
        ]
        dlg, player = _build_dialog(qtbot, words, _ctx_for(ep1, resolver))
        _focus_row(dlg, 1)  # dispatch resolve for ep2
        _focus_row(dlg, 2)  # dispatch resolve for ep3 — supersedes ep2
        assert len(deferred_off_thread) == 2
        # Deliver ep2's (stale) result first: must be dropped.
        work, on_done, _err = deferred_off_thread[0]
        on_done(work())
        player.set_source.assert_not_called()
        # ep3's (current) result applies.
        work, on_done, _err = deferred_off_thread[1]
        on_done(work())
        assert player.set_source.call_count == 1
        assert dlg._displayed_media_video == ep3

    def test_closing_blocks_swap_callback(self, qtbot, ep1, ep2, deferred_off_thread):
        resolver = MagicMock(side_effect=lambda v: _ctx_for(v))
        words = [_word("猫", video=ep1), _word("犬", start_time=7.0, video=ep2)]
        dlg, player = _build_dialog(qtbot, words, _ctx_for(ep1, resolver))
        _focus_row(dlg, 1)
        dlg._closing = True
        work, on_done, _err = deferred_off_thread[0]
        on_done(work())
        player.set_source.assert_not_called()

    def test_resolver_none_keeps_current_episode(self, qtbot, ep1, ep2, sync_off_thread):
        resolver = MagicMock(return_value=None)
        words = [_word("猫", video=ep1), _word("犬", start_time=7.0, video=ep2)]
        dlg, player = _build_dialog(qtbot, words, _ctx_for(ep1, resolver))
        _focus_row(dlg, 1)
        player.set_source.assert_not_called()
        assert dlg._displayed_media_video == ep1

    def test_no_resolver_never_swaps(self, qtbot, ep1, ep2, sync_off_thread):
        words = [_word("猫", video=ep1), _word("犬", start_time=7.0, video=ep2)]
        dlg, player = _build_dialog(qtbot, words, _ctx_for(ep1, resolver=None))
        _focus_row(dlg, 1)
        player.set_source.assert_not_called()
        player.seek_seconds.assert_called_with(7.0)


class TestClipPlayGate:
    def test_clip_play_noops_while_swap_in_flight(self, qtbot, ep1, ep2, deferred_off_thread):
        resolver = MagicMock(side_effect=lambda v: _ctx_for(v))
        words = [_word("猫", video=ep1), _word("犬", start_time=7.0, video=ep2)]
        dlg, player = _build_dialog(qtbot, words, _ctx_for(ep1, resolver))
        dlg.clip_editor = MagicMock()
        _focus_row(dlg, 1)  # swap dispatched, not yet applied
        dlg._on_clip_play_requested(6.0, 9.0)
        player.play_range.assert_not_called()
        dlg.clip_editor.set_playing.assert_called_with(False)
        # Space is gated the same way.
        dlg._toggle_play_pause()
        player.toggle_play_pause.assert_not_called()
        # Once the swap lands, playing works again.
        work, on_done, _err = deferred_off_thread[0]
        on_done(work())
        dlg._on_clip_play_requested(6.0, 9.0)
        player.play_range.assert_called_once_with(6.0, 9.0)

    def test_clip_play_unaffected_without_resolver(self, qtbot, ep1, sync_off_thread):
        words = [_word("猫", video=None)]
        dlg, player = _build_dialog(qtbot, words, _ctx_for(ep1))
        dlg.clip_editor = MagicMock()
        _focus_row(dlg, 0)
        dlg._on_clip_play_requested(0.5, 3.5)
        player.play_range.assert_called_once_with(0.5, 3.5)


class TestCandidatePresentation:
    def test_same_pick_distinguishes_episodes(self, ep1, ep2):
        a = _word("猫", start_time=1.0, video=ep1, sentence="同じ歌詞")
        b = _word("猫", start_time=1.0, video=ep2, sentence="同じ歌詞")
        assert WordCurationDialog._same_pick(a, a)
        assert not WordCurationDialog._same_pick(a, b)

    def test_candidate_rows_prefixed_only_cross_episode(self, qtbot, ep1, ep2, sync_off_thread):
        cand1 = _word("猫", start_time=1.0, video=ep1, sentence="一の文")
        cand2 = _word("猫", start_time=7.0, video=ep2, sentence="二の文")
        word = _word("猫", video=ep1, sentence="一の文")
        word.sentence_candidates = [cand1, cand2]
        single = _word("犬", start_time=2.0, video=ep1, sentence="犬文A")
        c1 = _word("犬", start_time=2.0, video=ep1, sentence="犬文A")
        c2 = _word("犬", start_time=4.0, video=ep1, sentence="犬文B")
        single.sentence_candidates = [c1, c2]
        dlg, _player = _build_dialog(qtbot, [word, single], _ctx_for(ep1))
        dlg._populate_candidate_list(word, 0)
        # Strip the BudouX word joiners: this test pins the episode prefix,
        # phrase wrapping is TestPhraseWrappedDisplay's contract.
        texts = [dlg.sentence_list.item(i).text().replace(WORD_JOINER, "") for i in range(dlg.sentence_list.count())]
        assert texts == ["[ep1] 一の文", "[ep2] 二の文"]
        dlg._populate_candidate_list(single, 1)
        texts = [dlg.sentence_list.item(i).text().replace(WORD_JOINER, "") for i in range(dlg.sentence_list.count())]
        assert texts == ["犬文A", "犬文B"]


class TestSelectionCarriesEpisode:
    def test_get_selected_words_preserves_video_file(self, qtbot, ep1, ep2, sync_off_thread):
        cand1 = _word("猫", start_time=1.0, video=ep1, sentence="一の文")
        cand2 = _word("猫", start_time=7.0, video=ep2, sentence="二の文")
        word = _word("猫", video=ep1, sentence="一の文")
        word.sentence_candidates = [cand1, cand2]
        dlg, _player = _build_dialog(qtbot, [word], _ctx_for(ep1))
        # Pick the ep2 candidate.
        dlg._chosen[0] = cand2
        selected = dlg.get_selected_words()
        assert len(selected) == 1
        assert selected[0].video_file == ep2
        # Clip-override copies keep the stamp too.
        dlg._clip_overrides[0] = (6.5, 8.5)
        selected = dlg.get_selected_words()
        assert selected[0].video_file == ep2
        assert selected[0].clip_override == (6.5, 8.5)


class TestExpansionButtonsSeason:
    def test_expansion_buttons_disabled_until_episode_context_cached(self, qtbot, ep1, ep2, deferred_off_thread):
        """A cross-episode word's expansion buttons stay off while the context
        swap is in flight, and refresh when _apply_media_context lands."""
        ep2_ctx = CurationMediaContext(
            video_file=ep2,
            subtitle_entries=[(7.0, 9.0, "犬の文"), (9.5, 11.0, "次の文")],
            offset=0.0,
        )
        resolver = MagicMock(return_value=ep2_ctx)
        words = [_word("猫", video=ep1, sentence="文"), _word("犬", start_time=7.0, video=ep2)]
        dlg, _player = _build_dialog(qtbot, words, _ctx_for(ep1, resolver))
        _focus_row(dlg, 1)  # ep2 swap dispatched, not yet applied
        assert not dlg.expand_prev_button.isEnabled()
        assert not dlg.expand_next_button.isEnabled()

        work, on_done, _err = deferred_off_thread[0]
        on_done(work())  # swap lands

        assert dlg.expand_next_button.isEnabled()
        assert not dlg.expand_prev_button.isEnabled()  # 犬's cue is the first entry
