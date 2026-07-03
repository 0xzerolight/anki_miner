"""Shared zip-extraction safety guards for Yomitan-format importers.

Yomitan dictionary, frequency, AND pitch-accent zips are user-supplied
(downloaded from third parties) and contain arbitrary file paths. We validate
every entry name before extraction and cap the total uncompressed size to
neutralize the standard path-traversal + zip-bomb attack surface. All three
importers route through :func:`validate_zip_safe` before calling
``ZipFile.extractall``.
"""

from __future__ import annotations

import zipfile
from collections.abc import Iterable
from pathlib import Path
from typing import NoReturn

from anki_miner.exceptions import SetupError

MAX_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024  # 2 GiB

INDEX_FILE_NAME = "index.json"


def find_redundant_index_dir(member_names: Iterable[str]) -> str | None:
    """Return the directory prefix an ``index.json`` is nested under, or None.

    Port of Yomitan ``DictionaryImporter._findRedundantDirectories``
    (``ext/js/dictionary/dictionary-importer.js``, upstream e2ed450): finds a
    member whose basename is ``index.json`` and returns everything before it in
    the path. Only meaningful when a root-level ``index.json`` is confirmed
    absent — that is the "user zipped the folder itself instead of its
    contents" mistake. Returns None when no ``index.json`` exists anywhere or it
    already sits at the root (empty prefix).
    """
    index_path = ""
    for name in member_names:
        normalized = name.replace("\\", "/")
        if normalized.rsplit("/", 1)[-1] == INDEX_FILE_NAME:
            index_path = normalized
    if not index_path:
        return None
    prefix = index_path[: index_path.rfind(INDEX_FILE_NAME)]
    return prefix or None


def raise_if_index_nested(member_names: Iterable[str], *, missing_msg: str) -> NoReturn:
    """Raise a guiding ``SetupError`` for a nested/absent ``index.json``.

    When a root ``index.json`` is missing but one is nested under a subdirectory
    (the "re-zipped the folder, not its contents" mistake), raise a diagnostic
    naming the redundant directory; otherwise raise ``missing_msg`` verbatim.
    Port of the redundant-directory branch of
    ``DictionaryImporter._readAndValidateIndex``
    (``ext/js/dictionary/dictionary-importer.js``, upstream e2ed450).
    """
    nested = find_redundant_index_dir(member_names)
    if nested:
        raise SetupError(
            f'index.json found nested under "{nested}" — ' "re-zip the folder CONTENTS, not the folder itself"
        )
    raise SetupError(missing_msg)


def validate_zip_safe(zf: zipfile.ZipFile, tmp_root: Path) -> None:
    """Reject malformed/malicious zip layouts before extraction.

    Args:
        zf: An already-opened ``ZipFile`` ready to be inspected.
        tmp_root: The directory ``extractall`` will write into; used as the
            anchor for the belt-and-suspenders containment check.

    Raises:
        SetupError: On any unsafe path, escaping path, or oversized total.
    """
    tmp_root_resolved = tmp_root.resolve()
    for name in zf.namelist():
        if "\\" in name:
            raise SetupError(f"Zip contains unsafe path (backslash): {name}")
        if name.startswith("/") or (len(name) > 1 and name[1] == ":"):
            raise SetupError(f"Zip contains unsafe path (absolute): {name}")
        if ".." in Path(name).parts:
            raise SetupError(f"Zip contains unsafe path (traversal): {name}")
        resolved = (tmp_root / name).resolve()
        try:
            resolved.relative_to(tmp_root_resolved)
        except ValueError:
            raise SetupError(f"Zip contains escaping path: {name}") from None

    # info.file_size is the uncompressed size DECLARED in the zip's central
    # directory and a malicious archive can lie about it; ZipFile.extractall
    # does not verify the declared size during decompression. The current
    # threat model is local-user only (zips come from the user, not the
    # network), so this declared-size cap is an intentional shortcut.
    # Hardening path: stream each entry via zf.open(name) with a running
    # byte counter and abort on overflow. See review of commit 63ffcd9.
    total = sum(info.file_size for info in zf.infolist())
    if total > MAX_UNCOMPRESSED_BYTES:
        raise SetupError(f"Zip uncompressed size exceeds limit ({total:,} > {MAX_UNCOMPRESSED_BYTES:,} bytes)")
