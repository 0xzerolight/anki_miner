#!/usr/bin/env python3
"""Download and extract the exact UniDic tree used by Android goldens.

The golden runtime consumes UniDic as external data.  Building the
``unidic-lite`` sdist into a wheel is deliberately avoided: wheel metadata is
not reproducible across build environments, while the source archive and the
dictionary bytes are stable and independently hashable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import tarfile
import tempfile
import urllib.error
import urllib.request
from collections.abc import Iterable, Sequence
from pathlib import Path, PurePosixPath
from typing import BinaryIO

RESOURCE_ID = "unidic-lite-1.0.8"
ARCHIVE_URL = (
    "https://files.pythonhosted.org/packages/55/2b/8cf7514cb57d028abcef625afa847d60ff1ffbf0049c36b78faa7c35046f/"
    "unidic-lite-1.0.8.tar.gz"
)
ARCHIVE_SHA256 = "db9d4572d9fdd4d00a97949d4b0741ec480ee05a7e7e2e32f547500dae27b245"
ARCHIVE_SIZE_BYTES = 47_356_746
ARCHIVE_DICDIR_PREFIX = PurePosixPath("unidic-lite-1.0.8/unidic_lite/dicdir")
TREE_SHA256 = "bd942f1b395aa7c56fe20321dc7f021930e29107f6b2949a49f5c56caab55ea7"
TREE_FILE_COUNT = 19
TREE_SIZE_BYTES = 260_467_176


class UniDicPreparationError(RuntimeError):
    """The pinned resource could not be proven or safely extracted."""


def _sha256_stream(stream: BinaryIO) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(chunk)
        size += len(chunk)
    return digest.hexdigest(), size


def _sha256_file(path: Path) -> tuple[str, int]:
    try:
        entry_stat = path.lstat()
    except OSError as exc:
        raise UniDicPreparationError(f"cannot inspect file {path}: {exc}") from exc
    if not stat.S_ISREG(entry_stat.st_mode) or stat.S_ISLNK(entry_stat.st_mode):
        raise UniDicPreparationError(f"resource input is not a regular file: {path}")
    with path.open("rb") as handle:
        return _sha256_stream(handle)


def _iter_tree_files(root: Path) -> Iterable[Path]:
    try:
        root_stat = root.lstat()
    except OSError as exc:
        raise UniDicPreparationError(f"cannot inspect dictionary tree {root}: {exc}") from exc
    if not stat.S_ISDIR(root_stat.st_mode) or stat.S_ISLNK(root_stat.st_mode):
        raise UniDicPreparationError(f"dictionary tree is not a real directory: {root}")

    for current, directory_names, file_names in os.walk(root, followlinks=False):
        current_path = Path(current)
        directory_names.sort()
        file_names.sort()
        for name in directory_names:
            path = current_path / name
            entry_stat = path.lstat()
            if stat.S_ISLNK(entry_stat.st_mode) or not stat.S_ISDIR(entry_stat.st_mode):
                raise UniDicPreparationError(f"invalid dictionary directory entry: {path}")
        for name in file_names:
            path = current_path / name
            entry_stat = path.lstat()
            if stat.S_ISLNK(entry_stat.st_mode) or not stat.S_ISREG(entry_stat.st_mode):
                raise UniDicPreparationError(f"invalid dictionary file entry: {path}")
            yield path


def tree_identity(root: Path) -> dict[str, int | str]:
    digest = hashlib.sha256()
    file_count = 0
    size_bytes = 0
    for path in _iter_tree_files(root):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        content = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
        file_count += 1
        size_bytes += len(content)
    return {
        "sha256": digest.hexdigest(),
        "file_count": file_count,
        "size_bytes": size_bytes,
    }


def verify_tree(root: Path) -> dict[str, int | str]:
    identity = tree_identity(root)
    expected = {
        "sha256": TREE_SHA256,
        "file_count": TREE_FILE_COUNT,
        "size_bytes": TREE_SIZE_BYTES,
    }
    if identity != expected:
        raise UniDicPreparationError(
            "UniDic dictionary tree identity mismatch: "
            f"expected {json.dumps(expected, sort_keys=True)}, "
            f"got {json.dumps(identity, sort_keys=True)}"
        )
    return identity


def _verify_archive(path: Path) -> None:
    actual_sha256, actual_size = _sha256_file(path)
    if actual_size != ARCHIVE_SIZE_BYTES or actual_sha256 != ARCHIVE_SHA256:
        raise UniDicPreparationError(
            "UniDic archive identity mismatch: "
            f"expected size={ARCHIVE_SIZE_BYTES} sha256={ARCHIVE_SHA256}, "
            f"got size={actual_size} sha256={actual_sha256}"
        )


def _download_archive(path: Path) -> None:
    request = urllib.request.Request(
        ARCHIVE_URL,
        headers={"User-Agent": "anki-miner-golden-resource-preparer/1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response, path.open("xb") as output:
            if response.geturl() != ARCHIVE_URL:
                raise UniDicPreparationError("the pinned UniDic URL redirected")
            shutil.copyfileobj(response, output, length=1024 * 1024)
    except (OSError, urllib.error.URLError) as exc:
        raise UniDicPreparationError(f"could not download pinned UniDic archive: {exc}") from exc


def _safe_relative_member(name: str, prefix: PurePosixPath) -> PurePosixPath | None:
    member_path = PurePosixPath(name)
    if member_path.is_absolute() or ".." in member_path.parts:
        raise UniDicPreparationError(f"unsafe archive member path: {name!r}")
    try:
        relative = member_path.relative_to(prefix)
    except ValueError:
        return None
    if not relative.parts:
        return None
    return relative


def _extract_dicdir(archive: Path, output: Path) -> None:
    seen: set[PurePosixPath] = set()
    try:
        with tarfile.open(archive, mode="r:gz") as source:
            for member in source:
                relative = _safe_relative_member(member.name, ARCHIVE_DICDIR_PREFIX)
                if relative is None:
                    continue
                if relative in seen:
                    raise UniDicPreparationError(f"duplicate UniDic archive member: {member.name!r}")
                seen.add(relative)
                destination = output.joinpath(*relative.parts)
                if member.isdir():
                    destination.mkdir(parents=True, exist_ok=True)
                    continue
                if not member.isreg():
                    raise UniDicPreparationError(f"non-regular UniDic archive member: {member.name!r}")
                destination.parent.mkdir(parents=True, exist_ok=True)
                extracted = source.extractfile(member)
                if extracted is None:
                    raise UniDicPreparationError(f"could not read UniDic archive member: {member.name!r}")
                with extracted, destination.open("xb") as target:
                    shutil.copyfileobj(extracted, target, length=1024 * 1024)
    except (OSError, tarfile.TarError) as exc:
        raise UniDicPreparationError(f"could not extract pinned UniDic archive: {exc}") from exc


def prepare(destination: Path, *, archive: Path | None = None) -> dict[str, object]:
    destination = destination.expanduser().absolute()
    if destination.exists():
        tree = verify_tree(destination)
        return resource_record(tree)
    destination.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="unidic-lite-golden-", dir=destination.parent) as temporary:
        temporary_root = Path(temporary)
        archive_path = archive.expanduser().resolve() if archive is not None else temporary_root / "source.tar.gz"
        if archive is None:
            _download_archive(archive_path)
        _verify_archive(archive_path)
        staged = temporary_root / "dicdir"
        staged.mkdir()
        _extract_dicdir(archive_path, staged)
        tree = verify_tree(staged)
        try:
            os.replace(staged, destination)
        except OSError as exc:
            raise UniDicPreparationError(f"could not install verified UniDic tree: {exc}") from exc
    return resource_record(tree)


def resource_record(tree: dict[str, int | str]) -> dict[str, object]:
    return {
        "resource_id": RESOURCE_ID,
        "archive": {
            "url": ARCHIVE_URL,
            "sha256": ARCHIVE_SHA256,
            "size_bytes": ARCHIVE_SIZE_BYTES,
            "dicdir_prefix": ARCHIVE_DICDIR_PREFIX.as_posix(),
        },
        "tree": tree,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--archive", type=Path, help="verified local archive; omit to download the pinned URL")
    parser.add_argument("--check", type=Path, help="verify an existing dicdir instead of preparing one")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.check is not None:
            record = resource_record(verify_tree(args.check.expanduser().resolve()))
        else:
            record = prepare(args.destination, archive=args.archive)
    except UniDicPreparationError as exc:
        print(f"UniDic preparation failed: {exc}", file=os.sys.stderr)
        return 2
    print(json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
