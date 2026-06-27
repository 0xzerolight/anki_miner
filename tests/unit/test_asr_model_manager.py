"""Tests for anki_miner.services.asr.model_manager.

faster-whisper is intentionally NOT installed. The _engine seam is
monkeypatched so no real network calls or downloads occur.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

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


def _write_model(subdir, *, payload=b"\x00", with_config=True):
    """Write a (by default complete) model layout under *subdir*."""
    subdir.mkdir(parents=True, exist_ok=True)
    (subdir / "model.bin").write_bytes(payload)
    if with_config:
        (subdir / "config.json").write_text("{}")


def test_is_downloaded_true_when_model_bin_present(tmp_path):
    """Returns True when a complete model (model.bin + config.json) is present."""
    subdir = tmp_path / "models--Systran--faster-whisper-small" / "snapshots" / "abc123"
    _write_model(subdir)
    assert is_downloaded("small", tmp_path) is True


def test_is_downloaded_true_model_bin_directly_in_subdir(tmp_path):
    """Returns True even with a flat layout (model.bin one level under models_root)."""
    subdir = tmp_path / "faster-whisper-large-v3"
    _write_model(subdir)
    assert is_downloaded("large-v3", tmp_path) is True


def test_is_downloaded_false_when_config_sibling_missing(tmp_path):
    """A model.bin without its config.json sibling is an incomplete download."""
    subdir = tmp_path / "models--Systran--faster-whisper-small" / "snapshots" / "abc123"
    _write_model(subdir, with_config=False)
    assert is_downloaded("small", tmp_path) is False


def test_is_downloaded_false_when_model_bin_empty(tmp_path):
    """A zero-byte model.bin (truncated transfer) is not a complete model."""
    subdir = tmp_path / "models--Systran--faster-whisper-small" / "snapshots" / "abc123"
    _write_model(subdir, payload=b"")
    assert is_downloaded("small", tmp_path) is False


def test_is_downloaded_name_anchored_no_substring_false_positive(tmp_path):
    """'large' must not match a 'large-v3' directory (anchored name match, M5)."""
    subdir = tmp_path / "models--Systran--faster-whisper-large-v3" / "snapshots" / "abc"
    _write_model(subdir)
    assert is_downloaded("large-v3", tmp_path) is True
    assert is_downloaded("large", tmp_path) is False


def test_is_downloaded_false_when_only_model_bin_in_root(tmp_path):
    """Returns False when model.bin is at models_root level (not in a subdir)."""
    (tmp_path / "model.bin").write_bytes(b"\x00")
    assert is_downloaded("small", tmp_path) is False


def test_is_downloaded_name_aware_cross_model(tmp_path):
    """With only small's model.bin present, large-v3 is False and small is True."""
    # HF-cache layout: models--Systran--faster-whisper-small/snapshots/abc/model.bin
    small_snapshot = tmp_path / "models--Systran--faster-whisper-small" / "snapshots" / "abc123"
    _write_model(small_snapshot)

    assert is_downloaded("small", tmp_path) is True
    assert is_downloaded("large-v3", tmp_path) is False


# ---------------------------------------------------------------------------
# download — delegates to _engine.get_download_fn()
# ---------------------------------------------------------------------------


def _real_sig_download_fn(recorder):
    """A fake mirroring faster-whisper 1.2.1's ``download_model`` signature.

    Passing a kwarg the real function does not accept (e.g. the long-broken
    ``download_root``) raises ``TypeError`` here exactly as it would against the
    installed library — so these mocked tests can no longer hide a kwarg drift.
    """

    def fake(size_or_id, output_dir=None, local_files_only=False, cache_dir=None, revision=None, use_auth_token=None):
        recorder.append({"name": size_or_id, "cache_dir": cache_dir})
        return cache_dir

    return fake


def test_download_passes_cache_dir_not_download_root(monkeypatch, tmp_path):
    """Regression (faster-whisper 1.2.1): download() must call download_model with
    ``cache_dir`` — its real param — not the nonexistent ``download_root``.

    The fake uses the real signature, so the pre-fix ``download_root=`` call
    raises TypeError, reproducing the dead "Download model" button.
    """
    calls = []
    monkeypatch.setattr(model_manager._engine, "get_download_fn", lambda: _real_sig_download_fn(calls))

    download("small", tmp_path)

    assert len(calls) == 1
    assert calls[0]["name"] == "small"
    staging = Path(calls[0]["cache_dir"])
    assert staging.parent == tmp_path
    assert staging.name.startswith(".staging-small-")


@pytest.mark.asr
def test_real_download_model_accepts_our_kwargs():
    """The installed faster-whisper ``download_model`` must accept the kwargs
    model_manager passes. Guards against an upstream signature change that the
    mocked tests above (by design) cannot observe.
    """
    import inspect

    from anki_miner.services.asr import _engine

    inspect.signature(_engine.get_download_fn()).bind("small", cache_dir="/tmp/x")


def test_download_calls_download_fn_into_staging_under_models_root(monkeypatch, tmp_path):
    """download() fetches into a staging dir inside models_root, not models_root itself."""
    calls = []

    def fake_download_fn(name, *, cache_dir):
        calls.append({"name": name, "cache_dir": cache_dir})

    monkeypatch.setattr(model_manager._engine, "get_download_fn", lambda: fake_download_fn)

    download("small", tmp_path)

    assert len(calls) == 1
    assert calls[0]["name"] == "small"
    staging = Path(calls[0]["cache_dir"])
    # Staging lives under models_root (atomic same-filesystem promotion) and is
    # cleaned up afterwards.
    assert staging.parent == tmp_path
    assert staging.name.startswith(".staging-small-")
    assert not staging.exists()


def test_download_promotes_staged_model_into_models_root(monkeypatch, tmp_path):
    """A successful download moves the staged model tree into models_root."""

    def fake_download_fn(name, *, cache_dir):
        snap = Path(cache_dir) / f"models--Systran--faster-whisper-{name}" / "snapshots" / "rev"
        snap.mkdir(parents=True)
        (snap / "model.bin").write_bytes(b"\x00\x01")
        (snap / "config.json").write_text("{}")

    monkeypatch.setattr(model_manager._engine, "get_download_fn", lambda: fake_download_fn)

    download("small", tmp_path)

    assert is_downloaded("small", tmp_path) is True
    assert not any(p.name.startswith(".staging-") for p in tmp_path.iterdir())


def test_download_leaves_models_root_clean_on_failure(monkeypatch, tmp_path):
    """A download that raises mid-transfer must not leave a partial model behind."""

    def fake_download_fn(name, *, cache_dir):
        # Simulate a partial transfer then a failure.
        snap = Path(cache_dir) / f"models--Systran--faster-whisper-{name}" / "snapshots" / "rev"
        snap.mkdir(parents=True)
        (snap / "model.bin").write_bytes(b"\x00")  # truncated, no config.json
        raise RuntimeError("network drop")

    monkeypatch.setattr(model_manager._engine, "get_download_fn", lambda: fake_download_fn)

    with pytest.raises(RuntimeError):
        download("small", tmp_path)

    assert is_downloaded("small", tmp_path) is False
    assert list(tmp_path.iterdir()) == []  # staging cleaned, nothing promoted


def test_download_cancel_mid_transfer_promotes_nothing(monkeypatch, tmp_path):
    """A cancel set during the transfer discards the staged copy."""
    cancel = threading.Event()

    def fake_download_fn(name, *, cache_dir):
        snap = Path(cache_dir) / f"models--Systran--faster-whisper-{name}" / "snapshots" / "rev"
        snap.mkdir(parents=True)
        (snap / "model.bin").write_bytes(b"\x00\x01")
        (snap / "config.json").write_text("{}")
        cancel.set()  # user cancels just as the transfer completes

    monkeypatch.setattr(model_manager._engine, "get_download_fn", lambda: fake_download_fn)

    download("small", tmp_path, cancel_event=cancel)

    assert is_downloaded("small", tmp_path) is False
    assert list(tmp_path.iterdir()) == []


def test_download_creates_models_root_if_missing(monkeypatch, tmp_path):
    """download() must create models_root before calling the download function."""
    new_root = tmp_path / "new_dir" / "deeper"
    calls = []

    def fake_download_fn(name, *, cache_dir):
        calls.append(cache_dir)

    monkeypatch.setattr(model_manager._engine, "get_download_fn", lambda: fake_download_fn)

    download("large-v3", new_root)

    assert new_root.exists()
    assert len(calls) == 1


def test_download_passes_model_name_unchanged(monkeypatch, tmp_path):
    """The model name passed to the download fn must match the argument exactly."""
    received = {}

    def fake_download_fn(name, *, cache_dir):
        received["name"] = name

    monkeypatch.setattr(model_manager._engine, "get_download_fn", lambda: fake_download_fn)
    download("large-v3", tmp_path)
    assert received["name"] == "large-v3"


def test_download_with_cancel_event_not_set(monkeypatch, tmp_path):
    """download() with a cancel_event that is NOT set proceeds normally."""
    cancel = threading.Event()
    called = []

    def fake_download_fn(name, *, cache_dir):
        called.append(True)

    monkeypatch.setattr(model_manager._engine, "get_download_fn", lambda: fake_download_fn)
    download("small", tmp_path, cancel_event=cancel)
    assert called


def test_download_skips_if_cancel_event_already_set(monkeypatch, tmp_path):
    """download() must not call the download fn when cancel_event is pre-set."""
    cancel = threading.Event()
    cancel.set()
    called = []

    def fake_download_fn(name, *, cache_dir):
        called.append(True)

    monkeypatch.setattr(model_manager._engine, "get_download_fn", lambda: fake_download_fn)
    download("small", tmp_path, cancel_event=cancel)
    assert not called
