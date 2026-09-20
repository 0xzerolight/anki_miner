"""``ArtifactSpec(kind="zip")``: a flat data archive extracts like a wheel, and its payload digests are enforced.

The CAMeL morphology zip is the case (``languages/ar/pack.py``): GitHub serves bytes whose digest differs
from the publisher's own catalogue, so the pack pins the served zip AND the file inside it.
"""

from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

import pytest

from anki_miner.exceptions import SetupError
from anki_miner.languages.pack_spec import ArtifactSpec, PackComponent
from anki_miner.services.pack_installer import _extract_component, component_complete

PAYLOAD = b"###DEFINES###\n"


def _archive(tmp_path: Path) -> Path:
    archive = tmp_path / "zz_db-1.0.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("LICENSE", "GPL-2")
        zf.writestr("morphology.db", PAYLOAD)
    return archive


def _component(inner: tuple[tuple[str, str], ...]) -> tuple[PackComponent, ArtifactSpec]:
    spec = ArtifactSpec(
        url="https://example.invalid/zz_db-1.0.zip",
        sha256="0" * 64,
        kind="zip",
        member_prefix="",
        inner_sha256=inner,
    )
    component = PackComponent(
        import_name="zz_db", required=True, sentinels=("morphology.db", "LICENSE"), universal=spec
    )
    return component, spec


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "pack"
    root.mkdir()
    return root


def test_a_flat_zip_extracts_into_the_component_dir(tmp_path):
    comp, spec = _component((("morphology.db", hashlib.sha256(PAYLOAD).hexdigest()),))
    root = _root(tmp_path)

    _extract_component(_archive(tmp_path), root, comp, spec)

    assert component_complete(root, comp)
    assert (root / "zz_db" / "morphology.db").read_bytes() == PAYLOAD
    assert (root / "zz_db" / "LICENSE").read_text(encoding="utf-8") == "GPL-2"


def test_an_inner_digest_mismatch_refuses_and_promotes_nothing(tmp_path):
    comp, spec = _component((("morphology.db", "f" * 64),))
    root = _root(tmp_path)

    with pytest.raises(SetupError, match="zz_db morphology.db checksum mismatch"):
        _extract_component(_archive(tmp_path), root, comp, spec)

    assert not (root / "zz_db").exists()


def test_a_pinned_inner_file_the_archive_lacks_is_reported_missing(tmp_path):
    comp, spec = _component((("other.db", "0" * 64),))

    with pytest.raises(SetupError, match="missing other.db"):
        _extract_component(_archive(tmp_path), _root(tmp_path), comp, spec)


def test_the_existing_kinds_pin_no_inner_file():
    assert ArtifactSpec(url="u", sha256="0" * 64, kind="wheel", member_prefix="x/").inner_sha256 == ()
