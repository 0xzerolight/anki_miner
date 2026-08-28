"""Tests for the retime orchestrator (engines and reference resolution mocked).

Engine internals are covered by test_alass_engine / test_ffsubsync_engine;
here the engines are fakes that write real subtitle files, so the chain,
cleaning round-trip, validation gate, commit/backup, and keep-original
guarantee are exercised on real file contents.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pysubs2
import pytest

from anki_miner.exceptions.subtitle import AlassNotFoundError
from anki_miner.services.retime_reference import RetimeReference
from anki_miner.services.subtitle_retimer import TMP_SUBDIR_NAME, retime_subtitle
from anki_miner.services.sync_engines import SyncResult
from anki_miner.utils.file_pairing import FilePairMatcher

_FFS = "anki_miner.services.subtitle_retimer.sync_with_ffsubsync"
_ALASS = "anki_miner.services.subtitle_retimer.sync_with_alass"
_REF = "anki_miner.services.subtitle_retimer.resolve_reference"
_DUR = "anki_miner.services.subtitle_retimer.get_media_duration_seconds"
_FFPROBE = "anki_miner.services.subtitle_retimer.resolve_ffprobe"


def _write_sub(path: Path, starts: list[int], texts: list[str] | None = None) -> Path:
    subs = pysubs2.SSAFile()
    for i, start in enumerate(starts):
        text = texts[i] if texts else f"せりふ {i}"
        subs.events.append(pysubs2.SSAEvent(start=start, end=start + 1000, text=text))
    subs.save(str(path), encoding="utf-8")
    return path


def _starts(count: int = 14) -> list[int]:
    return [2000 + i * 3000 for i in range(count)]


def _fake_engine(shift_ms: int = 1500, *, ok: bool = True, engine: str = "fake"):
    """An engine stand-in that writes a uniformly shifted copy of its input."""

    def _run(config: Any, reference: Path, in_sub: Path, out: Path, **kwargs: Any) -> SyncResult:
        if not ok:
            return SyncResult(ok=False, engine=engine, detail="engine failed")
        subs = pysubs2.load(str(in_sub))
        for event in subs.events:
            event.start += shift_ms
            event.end += shift_ms
        subs.save(str(out), encoding="utf-8")
        return SyncResult(ok=True, engine=engine)

    return _run


@pytest.fixture()
def video(tmp_path: Path) -> Path:
    p = tmp_path / "ep01.mkv"
    p.touch()
    return p


@pytest.fixture()
def in_sub(tmp_path: Path) -> Path:
    return _write_sub(tmp_path / "jp01.srt", _starts())


@pytest.fixture()
def out_sub(tmp_path: Path) -> Path:
    return tmp_path / "ep01.srt"


@pytest.fixture()
def cfg() -> MagicMock:
    return MagicMock()


@pytest.fixture(autouse=True)
def quiet_probes():
    """No reference resolution and no ffprobe duration probe by default."""
    with patch(_REF, return_value=None), patch(_DUR, return_value=None), patch(_FFPROBE, return_value="ffprobe"):
        yield


def _cue_starts(path: Path) -> list[int]:
    return [e.start for e in pysubs2.load(str(path)).events]


class TestEngineChain:
    def test_first_engine_success_writes_and_skips_alass(self, cfg, video, in_sub, out_sub):
        with (
            patch(_FFS, side_effect=_fake_engine(1500, engine="ffsubsync")),
            patch(_ALASS) as mock_alass,
        ):
            outcome = retime_subtitle(cfg, video, in_sub, out_sub)

        assert outcome
        assert outcome.engine == "ffsubsync"
        assert out_sub.exists()
        assert _cue_starts(out_sub) == [s + 1500 for s in _starts()]
        mock_alass.assert_not_called()
        assert outcome.attempts == ("ffsubsync: accepted",)

    def test_invalid_first_result_falls_back_to_alass(self, cfg, video, in_sub, out_sub):
        with (
            patch(_FFS, side_effect=_fake_engine(30 * 60 * 1000, engine="ffsubsync")),
            patch(_ALASS, side_effect=_fake_engine(1500, engine="alass")),
        ):
            outcome = retime_subtitle(cfg, video, in_sub, out_sub)

        assert outcome
        assert outcome.engine == "alass"
        assert _cue_starts(out_sub) == [s + 1500 for s in _starts()]
        assert len(outcome.attempts) == 2
        assert "max cue shift" in outcome.attempts[0]

    def test_third_attempt_runs_alass_no_split(self, cfg, video, in_sub, out_sub):
        alass_calls: list[dict[str, Any]] = []

        def _alass(config, reference, sub, out, **kwargs):
            alass_calls.append(kwargs)
            if not kwargs.get("no_split"):
                return SyncResult(ok=False, engine="alass", detail="split failed")
            return _fake_engine(700, engine="alass (single offset)")(config, reference, sub, out, **kwargs)

        with (
            patch(_FFS, side_effect=_fake_engine(ok=False, engine="ffsubsync")),
            patch(_ALASS, side_effect=_alass),
        ):
            outcome = retime_subtitle(cfg, video, in_sub, out_sub)

        assert outcome
        assert [call.get("no_split") for call in alass_calls] == [False, True]
        assert _cue_starts(out_sub) == [s + 700 for s in _starts()]

    def test_fourth_attempt_runs_ffsubsync_no_split(self, cfg, video, in_sub, out_sub):
        ffs_calls: list[dict[str, Any]] = []

        def _ffs(config, reference, sub, out, **kwargs):
            ffs_calls.append(kwargs)
            if kwargs.get("split_mode", True):
                return SyncResult(ok=False, engine="ffsubsync", detail="split failed")
            return _fake_engine(400, engine="ffsubsync")(config, reference, sub, out, **kwargs)

        with (
            patch(_FFS, side_effect=_ffs),
            patch(_ALASS, side_effect=_fake_engine(ok=False, engine="alass")),
        ):
            outcome = retime_subtitle(cfg, video, in_sub, out_sub)

        assert outcome
        assert outcome.attempts[-1].startswith("ffsubsync (single offset)")
        assert [call.get("split_mode", True) for call in ffs_calls] == [True, False]
        assert _cue_starts(out_sub) == [s + 400 for s in _starts()]

    def test_all_engines_fail_keeps_original(self, cfg, video, in_sub, out_sub):
        before = in_sub.read_bytes()
        with (
            patch(_FFS, side_effect=_fake_engine(ok=False, engine="ffsubsync")),
            patch(_ALASS, side_effect=_fake_engine(ok=False, engine="alass")),
        ):
            outcome = retime_subtitle(cfg, video, in_sub, out_sub)

        assert not outcome
        assert "original left untouched" in outcome.reason
        assert not out_sub.exists()
        assert in_sub.read_bytes() == before
        assert len(outcome.attempts) == 4
        assert [a.split(":", 1)[0] for a in outcome.attempts] == [
            "ffsubsync",
            "alass",
            "alass (single offset)",
            "ffsubsync (single offset)",
        ]

    def test_all_candidates_rejected_by_validator_keeps_original_bytes(self, cfg, video, in_sub, out_sub):
        """Every engine can report success (``ok=True``) and still lose every
        candidate to the validator -- an aligner that locks onto the wrong
        optimum exits 0 and writes a syntactically valid file. The
        keep-original guarantee has to hold on THIS path too, not only when
        an engine itself reports failure
        (``test_all_engines_fail_keeps_original`` above)."""
        before = in_sub.read_bytes()
        # Shift far past the validator's 5-minute max-shift bound: every
        # engine "succeeds" but every real candidate file gets rejected.
        huge_shift = _fake_engine(30 * 60 * 1000, ok=True, engine="huge-shift")
        with patch(_FFS, side_effect=huge_shift), patch(_ALASS, side_effect=huge_shift):
            outcome = retime_subtitle(cfg, video, in_sub, out_sub)

        assert not outcome
        assert "original left untouched" in outcome.reason
        assert not out_sub.exists()
        assert in_sub.read_bytes() == before
        assert len(outcome.attempts) == 4
        assert all("max cue shift" in a for a in outcome.attempts)

    def test_all_candidates_rejected_by_validator_in_place_leaves_input_untouched(self, cfg, video, in_sub):
        """Degenerate variant: a caller that hands the input in as *out_sub*
        (the GUI worker never does -- it appends ``_retimed``). A wrongly
        "successful" candidate on every engine must still never reach
        ``os.replace``, so the input comes out byte-identical."""
        before = in_sub.read_bytes()
        huge_shift = _fake_engine(30 * 60 * 1000, ok=True, engine="huge-shift")
        with patch(_FFS, side_effect=huge_shift), patch(_ALASS, side_effect=huge_shift):
            outcome = retime_subtitle(cfg, video, in_sub, in_sub)

        assert not outcome
        assert in_sub.read_bytes() == before

    def test_missing_alass_skips_remaining_alass_attempts(self, cfg, video, in_sub, out_sub):
        alass_calls: list[Any] = []

        def _alass(*args: Any, **kwargs: Any) -> SyncResult:
            alass_calls.append(args)
            raise AlassNotFoundError("not installed")

        with (
            patch(_FFS, side_effect=_fake_engine(ok=False, engine="ffsubsync")),
            patch(_ALASS, side_effect=_alass),
        ):
            outcome = retime_subtitle(cfg, video, in_sub, out_sub)

        assert not outcome
        assert len(alass_calls) == 1
        assert any("not installed" in a for a in outcome.attempts)
        assert [a.split(":", 1)[0] for a in outcome.attempts] == [
            "ffsubsync",
            "alass",
            "ffsubsync (single offset)",
        ]

    def test_sub_reference_forwarded_to_alass(self, cfg, video, in_sub, out_sub, tmp_path):
        reference = _write_sub(tmp_path / "ref.srt", _starts())
        captured: list[dict[str, Any]] = []

        def _alass(config, ref, sub, out, **kwargs):
            captured.append(kwargs)
            return _fake_engine(500, engine="alass")(config, ref, sub, out, **kwargs)

        with (
            patch(_REF, return_value=RetimeReference(path=reference, kind="subtitle", temp=None, label="eng")),
            patch(_FFS, side_effect=_fake_engine(ok=False, engine="ffsubsync")),
            patch(_ALASS, side_effect=_alass),
        ):
            outcome = retime_subtitle(cfg, video, in_sub, out_sub)

        assert outcome
        assert captured[0]["sub_reference"] is True
        assert outcome.reference_label == "eng"


class TestCleaningRoundTrip:
    def test_engine_sees_dialogue_only_output_keeps_all_lines(self, cfg, video, tmp_path, out_sub):
        starts = _starts(14)
        texts = [f"せりふ {i}" for i in range(14)]
        # Three non-dialogue cues interleaved.
        starts = [*starts[:5], 15_500, *starts[5:10], 30_500, *starts[10:], 44_000]
        texts = [*texts[:5], "♪～", *texts[5:10], "（雷鳴）", *texts[10:], "♪♪～"]
        in_sub = _write_sub(tmp_path / "jp01.srt", starts, texts)
        seen_counts: list[int] = []

        def _ffs(config, reference, sub, out, **kwargs):
            seen_counts.append(len(pysubs2.load(str(sub)).events))
            return _fake_engine(1200, engine="ffsubsync")(config, reference, sub, out, **kwargs)

        with patch(_FFS, side_effect=_ffs), patch(_ALASS):
            outcome = retime_subtitle(cfg, video, in_sub, out_sub)

        assert outcome
        assert seen_counts == [14]  # aligner saw dialogue only
        out_events = pysubs2.load(str(out_sub)).events
        assert len(out_events) == 17  # every original line survived
        assert all(e.start == s + 1200 for e, s in zip(out_events, starts, strict=True))


class TestCommit:
    def test_existing_output_replaced_without_a_backup(self, cfg, video, in_sub, out_sub):
        """The file at *out_sub* is a previous retime of the same pair, so it is
        replaced outright: no ``.bak`` sibling is left behind anywhere."""
        out_sub.write_text("old content", encoding="utf-8")
        with patch(_FFS, side_effect=_fake_engine(1500, engine="ffsubsync")), patch(_ALASS):
            outcome = retime_subtitle(cfg, video, in_sub, out_sub)

        assert outcome
        assert _cue_starts(out_sub) == [s + 1500 for s in _starts()]
        assert list(out_sub.parent.glob("*.bak")) == []

    def test_input_untouched_by_a_successful_commit(self, cfg, video, in_sub, out_sub):
        """The input keeps its bytes: the pipeline reads it and writes elsewhere."""
        original = in_sub.read_bytes()
        with patch(_FFS, side_effect=_fake_engine(1500, engine="ffsubsync")), patch(_ALASS):
            assert retime_subtitle(cfg, video, in_sub, out_sub)

        assert in_sub.read_bytes() == original

    def test_no_backup_when_output_is_new(self, cfg, video, in_sub, out_sub):
        with patch(_FFS, side_effect=_fake_engine(1500, engine="ffsubsync")), patch(_ALASS):
            assert retime_subtitle(cfg, video, in_sub, out_sub)
        assert list(out_sub.parent.glob("*.bak")) == []

    @pytest.mark.parametrize("succeed", [True, False])
    def test_temps_cleaned_up(self, cfg, video, in_sub, out_sub, tmp_path, succeed):
        engine = _fake_engine(1500, engine="ffsubsync") if succeed else _fake_engine(ok=False, engine="x")
        with (
            patch(_FFS, side_effect=engine),
            patch(_ALASS, side_effect=_fake_engine(ok=False, engine="alass")),
        ):
            retime_subtitle(cfg, video, in_sub, out_sub)

        # Nothing named ".retime-" leaks at the pairing-folder top level...
        leftovers = [p.name for p in tmp_path.iterdir() if ".retime-" in p.name]
        assert leftovers == []
        # ...and the working subdirectory itself is gone (emptied, then rmdir'd).
        assert not (out_sub.parent / TMP_SUBDIR_NAME).exists()


class TestTempFileLocation:
    """Working files must never land in the pairing folder itself.

    A crash-orphaned ``ep01.retime-cand-0.srt`` sitting directly beside the
    real subtitle is a pairable file (real ``.srt`` suffix) that the next
    folder run's episode matcher would consume, shadowing the genuine
    subtitle. Confining temps to a subdirectory keeps them invisible to
    FilePairMatcher's non-recursive folder scan.
    """

    def test_temps_live_under_tmp_subdir(self, cfg, video, in_sub, out_sub):
        seen: list[Path] = []

        def _ffs(config, reference, sub, out, **kwargs):
            seen.append(sub)
            seen.append(out)
            return _fake_engine(1500, engine="ffsubsync")(config, reference, sub, out, **kwargs)

        with patch(_FFS, side_effect=_ffs), patch(_ALASS):
            outcome = retime_subtitle(cfg, video, in_sub, out_sub)

        assert outcome
        tmp_dir = out_sub.parent / TMP_SUBDIR_NAME
        seen_temps = [p for p in seen if p != in_sub]
        assert seen_temps, "engine should have been handed at least one temp path"
        for p in seen_temps:
            assert p.parent == tmp_dir
            # Engines infer the output format from the suffix, so it must
            # stay a real subtitle extension even while hidden from pairing.
            assert p.suffix in FilePairMatcher.SUBTITLE_EXTENSIONS

    def test_tmp_dir_removed_when_empty_after_run(self, cfg, video, in_sub, out_sub):
        with patch(_FFS, side_effect=_fake_engine(1500, engine="ffsubsync")), patch(_ALASS):
            assert retime_subtitle(cfg, video, in_sub, out_sub)

        assert not (out_sub.parent / TMP_SUBDIR_NAME).exists()

    def test_leftover_temp_not_paired_as_subtitle(self, tmp_path):
        """Documents the hazard the fix removes: an orphaned temp from a
        crashed run must not be pickable as the episode's subtitle."""
        tmp_dir = tmp_path / TMP_SUBDIR_NAME
        tmp_dir.mkdir()
        (tmp_dir / "ep01.retime-cand-0.srt").write_text("orphaned candidate", encoding="utf-8")
        real_sub = tmp_path / "ep01.srt"
        real_sub.write_text("real subtitle", encoding="utf-8")
        video_file = tmp_path / "ep01.mkv"
        video_file.touch()

        pairs = FilePairMatcher.find_pairs_by_episode_number(tmp_path, tmp_path)

        assert len(pairs) == 1
        assert pairs[0].subtitle == real_sub


class TestCancellation:
    def test_pre_set_cancel_runs_no_engine(self, cfg, video, in_sub, out_sub):
        cancel = threading.Event()
        cancel.set()
        with patch(_FFS) as mock_ffs, patch(_ALASS) as mock_alass:
            outcome = retime_subtitle(cfg, video, in_sub, out_sub, cancel_event=cancel)

        assert not outcome
        assert outcome.cancelled
        mock_ffs.assert_not_called()
        mock_alass.assert_not_called()
