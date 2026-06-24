"""Tests for anki_miner.services.asr._engine — the import seam.

faster-whisper is intentionally NOT installed in this environment.
All assertions must pass without it.
"""


def test_engine_importable_without_faster_whisper():
    """The module must import cleanly even when faster_whisper is absent."""
    import anki_miner.services.asr._engine  # noqa: F401


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


def test_get_whisper_model_cls_raises_import_error():
    """get_whisper_model_cls() must raise ImportError when faster_whisper is absent."""
    import pytest

    from anki_miner.services.asr._engine import get_whisper_model_cls

    with pytest.raises(ImportError):
        get_whisper_model_cls()


def test_get_download_fn_raises_import_error():
    """get_download_fn() must raise ImportError when faster_whisper is absent."""
    import pytest

    from anki_miner.services.asr._engine import get_download_fn

    with pytest.raises(ImportError):
        get_download_fn()


def test_skeleton_modules_importable():
    """All four ASR skeleton modules must be importable with no faster-whisper installed."""
    import anki_miner.services.asr._engine  # noqa: F401
    import anki_miner.services.asr.model_manager  # noqa: F401
    import anki_miner.services.asr.srt_writer  # noqa: F401
    import anki_miner.services.asr.transcriber  # noqa: F401
