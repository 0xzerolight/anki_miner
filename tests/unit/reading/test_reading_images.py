"""Tests for the reading-tab card-image materializer (``images.py``)."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from PIL import Image

from anki_miner.exceptions import SetupError
from anki_miner.models.reading import ImageRef
from anki_miner.services.reading.images import (
    _MAX_EDGE,
    ReadingImageMemberError,
    prepare_card_image,
    validate_card_image,
)
from anki_miner.utils import pil_limits


def _make_image(path: Path, size: tuple[int, int], mode: str = "RGB", fmt: str = "PNG") -> Path:
    """Write a tiny real image of ``size``/``mode`` to ``path`` and return it."""
    Image.new(mode, size).save(path, fmt)
    return path


def _zip_with(path: Path, members: dict[str, Path]) -> Path:
    """Build a zip at ``path`` mapping arcname -> on-disk source file."""
    with zipfile.ZipFile(path, "w") as zf:
        for arcname, src in members.items():
            zf.write(src, arcname)
    return path


def test_downscale_large_image_long_edge_capped(tmp_path: Path) -> None:
    src = _make_image(tmp_path / "big.png", (2000, 1000))
    out = prepare_card_image(ImageRef(src), tmp_path / "out")
    with Image.open(out) as img:
        assert max(img.size) == _MAX_EDGE
        assert img.size == (1280, 640)


def test_oversized_image_or_text_file_fails_cleanly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = _make_image(tmp_path / "oversized.png", (3, 2))
    monkeypatch.setattr(pil_limits, "MAX_IMAGE_PIXELS", 4)

    with pytest.raises(ValueError, match=r"6 pixels.*cap 4"):
        prepare_card_image(ImageRef(src), tmp_path / "out")


def test_never_upscale_small_image_keeps_size(tmp_path: Path) -> None:
    src = _make_image(tmp_path / "small.png", (100, 50))
    out = prepare_card_image(ImageRef(src), tmp_path / "out")
    with Image.open(out) as img:
        assert img.size == (100, 50)


def test_output_is_rgb_jpeg(tmp_path: Path) -> None:
    src = _make_image(tmp_path / "p.png", (64, 64))
    out = prepare_card_image(ImageRef(src), tmp_path / "out")
    assert out.suffix == ".jpg"
    with Image.open(out) as img:
        assert img.format == "JPEG"
        assert img.mode == "RGB"


def test_rgba_converted_to_rgb(tmp_path: Path) -> None:
    src = _make_image(tmp_path / "rgba.png", (32, 32), mode="RGBA")
    out = prepare_card_image(ImageRef(src), tmp_path / "out")
    with Image.open(out) as img:
        assert img.mode == "RGB"


def test_palette_converted_to_rgb(tmp_path: Path) -> None:
    src = _make_image(tmp_path / "pal.png", (32, 32), mode="P")
    out = prepare_card_image(ImageRef(src), tmp_path / "out")
    with Image.open(out) as img:
        assert img.mode == "RGB"


def test_deterministic_name_same_ref(tmp_path: Path) -> None:
    src = _make_image(tmp_path / "d.png", (40, 40))
    ref = ImageRef(src)
    dest = tmp_path / "out"
    first = prepare_card_image(ref, dest)
    second = prepare_card_image(ref, dest)
    assert first == second
    assert first.parent == dest
    assert first.name.startswith("reading_")


def test_memoize_no_reencode(tmp_path: Path) -> None:
    src = _make_image(tmp_path / "m.png", (40, 40))
    ref = ImageRef(src)
    dest = tmp_path / "out"
    out = prepare_card_image(ref, dest)
    # Tamper with the materialized file; a second call must short-circuit on the
    # existing path and NOT re-encode (which would clobber the sentinel bytes).
    out.write_bytes(b"SENTINEL")
    again = prepare_card_image(ref, dest)
    assert again == out
    assert out.read_bytes() == b"SENTINEL"


def test_archive_materialization(tmp_path: Path) -> None:
    page = _make_image(tmp_path / "page.png", (200, 100))
    archive = _zip_with(tmp_path / "vol.cbz", {"page01.png": page})
    out = prepare_card_image(ImageRef(archive, "page01.png"), tmp_path / "out")
    with Image.open(out) as img:
        assert img.format == "JPEG"
        assert img.mode == "RGB"
        assert img.size == (200, 100)


def test_member_fault_normalized_but_memory_error_propagates(tmp_path: Path, monkeypatch) -> None:
    page = _make_image(tmp_path / "page.png", (50, 50))
    archive = _zip_with(tmp_path / "vol.cbz", {"page01.png": page})

    def _not_implemented(*_args, **_kwargs):
        raise NotImplementedError

    monkeypatch.setattr(zipfile.ZipFile, "open", _not_implemented)
    with pytest.raises(ReadingImageMemberError):
        prepare_card_image(ImageRef(archive, "page01.png"), tmp_path / "out")

    def _memory_error(*_args, **_kwargs):
        raise MemoryError

    monkeypatch.setattr(zipfile.ZipFile, "open", _memory_error)
    with pytest.raises(MemoryError):
        prepare_card_image(ImageRef(archive, "page01.png"), tmp_path / "out")


def test_validate_zip_safe_invoked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    page = _make_image(tmp_path / "page.png", (50, 50))
    archive = _zip_with(tmp_path / "vol.cbz", {"page01.png": page})

    calls: list[tuple] = []
    from anki_miner.services.reading import images as images_mod

    real = images_mod.validate_zip_safe

    def _spy(zf: zipfile.ZipFile, tmp_root: Path) -> None:
        calls.append((zf, tmp_root))
        real(zf, tmp_root)

    monkeypatch.setattr(images_mod, "validate_zip_safe", _spy)
    prepare_card_image(ImageRef(archive, "page01.png"), tmp_path / "out")
    assert len(calls) == 1


def test_cbz_page_access_does_not_rescan_archive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    page = _make_image(tmp_path / "page.png", (50, 50))
    archive = _zip_with(tmp_path / "vol.cbz", {"page01.png": page, "page02.png": page})

    calls = 0
    from anki_miner.services.reading import images as images_mod

    real = images_mod.validate_zip_safe

    def _spy(zf: zipfile.ZipFile, tmp_root: Path) -> None:
        nonlocal calls
        calls += 1
        real(zf, tmp_root)

    monkeypatch.setattr(images_mod, "validate_zip_safe", _spy)
    prepare_card_image(ImageRef(archive, "page01.png"), tmp_path / "out")
    prepare_card_image(ImageRef(archive, "page02.png"), tmp_path / "out")

    assert calls == 1


def test_archive_handle_reused_across_calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A shared ``archive_handles`` map opens the archive once for many pages."""
    page = _make_image(tmp_path / "page.png", (50, 50))
    archive = _zip_with(tmp_path / "vol.cbz", {"page01.png": page, "page02.png": page, "page03.png": page})

    opens = 0
    real_init = zipfile.ZipFile.__init__

    def _counting_init(self, *args, **kwargs):
        nonlocal opens
        opens += 1
        return real_init(self, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "__init__", _counting_init)

    handles: dict[Path, zipfile.ZipFile] = {}
    dest = tmp_path / "out"
    prepare_card_image(ImageRef(archive, "page01.png"), dest, handles)
    prepare_card_image(ImageRef(archive, "page02.png"), dest, handles)
    prepare_card_image(ImageRef(archive, "page03.png"), dest, handles)

    assert opens == 1
    assert list(handles) == [archive]
    handles[archive].close()


def test_validate_zip_safe_raise_propagates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    page = _make_image(tmp_path / "page.png", (50, 50))
    archive = _zip_with(tmp_path / "vol.cbz", {"page01.png": page})

    from anki_miner.services.reading import images as images_mod

    def _boom(zf: zipfile.ZipFile, tmp_root: Path) -> None:
        raise SetupError("nope")

    monkeypatch.setattr(images_mod, "validate_zip_safe", _boom)
    with pytest.raises(SetupError):
        prepare_card_image(ImageRef(archive, "page01.png"), tmp_path / "out")


def test_malicious_namelist_rejected(tmp_path: Path) -> None:
    page = _make_image(tmp_path / "page.png", (50, 50))
    archive = tmp_path / "evil.cbz"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.write(page, "../evil.png")
    with pytest.raises(SetupError):
        prepare_card_image(ImageRef(archive, "../evil.png"), tmp_path / "out")


def test_controlled_dest_name_ignores_entry(tmp_path: Path) -> None:
    # A valid but deeply nested entry name must not leak into the output path:
    # the file lands flat in dest_dir under a hash-derived name.
    page = _make_image(tmp_path / "page.png", (50, 50))
    archive = _zip_with(tmp_path / "vol.cbz", {"nested/deep/page01.png": page})
    dest = tmp_path / "out"
    out = prepare_card_image(ImageRef(archive, "nested/deep/page01.png"), dest)
    assert out.parent == dest
    assert out.name.startswith("reading_")
    assert out.suffix == ".jpg"
    assert not (dest / "nested").exists()
    # Nothing escaped dest_dir.
    assert list(dest.iterdir()) == [out]


def test_validate_card_image_accepts_a_real_image(tmp_path: Path) -> None:
    assert validate_card_image(_make_image(tmp_path / "ok.png", (32, 32))) is True


def test_validate_card_image_rejects_a_non_image(tmp_path: Path) -> None:
    path = tmp_path / "nope.png"
    path.write_text("this is not a png")
    assert validate_card_image(path) is False


def test_validate_card_image_rejects_a_missing_file(tmp_path: Path) -> None:
    assert validate_card_image(tmp_path / "gone.png") is False


def test_validate_card_image_rejects_a_decompression_bomb(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = _make_image(tmp_path / "bomb.png", (3, 2))
    monkeypatch.setattr(pil_limits, "MAX_IMAGE_PIXELS", 4)
    assert validate_card_image(src) is False
