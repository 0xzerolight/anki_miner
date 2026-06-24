"""Tests for anki_miner.services.asr.model_manager.

faster-whisper is intentionally NOT installed. The _engine seam is
monkeypatched so no real network calls or downloads occur.
"""

from __future__ import annotations

import threading

from anki_miner.services.asr import model_manager
from anki_miner.services.asr.model_manager import DEFAULT_MODEL, KNOWN_MODELS, download, is_downloaded

# ---------------------------------------------------------------------------
# KNOWN_MODELS / DEFAULT_MODEL constants
# ---------------------------------------------------------------------------


def test_known_models_contains_large_v3_and_small():
    assert "large-v3" in KNOWN_MODELS
    assert "small" in KNOWN_MODELS


def test_known_models_is_frozenset():
    assert isinstance(KNOWN_MODELS, frozenset)


def test_default_model_is_large_v3():
    assert DEFAULT_MODEL == "large-v3"


def test_default_model_in_known_models():
    assert DEFAULT_MODEL in KNOWN_MODELS


# ---------------------------------------------------------------------------
# is_downloaded — path-based checks (no real files needed, just tmp dirs)
# ---------------------------------------------------------------------------


def test_is_downloaded_false_when_models_root_missing(tmp_path):
    """Returns False when models_root doesn't exist at all."""
    missing = tmp_path / "nonexistent"
    assert is_downloaded("small", missing) is False


def test_is_downloaded_false_when_model_dir_empty(tmp_path):
    """Returns False when models_root exists but contains no model subdir."""
    assert is_downloaded("small", tmp_path) is False


def test_is_downloaded_false_when_model_bin_missing(tmp_path):
    """Returns False when a subdirectory exists but model.bin is absent."""
    subdir = tmp_path / "models--Systran--faster-whisper-small" / "snapshots" / "abc123"
    subdir.mkdir(parents=True)
    (subdir / "config.json").write_text("{}")
    (subdir / "tokenizer.json").write_text("{}")
    # No model.bin — should still be False
    assert is_downloaded("small", tmp_path) is False


def test_is_downloaded_true_when_model_bin_present(tmp_path):
    """Returns True when any subdirectory under models_root contains model.bin."""
    subdir = tmp_path / "models--Systran--faster-whisper-small" / "snapshots" / "abc123"
    subdir.mkdir(parents=True)
    (subdir / "model.bin").write_bytes(b"\x00")
    assert is_downloaded("small", tmp_path) is True


def test_is_downloaded_true_model_bin_directly_in_subdir(tmp_path):
    """Returns True even with a flat layout (model.bin one level under models_root)."""
    subdir = tmp_path / "faster-whisper-large-v3"
    subdir.mkdir()
    (subdir / "model.bin").write_bytes(b"\x00")
    assert is_downloaded("large-v3", tmp_path) is True


def test_is_downloaded_false_when_only_model_bin_in_root(tmp_path):
    """Returns False when model.bin is at models_root level (not in a subdir)."""
    (tmp_path / "model.bin").write_bytes(b"\x00")
    assert is_downloaded("small", tmp_path) is False


def test_is_downloaded_name_aware_cross_model(tmp_path):
    """With only small's model.bin present, large-v3 is False and small is True."""
    # HF-cache layout: models--Systran--faster-whisper-small/snapshots/abc/model.bin
    small_snapshot = tmp_path / "models--Systran--faster-whisper-small" / "snapshots" / "abc123"
    small_snapshot.mkdir(parents=True)
    (small_snapshot / "model.bin").write_bytes(b"\x00")

    assert is_downloaded("small", tmp_path) is True
    assert is_downloaded("large-v3", tmp_path) is False


# ---------------------------------------------------------------------------
# download — delegates to _engine.get_download_fn()
# ---------------------------------------------------------------------------


def test_download_calls_download_fn_with_correct_args(monkeypatch, tmp_path):
    """download() must call get_download_fn()(name, download_root=models_root)."""
    calls = []

    def fake_download_fn(name, *, download_root):
        calls.append({"name": name, "download_root": download_root})

    monkeypatch.setattr(model_manager._engine, "get_download_fn", lambda: fake_download_fn)

    download("small", tmp_path)

    assert len(calls) == 1
    assert calls[0]["name"] == "small"
    assert calls[0]["download_root"] == tmp_path


def test_download_creates_models_root_if_missing(monkeypatch, tmp_path):
    """download() must create models_root before calling the download function."""
    new_root = tmp_path / "new_dir" / "deeper"
    calls = []

    def fake_download_fn(name, *, download_root):
        calls.append(download_root)

    monkeypatch.setattr(model_manager._engine, "get_download_fn", lambda: fake_download_fn)

    download("large-v3", new_root)

    assert new_root.exists()
    assert len(calls) == 1


def test_download_passes_model_name_unchanged(monkeypatch, tmp_path):
    """The model name passed to the download fn must match the argument exactly."""
    received = {}

    def fake_download_fn(name, *, download_root):
        received["name"] = name

    monkeypatch.setattr(model_manager._engine, "get_download_fn", lambda: fake_download_fn)
    download("large-v3", tmp_path)
    assert received["name"] == "large-v3"


def test_download_with_cancel_event_not_set(monkeypatch, tmp_path):
    """download() with a cancel_event that is NOT set proceeds normally."""
    cancel = threading.Event()
    called = []

    def fake_download_fn(name, *, download_root):
        called.append(True)

    monkeypatch.setattr(model_manager._engine, "get_download_fn", lambda: fake_download_fn)
    download("small", tmp_path, cancel_event=cancel)
    assert called


def test_download_skips_if_cancel_event_already_set(monkeypatch, tmp_path):
    """download() must not call the download fn when cancel_event is pre-set."""
    cancel = threading.Event()
    cancel.set()
    called = []

    def fake_download_fn(name, *, download_root):
        called.append(True)

    monkeypatch.setattr(model_manager._engine, "get_download_fn", lambda: fake_download_fn)
    download("small", tmp_path, cancel_event=cancel)
    assert not called
