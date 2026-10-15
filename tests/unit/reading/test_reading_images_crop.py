"""F-11 (R4): a manga card picture cropped to its text block, keyed by (page, box)."""

from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

from PIL import Image

from anki_miner.models.reading import ImageRef
from anki_miner.services.reading.images import MANGA_CROP_SCALE, manga_crop_box, prepare_card_image


def _page(path: Path, size: tuple[int, int] = (800, 1000)) -> Path:
    Image.new("RGB", size, (255, 255, 255)).save(path, "PNG")
    return path


def test_the_box_grows_by_half_around_its_centre():
    assert MANGA_CROP_SCALE == 1.5
    assert manga_crop_box((100, 200, 300, 600), (800, 1000)) == (50, 100, 350, 700)


def test_the_crop_is_clamped_to_the_page():
    assert manga_crop_box((0, 0, 100, 100), (120, 110)) == (0, 0, 120, 110)


def test_a_degenerate_or_off_page_box_has_no_crop():
    assert manga_crop_box((50, 50, 50, 80), (1000, 1000)) is None
    assert manga_crop_box((2000, 2000, 2100, 2100), (1000, 1000)) is None


def test_prepare_crops_the_page_to_the_padded_box(tmp_path):
    src = _page(tmp_path / "p.png")

    out = prepare_card_image(ImageRef(src), tmp_path / "out", block_box=(100, 200, 300, 600))

    with Image.open(out) as img:
        assert img.size == (300, 600)


def test_two_boxes_on_one_page_get_two_files(tmp_path):
    src = _page(tmp_path / "p.png")

    first = prepare_card_image(ImageRef(src), tmp_path / "out", block_box=(100, 200, 300, 600))
    second = prepare_card_image(ImageRef(src), tmp_path / "out", block_box=(500, 100, 700, 300))

    assert first != second
    with Image.open(first) as img:
        assert img.size == (300, 600)
    with Image.open(second) as img:
        assert img.size == (300, 300)


def test_without_a_box_the_file_name_is_unchanged(tmp_path):
    src = _page(tmp_path / "p.png")

    out = prepare_card_image(ImageRef(src), tmp_path / "out")

    digest = hashlib.sha1(repr((str(src), None)).encode("utf-8")).hexdigest()[:12]
    assert out.name == f"reading_{digest}.jpg"
    with Image.open(out) as img:
        assert img.size == (800, 1000)


def test_a_box_on_an_archive_page_crops_too(tmp_path):
    src = _page(tmp_path / "p.png")
    archive = tmp_path / "vol.cbz"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.write(src, "p001.png")
    handles: dict[Path, zipfile.ZipFile] = {}

    try:
        out = prepare_card_image(
            ImageRef(archive, "p001.png"), tmp_path / "out", handles, block_box=(100, 200, 300, 600)
        )
    finally:
        for zf in handles.values():
            zf.close()

    with Image.open(out) as img:
        assert img.size == (300, 600)
