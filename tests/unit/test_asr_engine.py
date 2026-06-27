"""Tests for anki_miner.services.asr._engine — the import seam.

The default/dev environment has no faster-whisper, so the absence-path
assertions below run there. A dev who installs the ``[asr]`` extra (or CI's
``test-asr`` job) instead has it present; those tests skip rather than fail,
since the absence behaviour cannot be exercised when the package is importable.
"""

import importlib.util

import pytest

_FASTER_WHISPER_PRESENT = importlib.util.find_spec("faster_whisper") is not None

requires_faster_whisper_absent = pytest.mark.skipif(
    _FASTER_WHISPER_PRESENT,
    reason="faster-whisper is installed; absence-path behaviour only observable without it",
)


def test_engine_importable_without_faster_whisper():
    """The module must import cleanly even when faster_whisper is absent."""
    import anki_miner.services.asr._engine  # noqa: F401


@requires_faster_whisper_absent
def test_available_returns_false_when_faster_whisper_absent():
    """available() returns False (not an exception) when faster_whisper is not installed."""
    from anki_miner.services.asr._engine import available

    result = available()
    assert isinstance(result, bool)
    assert result is False


def test_available_no_top_level_faster_whisper_import():
    """faster_whisper must not be importable from the module's globals."""
    import anki_miner.services.asr._engine as engine_mod

    # The module namespace must not contain the 'faster_whisper' name.
    assert "faster_whisper" not in dir(engine_mod)


@requires_faster_whisper_absent
def test_get_whisper_model_cls_raises_import_error():
    """get_whisper_model_cls() must raise ImportError when faster_whisper is absent."""
    from anki_miner.services.asr._engine import get_whisper_model_cls

    with pytest.raises(ImportError):
        get_whisper_model_cls()


@requires_faster_whisper_absent
def test_get_download_fn_raises_import_error():
    """get_download_fn() must raise ImportError when faster_whisper is absent."""
    from anki_miner.services.asr._engine import get_download_fn

    with pytest.raises(ImportError):
        get_download_fn()


def test_skeleton_modules_importable():
    """All four ASR skeleton modules must be importable with no faster-whisper installed."""
    import anki_miner.services.asr._engine  # noqa: F401
    import anki_miner.services.asr.model_manager  # noqa: F401
    import anki_miner.services.asr.srt_writer  # noqa: F401
    import anki_miner.services.asr.transcriber  # noqa: F401


# ---------------------------------------------------------------------------
# cuda_device_count — GPU detection seam for the Settings GPU-pack gating
# ---------------------------------------------------------------------------


def test_cuda_device_count_returns_int():
    """cuda_device_count() returns an int (0 when ctranslate2 is absent/unusable)."""
    from anki_miner.services.asr._engine import cuda_device_count

    result = cuda_device_count()
    assert isinstance(result, int)
    assert result >= 0


@requires_faster_whisper_absent
def test_cuda_device_count_zero_when_ctranslate2_absent():
    """Without ctranslate2 installed the count is 0, never an exception."""
    from anki_miner.services.asr._engine import cuda_device_count

    assert cuda_device_count() == 0


def test_cuda_device_count_swallows_any_error(monkeypatch):
    """Any failure inside the count probe degrades to 0, never propagates."""
    import builtins

    from anki_miner.services.asr import _engine

    real_import = builtins.__import__

    def _boom(name, *args, **kwargs):
        if name == "ctranslate2":
            raise OSError("native CUDA runtime exploded")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _boom)
    assert _engine.cuda_device_count() == 0


def test_cuda_device_count_no_top_level_ctranslate2_import():
    """ctranslate2 must not be importable from the module's globals."""
    import anki_miner.services.asr._engine as engine_mod

    assert "ctranslate2" not in dir(engine_mod)
