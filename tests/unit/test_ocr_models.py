"""The shared OCR model store: per-call spec sets and the pinned recogniser table."""

from __future__ import annotations

import dataclasses
import re

from anki_miner.services.ocr import model_installer as mi
from anki_miner.services.ocr.recognizers import RECOGNIZERS

_PIN = re.compile(r"^https://www\.modelscope\.cn/models/RapidAI/RapidOCR/resolve/v3\.10\.0/onnx/PP-OCRv[56]/rec/")


def test_every_recogniser_is_tag_pinned_with_a_checksum_and_size():
    for key, spec in RECOGNIZERS.items():
        assert _PIN.match(spec.model.url), key
        assert re.fullmatch(r"[0-9a-f]{64}", spec.model.sha256), key
        assert spec.model.size_bytes > 1_000_000, key
        assert spec.model.url.endswith("/" + spec.model.filename), key


def test_recogniser_files_never_collide_with_meikiocr():
    names = [s.model.filename for s in RECOGNIZERS.values()] + [s.filename for s in mi.MEIKI_SPECS]
    assert len(names) == len(set(names))


def test_only_the_arabic_script_model_reads_right_to_left():
    assert {k for k, s in RECOGNIZERS.items() if s.reverse} == {"arabic"}


def test_is_installed_checks_only_the_specs_asked_for(tmp_path):
    spec = RECOGNIZERS["korean"].model
    (tmp_path / spec.filename).write_bytes(b"\0" * spec.size_bytes)
    assert mi.is_installed(tmp_path, (spec,))
    assert not mi.is_installed(tmp_path)  # meikiocr's pair is still missing


def test_install_models_downloads_only_the_given_specs(tmp_path, monkeypatch):
    fetched: list[str] = []
    spec = RECOGNIZERS["korean"].model

    def _fake_download(url, **kwargs):
        fetched.append(url)
        part = tmp_path / "x.part"
        part.write_bytes(b"\0" * spec.size_bytes)
        return part

    monkeypatch.setattr(mi, "download_to_temp", _fake_download)
    monkeypatch.setattr(mi, "verify_sha256", lambda *a, **k: None)
    mi.install_models(tmp_path, (spec,))
    assert fetched == [spec.url]


def test_missing_lists_the_specs_not_on_disk_at_their_exact_size(tmp_path):
    present, truncated, absent = (
        dataclasses.replace(mi.MEIKI_SPECS[0], filename=f"{name}.onnx", size_bytes=4)
        for name in ("present", "truncated", "absent")
    )
    (tmp_path / present.filename).write_bytes(b"onnx")
    (tmp_path / truncated.filename).write_bytes(b"onn")
    assert mi.missing(tmp_path, (present, truncated, absent)) == (truncated, absent)
    assert mi.missing(tmp_path, (present,)) == ()
