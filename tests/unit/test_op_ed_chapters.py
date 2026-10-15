"""OP/ED chapters (F-08 step 2): the ffprobe chapter probe and the title match."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from anki_miner.utils.audio_track_detector import Chapter, list_chapters

_DETECTOR = "anki_miner.utils.audio_track_detector"


def _proc(stdout: str = "", returncode: int = 0) -> MagicMock:
    proc = MagicMock()
    proc.stdout = stdout
    proc.stderr = ""
    proc.returncode = returncode
    return proc


def _chapters_json(*chapters: tuple[str, str, str | None]) -> str:
    rows: list[dict[str, object]] = []
    for start, end, title in chapters:
        row: dict[str, object] = {"id": len(rows), "time_base": "1/1000", "start_time": start, "end_time": end}
        if title is not None:
            row["tags"] = {"title": title}
        rows.append(row)
    return json.dumps({"chapters": rows})


class TestListChapters:
    def test_reads_start_end_and_title(self, tmp_path):
        video = tmp_path / "ep01.mkv"
        stdout = _chapters_json(("0.000000", "89.500000", "Opening"), ("89.500000", "700.000000", None))

        with patch(f"{_DETECTOR}.subprocess.run", return_value=_proc(stdout)) as run:
            chapters = list_chapters(video, "ffprobe-x")

        assert chapters == [Chapter(0.0, 89.5, "Opening"), Chapter(89.5, 700.0, None)]
        assert run.call_args.args[0] == [
            "ffprobe-x",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_chapters",
            str(video),
        ]

    def test_a_file_without_chapters_has_none(self, tmp_path):
        with patch(f"{_DETECTOR}.subprocess.run", return_value=_proc('{"chapters": []}')):
            assert list_chapters(tmp_path / "ep01.mkv") == []

    @pytest.mark.parametrize(
        "proc",
        [_proc("", returncode=1), _proc("not json"), _proc("[]")],
        ids=["exit-1", "not-json", "not-an-object"],
    )
    def test_a_failed_probe_has_none(self, tmp_path, proc):
        with patch(f"{_DETECTOR}.subprocess.run", return_value=proc):
            assert list_chapters(tmp_path / "ep01.mkv") == []

    def test_a_missing_ffprobe_has_none(self, tmp_path):
        with patch(f"{_DETECTOR}.subprocess.run", side_effect=FileNotFoundError("ffprobe")):
            assert list_chapters(tmp_path / "ep01.mkv") == []

    def test_a_malformed_row_is_skipped(self, tmp_path):
        stdout = json.dumps(
            {
                "chapters": [
                    {"start_time": "x", "end_time": "1.0"},
                    {"start_time": "1.0", "end_time": "2.0", "tags": {"title": "ED"}},
                ]
            }
        )

        with patch(f"{_DETECTOR}.subprocess.run", return_value=_proc(stdout)):
            assert list_chapters(tmp_path / "ep01.mkv") == [Chapter(1.0, 2.0, "ED")]
