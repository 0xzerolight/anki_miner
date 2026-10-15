"""StarDict dictionary -> SQLite index importer.

Reads a StarDict book (``.ifo`` + ``.idx``/``.idx.gz`` + ``.dict``/``.dict.dz``,
optional ``.syn``) from a loose ``.ifo`` file, a tar archive (``.tar.gz``,
``.tar.bz2``, ``.tar.xz``: FreeDict's release form) or a zip. Format reference:
StarDict's ``doc/StarDictFileFormat`` (GPL-2.0 project; read for the layout
only, no code copied).

Archives are untrusted: no member is ever extracted under its own name. Only
the book's regular files are streamed out, to fixed names in a temporary
folder, and every byte decompressed (archive members and the gzip-compressed
``.idx.gz``/``.dict.dz``/``.syn.dz``) counts against one budget,
``zip_safety.MAX_UNCOMPRESSED_BYTES``. A tar member's declared size is checked
before the archive reader skips over it, so a bomb is refused before it is
inflated.
"""

from __future__ import annotations

import gzip
import logging
import lzma
import struct
import tarfile
import tempfile
import zipfile
import zlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from anki_miner.exceptions import SetupError, raise_if_cancelled
from anki_miner.services._sqlite_index import read_slot_language
from anki_miner.services._staging import repair_managed_slot
from anki_miner.services.dictionary.importers.glossary_markup import glossary_row, markup_nodes, text_nodes
from anki_miner.services.dictionary.importers.plain_index import (
    STARDICT_TAR,
    STARDICT_ZIP,
    ProgressFn,
    plain_dict_id,
    write_plain_index,
)
from anki_miner.services.dictionary.storage import DictRow
from anki_miner.services.dictionary.zip_safety import MAX_UNCOMPRESSED_BYTES, validate_zip_safe
from anki_miner.utils.logging_ext import log_summary

logger = logging.getLogger(__name__)

_IFO_MAGIC = "StarDict's dict ifo file"
# Field types whose payload is text, by how it reads on a card. Upper-case
# types (binary: sound, picture, resource lists) are skipped.
_MARKUP_TYPES = frozenset("hgx")
_TEXT_TYPES = frozenset("mlty")
# More archive members than any StarDict release has; bounds header spam.
_MAX_ARCHIVE_MEMBERS = 10_000
_CHUNK = 1024 * 1024
# The book's parts, in the order a member name is matched against them.
_PARTS = (".idx.gz", ".idx", ".dict.dz", ".dict", ".syn.dz", ".syn")


@dataclass(frozen=True)
class StarDictImportResult:
    dict_id: str
    source_name: str
    entry_count: int
    skipped_malformed: int


class _Readable(Protocol):
    def read(self, size: int = -1, /) -> bytes: ...


class _Budget:
    """Running count of decompressed bytes; refuses past the shared cap."""

    def __init__(self) -> None:
        self.used = 0

    def take(self, n: int) -> None:
        self.used += n
        if self.used > MAX_UNCOMPRESSED_BYTES:
            raise SetupError(f"StarDict data exceeds the size limit ({self.used:,} > {MAX_UNCOMPRESSED_BYTES:,} bytes)")


def _copy_capped(src: _Readable, dest: Path, budget: _Budget, cancel_check: Callable[[], bool] | None) -> None:
    with dest.open("wb") as out:
        while chunk := src.read(_CHUNK):
            budget.take(len(chunk))
            raise_if_cancelled(cancel_check)
            out.write(chunk)


def is_stardict_source(path: Path) -> bool:
    """True for a ``.ifo`` file, a tar archive, or a zip holding a ``.ifo`` and no ``index.json``."""
    if path.suffix.lower() == ".ifo":
        return True
    try:
        if tarfile.is_tarfile(path):
            return True
        if zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as zf:
                names = [PurePosixPath(n).name for n in zf.namelist()]
            return any(n.lower().endswith(".ifo") for n in names) and "index.json" not in names
    except OSError:
        return False
    return False


def _book_members(names: list[str]) -> dict[str, str]:
    """Map ``.ifo`` and each found part suffix to its member name; one book per archive."""
    ifos = [n for n in names if n.lower().endswith(".ifo")]
    if not ifos:
        raise SetupError("No StarDict .ifo file in the archive")
    if len(ifos) > 1:
        raise SetupError(f"The archive holds {len(ifos)} StarDict dictionaries; add them one at a time")
    stem = ifos[0][: -len(".ifo")]
    found = {".ifo": ifos[0]}
    for suffix in _PARTS:
        for name in names:
            if name == stem + suffix or name.lower() == (stem + suffix).lower():
                found.setdefault(suffix, name)
    return found


def _unpack(
    source: Path, work: Path, budget: _Budget, cancel_check: Callable[[], bool] | None
) -> tuple[dict[str, Path], dict[str, Path]]:
    """Copy the book's files to ``work/book<suffix>``.

    Returns ``(parts, keep)``: the readable parts by suffix, and what the slot
    keeps as its Re-import source (file name -> path).
    """
    parts: dict[str, Path] = {}
    if source.suffix.lower() == ".ifo":
        stem = source.name[: -len(".ifo")]
        siblings = [p.name for p in source.parent.iterdir() if p.name.startswith(stem)]
        members = _book_members([source.name] + [n for n in siblings if not n.lower().endswith(".ifo")])
        keep = {}
        for suffix, name in members.items():
            path = source.parent / name
            if suffix != ".ifo" and not path.is_file():
                continue
            parts[suffix] = path
            keep[name] = path
        return parts, keep
    if tarfile.is_tarfile(source):
        with tarfile.open(source, "r:*") as tf:
            files: dict[str, tarfile.TarInfo] = {}
            declared = 0
            for count, member in enumerate(tf, 1):
                if count > _MAX_ARCHIVE_MEMBERS:
                    raise SetupError("The archive has too many members")
                declared += member.size
                if declared > MAX_UNCOMPRESSED_BYTES:
                    raise SetupError(
                        f"Archive uncompressed size exceeds limit ({declared:,} > {MAX_UNCOMPRESSED_BYTES:,} bytes)"
                    )
                if member.isfile():
                    files[member.name] = member
            for suffix, name in _book_members(list(files)).items():
                extracted = tf.extractfile(files[name])
                if extracted is None:
                    continue
                with extracted:
                    _copy_capped(extracted, work / f"book{suffix}", budget, cancel_check)
                parts[suffix] = work / f"book{suffix}"
        return parts, {STARDICT_TAR: source}
    try:
        with zipfile.ZipFile(source) as zf:
            validate_zip_safe(zf, work)
            names = [info.filename for info in zf.infolist() if not info.is_dir()]
            for suffix, name in _book_members(names).items():
                with zf.open(name) as stream:
                    _copy_capped(stream, work / f"book{suffix}", budget, cancel_check)
                parts[suffix] = work / f"book{suffix}"
    except zipfile.BadZipFile as exc:
        raise SetupError(f"Not a StarDict dictionary (no .ifo file, tar or zip): {source.name}") from exc
    return parts, {STARDICT_ZIP: source}


def _read_part(
    parts: dict[str, Path],
    plain: str,
    packed: str,
    work: Path,
    budget: _Budget,
    cancel_check: Callable[[], bool] | None,
) -> Path | None:
    """The uncompressed file of one part, inflating its gzip form under the budget."""
    if packed in parts:
        out = work / f"inflated{plain}"
        try:
            with gzip.open(parts[packed], "rb") as src:
                _copy_capped(src, out, budget, cancel_check)
        except (OSError, EOFError) as exc:
            raise SetupError(f"Corrupt StarDict file {parts[packed].name}: {exc}") from exc
        return out
    return parts.get(plain)


def _parse_ifo(path: Path) -> dict[str, str]:
    text = path.read_bytes().decode("utf-8", errors="replace").lstrip("\ufeff")
    lines = text.splitlines()
    if not lines or lines[0].strip() != _IFO_MAGIC:
        raise SetupError(f"{path.name} is not a StarDict .ifo file")
    info: dict[str, str] = {}
    for line in lines[1:]:
        key, sep, value = line.partition("=")
        if sep:
            info[key.strip()] = value.strip()
    if not info.get("bookname"):
        raise SetupError("The StarDict .ifo file has no bookname")
    return info


def _parse_idx(data: bytes, offset_bits: int) -> list[tuple[str, int, int]]:
    fmt = ">QI" if offset_bits == 64 else ">II"
    width = struct.calcsize(fmt)
    entries: list[tuple[str, int, int]] = []
    pos = 0
    while pos < len(data):
        end = data.find(b"\0", pos)
        if end < 0 or end + 1 + width > len(data):
            raise SetupError("The StarDict index is truncated")
        word = data[pos:end].decode("utf-8", errors="replace")
        offset, size = struct.unpack_from(fmt, data, end + 1)
        entries.append((word, offset, size))
        pos = end + 1 + width
    return entries


def _parse_syn(data: bytes) -> list[tuple[str, int]]:
    synonyms: list[tuple[str, int]] = []
    pos = 0
    while pos < len(data):
        end = data.find(b"\0", pos)
        if end < 0 or end + 5 > len(data):
            raise SetupError("The StarDict synonym file is truncated")
        (index,) = struct.unpack_from(">I", data, end + 1)
        synonyms.append((data[pos:end].decode("utf-8", errors="replace"), index))
        pos = end + 5
    return synonyms


def _field_nodes(kind: str, payload: bytes) -> list[Any]:
    if kind in _MARKUP_TYPES:
        return markup_nodes(payload.decode("utf-8", errors="replace"))
    if kind in _TEXT_TYPES:
        return text_nodes(payload.decode("utf-8", errors="replace"))
    return []


def _render(blob: bytes, sametypesequence: str) -> str:
    """One rendered row from one entry's data; "" when it has no text field."""
    fields: list[list[Any]] = []
    if sametypesequence:
        pos = 0
        for i, kind in enumerate(sametypesequence):
            last = i == len(sametypesequence) - 1
            if kind.islower():
                end = len(blob) if last else blob.find(b"\0", pos)
                if end < 0:
                    end = len(blob)
                fields.append(_field_nodes(kind, blob[pos:end]))
                pos = end + 1
            else:
                if last:
                    size = len(blob) - pos
                    start = pos
                else:
                    if pos + 4 > len(blob):
                        break
                    (size,) = struct.unpack_from(">I", blob, pos)
                    start = pos + 4
                pos = start + size
        return glossary_row(fields)
    pos = 0
    while pos < len(blob):
        kind = chr(blob[pos])
        pos += 1
        if kind.islower():
            end = blob.find(b"\0", pos)
            if end < 0:
                end = len(blob)
            fields.append(_field_nodes(kind, blob[pos:end]))
            pos = end + 1
        else:
            if pos + 4 > len(blob):
                break
            (size,) = struct.unpack_from(">I", blob, pos)
            pos += 4 + size
    return glossary_row(fields)


def import_stardict(
    source: Path,
    dest_root: Path,
    *,
    progress: ProgressFn | None = None,
    cancel_check: Callable[[], bool] | None = None,
    overwrite: bool = False,
    dict_id: str | None = None,
    before_promote: Callable[[], None] | None = None,
    language: str = "ja",
) -> StarDictImportResult:
    """Import one StarDict book into ``dest_root/<dict_id>/index.sqlite``.

    ``source`` is a ``.ifo`` file (its ``.idx``/``.dict``/``.syn`` siblings
    beside it), a tar archive or a zip. ``dict_id`` pins the slot (catalog
    downloads, Re-import); otherwise it derives from the book name.
    """
    if not source.exists():
        raise SetupError(f"StarDict dictionary not found: {source}")
    if progress:
        progress(0, 0, "Reading dictionary")
    with tempfile.TemporaryDirectory(prefix="anki_miner_stardict_") as tmp:
        work = Path(tmp)
        budget = _Budget()
        try:
            parts, keep = _unpack(source, work, budget, cancel_check)
        except (tarfile.TarError, EOFError, OSError, lzma.LZMAError, zlib.error) as exc:
            raise SetupError(f"Corrupt StarDict archive {source.name}: {exc}") from exc
        info = _parse_ifo(parts[".ifo"])
        idx_path = _read_part(parts, ".idx", ".idx.gz", work, budget, cancel_check)
        dict_path = _read_part(parts, ".dict", ".dict.dz", work, budget, cancel_check)
        syn_path = _read_part(parts, ".syn", ".syn.dz", work, budget, cancel_check)
        if idx_path is None or dict_path is None:
            raise SetupError("The StarDict dictionary is missing its .idx or .dict file")
        offset_bits = 64 if info.get("idxoffsetbits") == "64" else 32
        entries = _parse_idx(idx_path.read_bytes(), offset_bits)
        synonyms = _parse_syn(syn_path.read_bytes()) if syn_path is not None else []
        title = info["bookname"]
        sametypesequence = info.get("sametypesequence", "")
        slot = dict_id or plain_dict_id(dest_root, title, language)
        log_summary(logger, "StarDict import", source=source, dict_id=slot, title=title, entries=len(entries))

        skipped = 0
        dict_size = dict_path.stat().st_size
        wanted = {index for _word, index in synonyms if 0 <= index < len(entries)}
        rendered: dict[int, str] = {}

        def rows() -> Iterator[DictRow]:
            nonlocal skipped
            with dict_path.open("rb") as data:
                for index, (word, offset, size) in enumerate(entries):
                    if index % 1000 == 0:
                        raise_if_cancelled(cancel_check)
                    if not word.strip() or offset + size > dict_size:
                        skipped += 1
                        continue
                    data.seek(offset)
                    content = _render(data.read(size), sametypesequence)
                    if not content:
                        skipped += 1
                        continue
                    if index in wanted:
                        rendered[index] = content
                    yield DictRow(term=word.strip(), reading=None, content=content)
            for word, index in synonyms:
                content = rendered.get(index, "")
                if word.strip() and content:
                    yield DictRow(term=word.strip(), reading=None, content=content)

        count = write_plain_index(
            rows(),
            dest_root=dest_root,
            dict_id=slot,
            title=title,
            format_name="stardict",
            language=language,
            keep=keep,
            overwrite=overwrite,
            expected_rows=len(entries) + len(synonyms),
            progress=progress,
            cancel_check=cancel_check,
            before_promote=before_promote,
        )
    if skipped:
        log_summary(logger, "StarDict rows skipped", level=logging.WARNING, dict_id=slot, count=skipped)
    log_summary(logger, "StarDict import done", dict_id=slot, entries=count, skipped=skipped)
    return StarDictImportResult(dict_id=slot, source_name=title, entry_count=count, skipped_malformed=skipped)


def repair_stardict(
    source: Path,
    dest_root: Path,
    *,
    dict_id: str,
    progress: ProgressFn | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> StarDictImportResult:
    """Rebuild one slot from its kept source, quarantining an invalid prior slot."""
    # Read before the rebuild: a quarantined slot would otherwise come back "ja".
    language = read_slot_language(dest_root / dict_id)
    return repair_managed_slot(
        source,
        dest_root,
        dict_id,
        "dictionary",
        lambda path, overwrite: import_stardict(
            path,
            dest_root,
            progress=progress,
            cancel_check=cancel_check,
            overwrite=overwrite,
            dict_id=dict_id,
            language=language,
        ),
    )
