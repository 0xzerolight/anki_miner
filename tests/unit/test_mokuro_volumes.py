"""Tests for the Manga OCR volume scanner (mokuro's --parent_dir rule)."""

from __future__ import annotations

from pathlib import Path

from anki_miner.services.mokuro_volumes import MokuroVolume, mokuro_output_path, scan_volumes


def _img(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG")


def test_output_path_for_dir_is_sibling_with_dir_name(tmp_path):
    vol = tmp_path / "Vol 1"
    vol.mkdir()
    assert mokuro_output_path(vol) == tmp_path / "Vol 1.mokuro"


def test_output_path_for_archive_swaps_suffix(tmp_path):
    cbz = tmp_path / "Vol.1.cbz"
    cbz.write_bytes(b"PK")
    assert mokuro_output_path(cbz) == tmp_path / "Vol.1.mokuro"


def test_folder_with_images_directly_is_one_volume(tmp_path):
    _img(tmp_path / "001.jpg")
    _img(tmp_path / "002.PNG")
    assert scan_volumes(tmp_path) == [MokuroVolume(tmp_path, tmp_path.parent / f"{tmp_path.name}.mokuro", False)]


def test_series_folder_yields_child_dirs_and_archives_natural_sorted(tmp_path):
    _img(tmp_path / "vol10" / "p.jpg")
    _img(tmp_path / "vol2" / "nested" / "p.webp")
    (tmp_path / "vol3.cbz").write_bytes(b"PK")
    (tmp_path / "notes.txt").write_text("x")
    assert [v.source.name for v in scan_volumes(tmp_path)] == ["vol2", "vol3.cbz", "vol10"]


def test_ocr_cache_junk_and_imageless_dirs_are_skipped(tmp_path):
    _img(tmp_path / "_ocr" / "vol1" / "p.jpg")
    _img(tmp_path / "__MACOSX" / "p.jpg")
    (tmp_path / "empty").mkdir()
    _img(tmp_path / "vol1" / "p.jpg")
    assert [v.source.name for v in scan_volumes(tmp_path)] == ["vol1"]


def test_dir_and_same_stem_archive_are_one_volume(tmp_path):
    _img(tmp_path / "vol1" / "p.jpg")
    (tmp_path / "vol1.cbz").write_bytes(b"PK")
    vols = scan_volumes(tmp_path)
    assert [v.source.name for v in vols] == ["vol1"]
    assert vols[0].output == tmp_path / "vol1.mokuro"


def test_already_processed_reflects_existing_sidecar(tmp_path):
    _img(tmp_path / "vol1" / "p.jpg")
    (tmp_path / "vol1.mokuro").write_text("{}")
    assert scan_volumes(tmp_path)[0].already_processed is True


def test_empty_folder_yields_nothing(tmp_path):
    assert scan_volumes(tmp_path) == []
