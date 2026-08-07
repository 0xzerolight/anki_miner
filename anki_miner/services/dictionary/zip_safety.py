"""Shared zip-extraction safety guards for Yomitan-format importers.

Yomitan dictionary, frequency, AND pitch-accent zips are user-supplied
(downloaded from third parties) and contain arbitrary file paths. We validate
every entry name before extraction and cap the total uncompressed size to
neutralize the standard path-traversal + zip-bomb attack surface. All three
importers route through :func:`validate_zip_safe` and then :func:`extract_members`.
"""

from __future__ import annotations

import logging
import zipfile
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import NoReturn

from anki_miner.exceptions import OperationCancelled, SetupError

logger = logging.getLogger(__name__)

MAX_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024  # 2 GiB

INDEX_FILE_NAME = "index.json"

# CPython's zipfile verifies each member against the crc-32 recorded in the
# central directory and raises BadZipFile with this text on a mismatch.
_BAD_CRC_MARKER = "Bad CRC-32"


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


def extract_members(
    zf: zipfile.ZipFile,
    dest: Path,
    *,
    cancel_check: Callable[[], bool] | None = None,
) -> list[str]:
    """Extract every member into ``dest``; return the names whose crc-32 lied.

    Yomitan dictionaries are published with wrong checksums often enough to
    matter, and nothing else in that ecosystem notices: Yomitan reads its zips
    through JSZip, which defaults to ``checkCRC32: false``. CPython does check,
    so an archive that imports fine everywhere else used to die here on
    "Corrupt zip file: Bad CRC-32 for file 'term_meta_bank_1.json'" with no way
    forward for the user.

    So: strict read first, and *only* on a checksum mismatch re-extract that one
    member with verification off. Everything else — a truncated archive, a bad
    header, an unsupported compression method — still raises. Giving up the
    checksum is affordable because it is not what protects the import: the JSON
    parse, the ``format`` gate, and the per-entry schema checks all still run,
    and they reject damage a checksum would only have flagged.

    Raises:
        OperationCancelled: If ``cancel_check`` returns True between members.
        zipfile.BadZipFile: On any structural problem (callers map this to a
            ``SetupError``).
    """
    mismatched: list[str] = []
    for member in zf.infolist():
        if cancel_check is not None and cancel_check():
            raise OperationCancelled("Import cancelled")
        try:
            zf.extract(member, dest)
        except zipfile.BadZipFile as exc:
            if _BAD_CRC_MARKER not in str(exc):
                raise
            _extract_unverified(zf, member, dest)
            mismatched.append(member.filename)
    log_checksum_mismatches(mismatched)
    return mismatched


def read_member(zf: zipfile.ZipFile, name: str, *, limit: int) -> bytes:
    """Read at most ``limit`` bytes of ``name``, tolerating a wrong crc-32.

    Bounded counterpart to :func:`extract_members` for the metadata-only probes
    that read ``index.json`` without extracting the archive. Reads ``limit + 1``
    bytes so the caller can still detect an under-declared size.
    """
    info = zf.getinfo(name)
    try:
        with zf.open(info) as fp:
            return fp.read(limit + 1)
    except zipfile.BadZipFile as exc:
        if _BAD_CRC_MARKER not in str(exc):
            raise
    log_checksum_mismatches([name])
    original_crc = info.CRC
    info.CRC = None  # type: ignore[assignment]  # ZipExtFile skips verification when unset
    try:
        with zf.open(info) as fp:
            return fp.read(limit + 1)
    finally:
        info.CRC = original_crc


def log_checksum_mismatches(names: list[str]) -> None:
    """Warn once about members that were extracted despite a bad crc-32."""
    if not names:
        return
    logger.warning(
        "Zip checksum mismatch: count=%d members=%s — extracted anyway (contents may be stale or altered)",
        len(names),
        ", ".join(names[:5]) + (", …" if len(names) > 5 else ""),
    )


def _extract_unverified(zf: zipfile.ZipFile, member: zipfile.ZipInfo, dest: Path) -> None:
    """Extract ``member`` with crc-32 verification suppressed.

    ``ZipExtFile`` reads the expected checksum off the ``ZipInfo`` it is handed
    and skips the comparison entirely when it is ``None``. Blanking the field
    for the duration of one extract is the only seam CPython offers; it is
    restored so nothing downstream sees a doctored entry.
    """
    original_crc = member.CRC
    member.CRC = None  # type: ignore[assignment]
    try:
        zf.extract(member, dest)
    finally:
        member.CRC = original_crc
