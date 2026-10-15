"""Build minimal StarDict books (loose files, tar, zip) for tests.

Layout per StarDict's ``doc/StarDictFileFormat``: ``.ifo`` key=value text,
``.idx`` entries ``word\\0 + offset + size`` (big-endian, 32-bit offset unless
``idxoffsetbits=64``), ``.dict`` the concatenated entry data, optional ``.syn``
entries ``synonym\\0 + uint32 index into .idx``.
"""

from __future__ import annotations

import gzip
import struct
import tarfile
import zipfile
from pathlib import Path

DEFAULT_ENTRIES: list[tuple[str, bytes]] = [
    ("casa", b"<b>house</b><br>home"),
    ("perro", b"<i>noun</i> dog"),
]


def build_stardict_files(
    folder: Path,
    *,
    name: str = "test-book",
    bookname: str = "Test StarDict",
    entries: list[tuple[str, bytes]] | None = None,
    sametypesequence: str | None = "h",
    synonyms: list[tuple[str, int]] | None = None,
    compressed: bool = True,
    offset_bits: int = 32,
) -> Path:
    """Write one book into ``folder``; return its ``.ifo`` path.

    ``compressed`` writes ``.idx.gz``/``.dict.dz`` (FreeDict's form), else
    plain ``.idx``/``.dict``. ``sametypesequence=None`` writes per-field type
    bytes into the data instead of declaring one sequence.
    """
    entries = DEFAULT_ENTRIES if entries is None else entries
    folder.mkdir(parents=True, exist_ok=True)
    data = b""
    idx = b""
    offset_fmt = ">Q" if offset_bits == 64 else ">I"
    for word, blob in entries:
        idx += word.encode("utf-8") + b"\0" + struct.pack(offset_fmt, len(data)) + struct.pack(">I", len(blob))
        data += blob
    if compressed:
        (folder / f"{name}.idx.gz").write_bytes(gzip.compress(idx))
        (folder / f"{name}.dict.dz").write_bytes(gzip.compress(data))
    else:
        (folder / f"{name}.idx").write_bytes(idx)
        (folder / f"{name}.dict").write_bytes(data)
    if synonyms:
        syn = b"".join(word.encode("utf-8") + b"\0" + struct.pack(">I", index) for word, index in synonyms)
        (folder / f"{name}.syn").write_bytes(syn)
    lines = [
        "StarDict's dict ifo file",
        "version=3.0.0" if offset_bits == 64 else "version=2.4.2",
        f"bookname={bookname}",
        f"wordcount={len(entries)}",
        f"idxfilesize={len(idx)}",
    ]
    if offset_bits == 64:
        lines.append("idxoffsetbits=64")
    if sametypesequence:
        lines.append(f"sametypesequence={sametypesequence}")
    if synonyms:
        lines.append(f"synwordcount={len(synonyms)}")
    ifo = folder / f"{name}.ifo"
    ifo.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return ifo


def build_stardict_tar(archive: Path, *, mode: str = "w:xz", **kwargs) -> Path:
    """Write a FreeDict-shaped tarball (``<name>/<name>.ifo`` …) at ``archive``; return it."""
    staging = archive.parent / f"{archive.name}.files"
    ifo = build_stardict_files(staging / kwargs.get("name", "test-book"), **kwargs)
    with tarfile.open(archive, mode) as tf:
        for path in sorted(ifo.parent.iterdir()):
            tf.add(path, arcname=f"{ifo.parent.name}/{path.name}")
    return archive


def build_stardict_zip(archive: Path, **kwargs) -> Path:
    """Write the book's files into a zip at ``archive``; return it."""
    staging = archive.parent / f"{archive.name}.files"
    ifo = build_stardict_files(staging / kwargs.get("name", "test-book"), **kwargs)
    with zipfile.ZipFile(archive, "w") as zf:
        for path in sorted(ifo.parent.iterdir()):
            zf.write(path, arcname=f"{ifo.parent.name}/{path.name}")
    return archive
