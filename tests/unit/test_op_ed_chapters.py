"""OP/ED chapters (F-08 step 2): the ffprobe chapter probe and the title match."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from anki_miner.services.subtitle_cleaner import OP_ED_CHAPTER_RE, midpoint_in_ranges, op_ed_ranges, op_ed_skip_ranges
from anki_miner.utils.audio_track_detector import Chapter, list_chapters

_CLEANER = "anki_miner.services.subtitle_cleaner"
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

    def test_require_video_asks_for_picture_streams_in_the_same_probe(self, tmp_path):
        video = tmp_path / "ep01.mkv"
        stdout = json.dumps({"streams": [{"index": 0}], **json.loads(_chapters_json(("0.0", "90.0", "OP")))})

        with patch(f"{_DETECTOR}.subprocess.run", return_value=_proc(stdout)) as run:
            chapters = list_chapters(video, "ffprobe-x", require_video=True)

        assert chapters == [Chapter(0.0, 90.0, "OP")]
        assert run.call_count == 1
        command = run.call_args.args[0]
        assert command[command.index("-select_streams") + 1] == "V"
        assert command[-1] == str(video)

    def test_require_video_gives_audio_only_media_no_chapters(self, tmp_path):
        """An audiobook's chapters are the book's own: "Opening the Door" is not an opening."""
        book = tmp_path / "book.m4b"
        stdout = json.dumps({"streams": [], **json.loads(_chapters_json(("0.0", "600.0", "Opening the Door")))})

        with patch(f"{_DETECTOR}.subprocess.run", return_value=_proc(stdout)):
            assert list_chapters(book, "ffprobe", require_video=True) == []

    @pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg/ffprobe")
    def test_cover_art_is_not_video_on_a_real_file(self, tmp_path):
        meta = tmp_path / "chapters.txt"
        meta.write_text(
            ";FFMETADATA1\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=2000\ntitle=Opening the Door\n",
            encoding="utf-8",
        )
        book = tmp_path / "book.m4b"
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=d=2", "-f", "lavfi"]
            + ["-i", "color=c=red:s=64x64:d=1", "-i", str(meta), "-map", "0:a", "-map", "1:v"]
            + ["-map_chapters", "2", "-c:a", "aac", "-c:v", "mjpeg", "-frames:v", "1"]
            + ["-disposition:v", "attached_pic", str(book)],
            check=True,
            timeout=60,
        )

        assert [c.title for c in list_chapters(book, "ffprobe")] == ["Opening the Door"]
        assert list_chapters(book, "ffprobe", require_video=True) == []

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


class TestChapterTitles:
    @pytest.mark.parametrize(
        "title",
        [
            "OP",
            "Opening",
            "OP1",
            "op 2",
            "ED",
            "ED2",
            "Ending Credits",
            "Part B (ED)",
            "Outro",
            "Preview",
            "Next Episode Preview",
            "PV",
            "Credits",
            "Credit Roll",
            "オープニング",
            "エンディング",
            "次回予告",
        ],
    )
    def test_names_an_opening_ending_or_preview(self, title):
        assert OP_ED_CHAPTER_RE.search(title)

    @pytest.mark.parametrize(
        "title",
        [
            "Part A",
            "Part B",
            "Episode",
            "Avant",
            "Prologue",
            "Chapter 1",
            "Opener",
            "Edit",
            "Intro",
            "Introduction",
            "Bending",
        ],
    )
    def test_other_titles_do_not_match(self, title):
        assert OP_ED_CHAPTER_RE.search(title) is None

    def test_op_ed_ranges_keep_only_matching_chapters_with_a_length(self):
        chapters = [
            Chapter(0.0, 90.0, "Opening"),
            Chapter(90.0, 600.0, "Part A"),
            Chapter(600.0, 690.0, "Ending"),
            Chapter(690.0, 720.0, "Preview"),
            Chapter(720.0, 720.0, "OP"),
            Chapter(0.0, 1.0, None),
        ]

        assert op_ed_ranges(chapters) == ((0.0, 90.0), (600.0, 690.0), (690.0, 720.0))


class TestOpEdSkipRanges:
    def test_a_missing_file_is_never_probed(self, tmp_path):
        with patch(f"{_CLEANER}.list_chapters", side_effect=AssertionError("probed")):
            assert op_ed_skip_ranges(tmp_path / "missing.mkv", "ffprobe") == ()

    def test_an_existing_file_is_probed_once(self, tmp_path, caplog):
        video = tmp_path / "ep01.mkv"
        video.write_bytes(b"")
        chapters = [Chapter(0.0, 90.0, "OP"), Chapter(90.0, 500.0, "Part A")]

        with (
            patch(f"{_CLEANER}.list_chapters", return_value=chapters) as probe,
            caplog.at_level(logging.INFO, logger=_CLEANER),
        ):
            assert op_ed_skip_ranges(video, "ffprobe-x") == ((0.0, 90.0),)

        probe.assert_called_once_with(video, "ffprobe-x", require_video=True)
        assert "OP" in caplog.text

    def test_no_matching_chapter_skips_nothing(self, tmp_path):
        video = tmp_path / "ep01.mkv"
        video.write_bytes(b"")

        with patch(f"{_CLEANER}.list_chapters", return_value=[Chapter(0.0, 500.0, "Part A")]):
            assert op_ed_skip_ranges(video, "ffprobe") == ()


class TestMidpointInRanges:
    def test_the_midpoint_decides_and_the_end_is_open(self):
        ranges = ((10.0, 20.0),)

        assert midpoint_in_ranges(9.0, 12.0, ranges)  # middle 10.5
        assert not midpoint_in_ranges(5.0, 14.0, ranges)  # middle 9.5
        assert midpoint_in_ranges(18.0, 21.0, ranges)  # middle 19.5
        assert not midpoint_in_ranges(19.0, 21.0, ranges)  # middle 20.0

    def test_no_ranges_keeps_everything(self):
        assert not midpoint_in_ranges(0.0, 1.0, ())
