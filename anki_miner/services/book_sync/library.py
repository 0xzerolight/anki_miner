"""Which books a chosen Audiobook Sync folder holds (WP-27). No Qt.

A folder with audio files directly in it is one book read in file-name order
(the book itself is picked separately), exactly as before. A folder with no
audio of its own is a shelf: each subfolder holding one book (``.epub``, or
else one ``.txt``) and its audio files is one book, and the books run in
subfolder-name order. A subfolder that does not fit is reported, not guessed.
Only one level deep: a book's audio sits directly in its subfolder.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from anki_miner.services.reading._util import natural_sort_key
from anki_miner.utils.file_utils import is_junk_path


class SkipReason(Enum):
    """Why a subfolder of a shelf is not a book."""

    NO_AUDIO = "no_audio"
    NO_BOOK = "no_book"
    SEVERAL_BOOKS = "several_books"


@dataclass(frozen=True)
class BookJob:
    """One book and the audio files that read it, in reading order."""

    book: Path
    audio_files: tuple[Path, ...]
    #: The subfolder the job came from on a shelf, else None. In a chosen
    #: output folder each book writes into a subfolder of this name, so two
    #: books' ``01.mp3`` never write the same ``01.srt``.
    out_subdir: str | None = None


@dataclass(frozen=True)
class FolderScan:
    """What :func:`scan_book_folder` found; at most one of the first two is non-empty."""

    #: Audio directly in the folder (one book, picked separately), natural order.
    audio_files: tuple[Path, ...] = ()
    #: One job per usable subfolder, natural order.
    books: tuple[BookJob, ...] = ()
    #: Subfolders that are not a book, with why.
    skipped: tuple[tuple[str, SkipReason], ...] = ()


def _sorted_files(folder: Path) -> list[Path]:
    files = (f for f in folder.iterdir() if f.is_file() and not is_junk_path(f.name))
    return sorted(files, key=lambda f: natural_sort_key(f.name))


def scan_book_folder(folder: Path, audio_suffixes: frozenset[str]) -> FolderScan:
    """Classify ``folder`` (see the module docstring). Raises ``OSError`` when it cannot be listed.

    Args:
        folder: The folder the user picked.
        audio_suffixes: Lower-case audio extensions with the dot (``.mp3``).
    """
    audio = tuple(f for f in _sorted_files(folder) if f.suffix.lower() in audio_suffixes)
    if audio:
        return FolderScan(audio_files=audio)
    subfolders = sorted(
        (d for d in folder.iterdir() if d.is_dir() and not is_junk_path(d.name)),
        key=lambda d: natural_sort_key(d.name),
    )
    books: list[BookJob] = []
    skipped: list[tuple[str, SkipReason]] = []
    for sub in subfolders:
        files = _sorted_files(sub)
        sub_audio = tuple(f for f in files if f.suffix.lower() in audio_suffixes)
        epubs = [f for f in files if f.suffix.lower() == ".epub"]
        texts = [f for f in files if f.suffix.lower() == ".txt"]
        candidates = epubs or texts  # an EPUB wins over a stray notes.txt
        if not sub_audio:
            skipped.append((sub.name, SkipReason.NO_AUDIO))
        elif not candidates:
            skipped.append((sub.name, SkipReason.NO_BOOK))
        elif len(candidates) > 1:
            skipped.append((sub.name, SkipReason.SEVERAL_BOOKS))
        else:
            books.append(BookJob(book=candidates[0], audio_files=sub_audio, out_subdir=sub.name))
    return FolderScan(books=tuple(books), skipped=tuple(skipped))
