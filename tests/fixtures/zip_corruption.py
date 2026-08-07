"""Byte-level zip mutations for import-robustness tests.

Yomitan dictionaries in the wild are sometimes published with checksums that do
not match their (perfectly readable) contents — Yomitan's own JSZip reader
defaults to ``checkCRC32: false``, so nobody in that ecosystem notices. These
helpers reproduce that archive shape, plus the genuinely-damaged shape it must
still be told apart from.
"""

from __future__ import annotations

import struct
import zipfile
import zlib
from pathlib import Path

_LOCAL_SIG = b"PK\x03\x04"
_CENTRAL_SIG = b"PK\x01\x02"
# Offset of the crc-32 field within each header (APPNOTE 4.3.7 / 4.3.12).
_LOCAL_CRC_OFFSET = 14
_CENTRAL_CRC_OFFSET = 16
_CENTRAL_NAME_LEN_OFFSET = 28
_CENTRAL_NAME_OFFSET = 46


def corrupt_member_crc(zip_path: Path, member_name: str) -> None:
    """Rewrite ``member_name``'s recorded crc-32 to a wrong value, in place.

    Contents stay byte-for-byte valid; only the checksum lies. Patches both the
    local header and the central directory (CPython reads the central directory,
    the other is patched so the archive matches what a real bad writer emits).
    """
    with zipfile.ZipFile(zip_path) as zf:
        info = zf.getinfo(member_name)
    good = struct.pack("<I", info.CRC)
    bad = struct.pack("<I", info.CRC ^ 0xFFFFFFFF)

    data = bytearray(zip_path.read_bytes())

    local = info.header_offset
    assert data[local : local + 4] == _LOCAL_SIG, "not a local file header"
    assert data[local + _LOCAL_CRC_OFFSET : local + _LOCAL_CRC_OFFSET + 4] == good
    data[local + _LOCAL_CRC_OFFSET : local + _LOCAL_CRC_OFFSET + 4] = bad

    central = _find_central_entry(data, member_name)
    assert data[central + _CENTRAL_CRC_OFFSET : central + _CENTRAL_CRC_OFFSET + 4] == good
    data[central + _CENTRAL_CRC_OFFSET : central + _CENTRAL_CRC_OFFSET + 4] = bad

    zip_path.write_bytes(bytes(data))


def replace_member_payload(zip_path: Path, member_name: str, payload: bytes) -> None:
    """Swap ``member_name``'s data for ``payload`` while keeping the old checksum.

    The result is a *genuinely wrong* member: the deflate stream is well-formed
    (so no decompression error masks the outcome) but the bytes are not what the
    archive claims. Only the recorded crc-32 would ever catch it, which is
    exactly the guarantee a lenient extractor gives up.
    """
    with zipfile.ZipFile(zip_path) as zf:
        infos = zf.infolist()
        contents = {info.filename: zf.read(info.filename) for info in infos}
    crcs = {info.filename: info.CRC for info in infos}
    assert zlib.crc32(payload) != crcs[member_name], "payload must differ from the original"
    contents[member_name] = payload

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for info in infos:
            zf.writestr(info.filename, contents[info.filename])

    # Restore the ORIGINAL crc-32 for the swapped member so the archive still
    # advertises the pre-swap contents.
    data = bytearray(zip_path.read_bytes())
    stale = struct.pack("<I", crcs[member_name])
    with zipfile.ZipFile(zip_path) as zf:
        local = zf.getinfo(member_name).header_offset
    data[local + _LOCAL_CRC_OFFSET : local + _LOCAL_CRC_OFFSET + 4] = stale
    central = _find_central_entry(data, member_name)
    data[central + _CENTRAL_CRC_OFFSET : central + _CENTRAL_CRC_OFFSET + 4] = stale
    zip_path.write_bytes(bytes(data))


def _find_central_entry(data: bytearray, member_name: str) -> int:
    """Return the offset of ``member_name``'s central-directory record."""
    wanted = member_name.encode("utf-8")
    pos = data.find(_CENTRAL_SIG)
    while pos >= 0:
        name_len = struct.unpack_from("<H", data, pos + _CENTRAL_NAME_LEN_OFFSET)[0]
        start = pos + _CENTRAL_NAME_OFFSET
        if bytes(data[start : start + name_len]) == wanted:
            return pos
        pos = data.find(_CENTRAL_SIG, pos + 4)
    raise AssertionError(f"no central-directory record for {member_name!r}")
