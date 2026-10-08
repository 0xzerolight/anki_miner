"""Pinned OCR model install (no network) and the runtime/engine helpers."""

from __future__ import annotations

import dataclasses
import hashlib
import threading
from pathlib import Path

import pytest

from anki_miner.exceptions import SetupError
from anki_miner.services.asr import onnx_pack_installer
from anki_miner.services.video_ocr import model_installer as mi
from anki_miner.services.video_ocr import runtime
from tests.unit._resume_key_assert import assert_stable_resume_key

_PAYLOAD = b"onnx-fake-model"


@pytest.fixture
def fake_specs(monkeypatch):
    specs = tuple(
        dataclasses.replace(s, sha256=hashlib.sha256(_PAYLOAD).hexdigest(), size_bytes=len(_PAYLOAD))
        for s in mi.MODEL_SPECS
    )
    monkeypatch.setattr(mi, "MODEL_SPECS", specs)
    return specs


def _patch_download(monkeypatch, payload=_PAYLOAD, on_call=None):
    calls: list[str] = []

    def fake(url, *, dest_dir, progress=None, cancelled_check=None, max_bytes=None, resume_key=None, resume_root=None):
        assert_stable_resume_key(resume_key)
        calls.append(url)
        if on_call is not None:
            on_call()
        dest_dir.mkdir(parents=True, exist_ok=True)
        part = dest_dir / f"{len(calls)}.part"
        part.write_bytes(payload)
        return part

    monkeypatch.setattr(mi, "download_to_temp", fake)
    return calls


def test_specs_pin_hugging_face_commits():
    for spec in mi.MODEL_SPECS:
        assert "/resolve/main/" not in spec.url
        assert spec.url.endswith("/" + spec.filename)
        assert len(spec.sha256) == 64


def test_install_downloads_verifies_and_promotes_both(monkeypatch, tmp_path, fake_specs):
    calls = _patch_download(monkeypatch)
    assert not mi.is_installed(tmp_path)
    mi.install_models(tmp_path)
    assert len(calls) == 2
    assert mi.is_installed(tmp_path)
    assert list(tmp_path.glob("*.part")) == []


def test_installed_models_are_not_downloaded_again(monkeypatch, tmp_path, fake_specs):
    _patch_download(monkeypatch)
    mi.install_models(tmp_path)
    calls = _patch_download(monkeypatch)
    mi.install_models(tmp_path)
    assert calls == []


def test_checksum_mismatch_promotes_nothing(monkeypatch, tmp_path, fake_specs):
    _patch_download(monkeypatch, payload=b"tampered-bytes")
    with pytest.raises(SetupError, match="checksum"):
        mi.install_models(tmp_path)
    assert not mi.is_installed(tmp_path)
    assert list(tmp_path.glob("*.part")) == []


def test_cancel_during_download_promotes_nothing(monkeypatch, tmp_path, fake_specs):
    event = threading.Event()
    _patch_download(monkeypatch, on_call=event.set)
    with pytest.raises(SetupError, match="cancel"):
        mi.install_models(tmp_path, cancel_event=event)
    assert not mi.is_installed(tmp_path)


def test_a_truncated_model_counts_as_missing(tmp_path, fake_specs):
    for spec in fake_specs:
        (tmp_path / spec.filename).write_bytes(_PAYLOAD[:-1])
    assert not mi.is_installed(tmp_path)


def test_onnxruntime_importable_appends_the_pack_root(monkeypatch, tmp_path):
    (tmp_path / "onnxruntime").mkdir()
    (tmp_path / "onnxruntime" / "__init__.py").write_text("")
    monkeypatch.setattr(onnx_pack_installer.importlib.util, "find_spec", lambda name: None)
    monkeypatch.setattr(onnx_pack_installer.sys, "path", list(onnx_pack_installer.sys.path))
    onnx_pack_installer.onnxruntime_importable(tmp_path)
    assert str(tmp_path) in onnx_pack_installer.sys.path


def test_get_engine_loads_once_per_models_root(monkeypatch, tmp_path):
    from anki_miner.services.video_ocr import meiki_engine

    runtime._clear_cache()
    loads: list[Path] = []
    monkeypatch.setattr(meiki_engine, "load_engine", lambda root: loads.append(root) or object())
    first = runtime.get_engine(tmp_path, tmp_path / "a")
    assert runtime.get_engine(tmp_path, tmp_path / "a") is first
    runtime.get_engine(tmp_path, tmp_path / "b")
    assert loads == [tmp_path / "a", tmp_path / "b"]
    runtime._clear_cache()
