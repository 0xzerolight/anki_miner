"""Tests for the synced-candidate validator."""

from pathlib import Path

import pysubs2

from anki_miner.services.sync_engines import SyncResult
from anki_miner.services.sync_validator import validate_candidate

_OK = SyncResult(ok=True, engine="test")


def _save(path: Path, starts: list[int], duration: int = 1000) -> Path:
    subs = pysubs2.SSAFile()
    subs.events = [pysubs2.SSAEvent(start=s, end=s + duration, text=f"line {i}") for i, s in enumerate(starts)]
    subs.save(str(path), encoding="utf-8")
    return path


def _starts(count: int, base: int = 1000, step: int = 3000) -> list[int]:
    return [base + i * step for i in range(count)]


class TestValidateCandidate:
    def test_uniform_shift_passes(self, tmp_path):
        orig = _save(tmp_path / "o.srt", _starts(20))
        cand = _save(tmp_path / "c.srt", [s + 1500 for s in _starts(20)])
        verdict = validate_candidate(orig, cand, _OK)
        assert verdict.ok
        assert verdict.reasons == ()

    def test_engine_failure_rejected_without_file_reads(self, tmp_path):
        missing = tmp_path / "nowhere.srt"
        verdict = validate_candidate(missing, missing, SyncResult(ok=False, engine="alass", detail="exit 1"))
        assert not verdict.ok
        assert "alass: exit 1" in verdict.reasons

    def test_engine_warning_rejects(self, tmp_path):
        orig = _save(tmp_path / "o.srt", _starts(20))
        cand = _save(tmp_path / "c.srt", [s + 100 for s in _starts(20)])
        result = SyncResult(ok=True, engine="alass", warnings=("negative timestamps clamped",))
        verdict = validate_candidate(orig, cand, result)
        assert not verdict.ok
        assert any("negative timestamps" in r for r in verdict.reasons)

    def test_huge_shift_rejected(self, tmp_path):
        orig = _save(tmp_path / "o.srt", _starts(20))
        cand = _save(tmp_path / "c.srt", [s + 6 * 60 * 1000 for s in _starts(20)])
        verdict = validate_candidate(orig, cand, _OK)
        assert not verdict.ok
        assert any("max cue shift" in r for r in verdict.reasons)

    def test_huge_block_shift_from_engine_rejected(self, tmp_path):
        orig = _save(tmp_path / "o.srt", _starts(20))
        cand = _save(tmp_path / "c.srt", [s + 500 for s in _starts(20)])
        result = SyncResult(ok=True, engine="alass", block_shifts_seconds=(0.5, 720.0))
        verdict = validate_candidate(orig, cand, result)
        assert not verdict.ok

    def test_scrambled_order_rejected(self, tmp_path):
        starts = _starts(20)
        orig = _save(tmp_path / "o.srt", starts)
        scrambled = list(starts)
        scrambled[5], scrambled[6] = scrambled[6] + 40_000, scrambled[5]
        cand = _save(tmp_path / "c.srt", scrambled)
        verdict = validate_candidate(orig, cand, _OK)
        assert not verdict.ok
        assert any("scrambled" in r for r in verdict.reasons)

    def test_zero_pileup_rejected(self, tmp_path):
        starts = _starts(20, base=5000)
        orig = _save(tmp_path / "o.srt", starts)
        clamped = [0, 0, 0, *starts[3:]]
        cand = _save(tmp_path / "c.srt", clamped)
        verdict = validate_candidate(orig, cand, _OK)
        assert not verdict.ok
        assert any("00:00" in r for r in verdict.reasons)

    def test_cues_past_video_end_rejected(self, tmp_path):
        orig = _save(tmp_path / "o.srt", _starts(20))
        cand = _save(tmp_path / "c.srt", [s + 60_000 for s in _starts(20)])
        verdict = validate_candidate(orig, cand, _OK, video_duration_seconds=90.0)
        assert not verdict.ok
        assert any("past the end" in r for r in verdict.reasons)

    def test_span_stretch_rejected(self, tmp_path):
        starts = _starts(20)
        orig = _save(tmp_path / "o.srt", starts)
        cand = _save(tmp_path / "c.srt", [s * 2 for s in starts])
        verdict = validate_candidate(orig, cand, _OK)
        assert not verdict.ok
        assert any("span scaled" in r for r in verdict.reasons)

    def test_framerate_ratio_span_change_passes(self, tmp_path):
        starts = _starts(20)
        orig = _save(tmp_path / "o.srt", starts)
        cand = _save(tmp_path / "c.srt", [int(s * 25 / 23.976) for s in starts])
        assert validate_candidate(orig, cand, _OK).ok

    def test_cue_count_change_rejected(self, tmp_path):
        orig = _save(tmp_path / "o.srt", _starts(20))
        cand = _save(tmp_path / "c.srt", _starts(19))
        verdict = validate_candidate(orig, cand, _OK)
        assert not verdict.ok
        assert any("cue count" in r for r in verdict.reasons)

    def test_unparsable_candidate_rejected(self, tmp_path):
        orig = _save(tmp_path / "o.srt", _starts(20))
        cand = tmp_path / "c.srt"
        cand.write_bytes(b"\x00garbage")
        assert not validate_candidate(orig, cand, _OK).ok
