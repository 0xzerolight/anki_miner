"""WP-27: which books a chosen Audiobook Sync folder holds (services/book_sync/library.py)."""

from __future__ import annotations

from pathlib import Path

import pytest

from anki_miner.services.book_sync.library import BookJob, SkipReason, scan_book_folder

_AUDIO = frozenset({".mp3", ".m4b"})


def _touch(folder: Path, *names: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for name in names:
        (folder / name).write_bytes(b"x")


def test_audio_directly_in_the_folder_is_one_book_as_before(tmp_path):
    _touch(tmp_path, "10.mp3", "2.mp3", "1.mp3", "notes.txt", "._1.mp3")
    _touch(tmp_path / "Vol 1", "a.epub", "01.mp3")

    scan = scan_book_folder(tmp_path, _AUDIO)

    assert [p.name for p in scan.audio_files] == ["1.mp3", "2.mp3", "10.mp3"]
    assert scan.books == () and scan.skipped == ()


def test_a_shelf_gives_one_job_per_book_folder_in_natural_order(tmp_path):
    _touch(tmp_path / "Vol 10", "ten.epub", "01.mp3")
    _touch(tmp_path / "Vol 2", "two.epub", "notes.txt", "10.mp3", "9.mp3", "._9.mp3")
    _touch(tmp_path / "__MACOSX", "junk.epub", "01.mp3")
    _touch(tmp_path, "cover.jpg")

    scan = scan_book_folder(tmp_path, _AUDIO)

    assert scan.audio_files == ()
    assert scan.books == (
        BookJob(
            tmp_path / "Vol 2" / "two.epub", (tmp_path / "Vol 2" / "9.mp3", tmp_path / "Vol 2" / "10.mp3"), "Vol 2"
        ),
        BookJob(tmp_path / "Vol 10" / "ten.epub", (tmp_path / "Vol 10" / "01.mp3",), "Vol 10"),
    )
    assert scan.skipped == ()


def test_a_txt_book_counts_when_there_is_no_epub(tmp_path):
    _touch(tmp_path / "Aozora", "book.txt", "01.m4b")

    scan = scan_book_folder(tmp_path, _AUDIO)

    assert [job.book.name for job in scan.books] == ["book.txt"]


@pytest.mark.parametrize(
    ("files", "reason"),
    [
        (("front.jpg",), SkipReason.NO_AUDIO),
        (("book.epub",), SkipReason.NO_AUDIO),
        (("01.mp3",), SkipReason.NO_BOOK),
        (("a.epub", "b.epub", "01.mp3"), SkipReason.SEVERAL_BOOKS),
        (("a.txt", "b.txt", "01.mp3"), SkipReason.SEVERAL_BOOKS),
    ],
)
def test_a_subfolder_that_is_not_one_book_is_reported_not_guessed(tmp_path, files, reason):
    _touch(tmp_path / "Odd", *files)
    _touch(tmp_path / "Good", "g.epub", "01.mp3")

    scan = scan_book_folder(tmp_path, _AUDIO)

    assert [job.out_subdir for job in scan.books] == ["Good"]
    assert scan.skipped == (("Odd", reason),)


def test_an_empty_folder_holds_nothing(tmp_path):
    scan = scan_book_folder(tmp_path, _AUDIO)
    assert scan.audio_files == () and scan.books == () and scan.skipped == ()
