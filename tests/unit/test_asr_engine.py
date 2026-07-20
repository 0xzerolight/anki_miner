"""Tests for anki_miner.services.asr._engine — the import seam.

The default/dev environment has no faster-whisper, so the absence-path
assertions below run there. A dev who installs the ``[asr]`` extra (or CI's
``test-asr`` job) instead has it present; those tests skip rather than fail,
since the absence behaviour cannot be exercised when the package is importable.
"""

import importlib.util
import subprocess

import pytest

_FASTER_WHISPER_PRESENT = importlib.util.find_spec("faster_whisper") is not None
_PYWHISPERCPP_PRESENT = importlib.util.find_spec("pywhispercpp") is not None

requires_pywhispercpp = pytest.mark.skipif(
    not _PYWHISPERCPP_PRESENT,
    reason="pywhispercpp not installed; the whisper.cpp model class import only works with it present",
)

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


# ---------------------------------------------------------------------------
# whisper.cpp (pywhispercpp) seam — engine availability + Vulkan device probe
# ---------------------------------------------------------------------------


def test_engine_no_top_level_pywhispercpp_or_ctypes_import():
    """pywhispercpp and ctypes must not leak into the module's globals (function-local only)."""
    import anki_miner.services.asr._engine as engine_mod

    assert "pywhispercpp" not in dir(engine_mod)
    assert "ctypes" not in dir(engine_mod)


def test_whisper_cpp_available_true_when_spec_and_lib_present(monkeypatch):
    """available iff pywhispercpp is findable AND a ggml-vulkan lib is locatable."""
    from pathlib import Path

    from anki_miner.services.asr import _engine

    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        lambda name: object() if name == "pywhispercpp" else None,
    )
    monkeypatch.setattr(_engine, "_find_ggml_vulkan_lib", lambda: Path("/fake/libggml-vulkan.so"))

    assert _engine.whisper_cpp_available() is True


def test_whisper_cpp_available_false_when_spec_missing(monkeypatch):
    """No pywhispercpp spec => False even if a lib were locatable."""
    from pathlib import Path

    from anki_miner.services.asr import _engine

    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    monkeypatch.setattr(_engine, "_find_ggml_vulkan_lib", lambda: Path("/fake/libggml-vulkan.so"))

    assert _engine.whisper_cpp_available() is False


def test_whisper_cpp_available_false_when_lib_missing(monkeypatch):
    """pywhispercpp present but no ggml-vulkan lib (the dev/CPU case) => False."""
    from anki_miner.services.asr import _engine

    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(_engine, "_find_ggml_vulkan_lib", lambda: None)

    assert _engine.whisper_cpp_available() is False


def test_whisper_cpp_available_false_in_this_real_env():
    """This dev env ships the CPU wheel (no ggml-vulkan) => False, no exception."""
    from anki_miner.services.asr import _engine

    result = _engine.whisper_cpp_available()
    assert isinstance(result, bool)
    assert result is False


def test_whisper_cpp_available_never_raises(monkeypatch):
    """A throwing locator degrades to False, never propagates."""
    from anki_miner.services.asr import _engine

    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())

    def _boom():
        raise RuntimeError("locator exploded")

    monkeypatch.setattr(_engine, "_find_ggml_vulkan_lib", _boom)

    assert _engine.whisper_cpp_available() is False


@requires_pywhispercpp
@pytest.mark.asr
def test_get_whisper_cpp_model_cls_returns_model():
    """get_whisper_cpp_model_cls() returns pywhispercpp.model.Model (real import)."""
    import pywhispercpp.model

    from anki_miner.services.asr._engine import get_whisper_cpp_model_cls

    assert get_whisper_cpp_model_cls() is pywhispercpp.model.Model


# --- vulkan_device_count: crash-safe, subprocess-isolated, memoized ----------


@pytest.fixture(autouse=False)
def _reset_vulkan_cache():
    """Reset the module-level memoization so each test sees a cold probe."""
    from anki_miner.services.asr import _engine

    _engine._VULKAN_DEVICE_COUNT = None
    yield
    _engine._VULKAN_DEVICE_COUNT = None


def test_vulkan_device_count_parses_stdout(monkeypatch, _reset_vulkan_cache):
    """A clean subprocess printing '3' yields 3."""
    from anki_miner.services.asr import _engine

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(args=a, returncode=0, stdout="3\n", stderr=""),
    )

    assert _engine.vulkan_device_count() == 3


def test_vulkan_device_count_zero_on_nonzero_returncode(monkeypatch, _reset_vulkan_cache):
    """A nonzero exit => 0, regardless of stdout."""
    from anki_miner.services.asr import _engine

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(args=a, returncode=1, stdout="3\n", stderr=""),
    )

    assert _engine.vulkan_device_count() == 0


def test_vulkan_device_count_zero_on_timeout(monkeypatch, _reset_vulkan_cache):
    """A TimeoutExpired => 0, never propagates."""
    from anki_miner.services.asr import _engine

    def _timeout(*a, **k):
        raise subprocess.TimeoutExpired(cmd="probe", timeout=15)

    monkeypatch.setattr(subprocess, "run", _timeout)

    assert _engine.vulkan_device_count() == 0


def test_vulkan_device_count_zero_on_non_integer_stdout(monkeypatch, _reset_vulkan_cache):
    """Garbage stdout => 0 (parse failure)."""
    from anki_miner.services.asr import _engine

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(args=a, returncode=0, stdout="not-a-number\n", stderr=""),
    )

    assert _engine.vulkan_device_count() == 0


def test_vulkan_device_count_zero_on_oserror(monkeypatch, _reset_vulkan_cache):
    """A spawn failure (OSError) => 0, never propagates."""
    from anki_miner.services.asr import _engine

    def _boom(*a, **k):
        raise OSError("cannot spawn")

    monkeypatch.setattr(subprocess, "run", _boom)

    assert _engine.vulkan_device_count() == 0


def test_vulkan_device_count_memoized(monkeypatch, _reset_vulkan_cache):
    """The subprocess runs at most once across repeated calls (per-process memoization)."""
    from anki_miner.services.asr import _engine

    calls = {"n": 0}

    def _run(*a, **k):
        calls["n"] += 1
        return subprocess.CompletedProcess(args=a, returncode=0, stdout="2\n", stderr="")

    monkeypatch.setattr(subprocess, "run", _run)

    assert _engine.vulkan_device_count() == 2
    assert _engine.vulkan_device_count() == 2
    assert _engine.vulkan_device_count() == 2
    assert calls["n"] == 1


def test_vulkan_device_count_argv_module_when_not_frozen(monkeypatch, _reset_vulkan_cache):
    """In the dev (non-frozen) case argv runs the probe module via -m."""
    import sys

    from anki_miner.services.asr import _engine

    monkeypatch.delattr(sys, "frozen", raising=False)
    captured = {}

    def _run(argv, *a, **k):
        captured["argv"] = argv
        captured["env"] = k.get("env")
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout="0\n", stderr="")

    monkeypatch.setattr(subprocess, "run", _run)
    _engine.vulkan_device_count()

    assert captured["argv"] == [sys.executable, "-m", "anki_miner.services.asr._vulkan_probe"]


def test_vulkan_device_count_argv_frozen_sets_env(monkeypatch, _reset_vulkan_cache):
    """In the frozen case argv is the executable with the probe env var set."""
    import sys

    from anki_miner.services.asr import _engine

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    captured = {}

    def _run(argv, *a, **k):
        captured["argv"] = argv
        captured["env"] = k.get("env")
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout="0\n", stderr="")

    monkeypatch.setattr(subprocess, "run", _run)
    _engine.vulkan_device_count()

    assert captured["argv"] == [sys.executable]
    assert captured["env"] is not None
    assert captured["env"].get("ANKI_MINER_ASR_VULKAN_PROBE") == "1"


def test_vulkan_device_count_zero_in_this_real_env(_reset_vulkan_cache):
    """End-to-end (real subprocess): the CPU wheel ships no ggml-vulkan => 0."""
    from anki_miner.services.asr import _engine

    result = _engine.vulkan_device_count()
    assert isinstance(result, int)
    assert result == 0


def test_find_ggml_vulkan_lib_none_in_this_real_env():
    """The locator returns None in this dev env (CPU wheel has no ggml-vulkan)."""
    from anki_miner.services.asr import _engine

    assert _engine._find_ggml_vulkan_lib() is None


def test_find_ggml_vulkan_lib_never_raises(monkeypatch):
    """A locator that hits an unexpected error returns None, never propagates."""
    from anki_miner.services.asr import _engine

    def _boom(name):
        raise RuntimeError("find_spec exploded")

    monkeypatch.setattr(importlib.util, "find_spec", _boom)

    assert _engine._find_ggml_vulkan_lib() is None


def test_find_ggml_core_lib_picks_auditwheel_hashed_dispatcher(tmp_path):
    """The dispatcher glob must match the AUDITWHEEL-HASHED libggml, not just the
    plain name — the shipped wheel renames it to libggml-<hash>.so.0.9.8, and a
    "libggml.so*" glob would miss it (registry never loads -> GGML_ASSERT abort in
    the bundle; caught by the release dry-run). It must NOT pick the -base/-cpu/
    -vulkan backend modules.
    """
    from anki_miner.services.asr import _engine

    for name in (
        "libggml-9964a741.so.0.9.8",  # hashed dispatcher (what auditwheel ships)
        "libggml-base-abcd1234.so.0.9.8",
        "libwhisper-ef012345.so.1.8.4",
        "libggml-cpu.so",
        "libggml-vulkan.so",
    ):
        (tmp_path / name).write_bytes(b"\x7fELF")

    core = _engine._find_ggml_core_lib([tmp_path])
    assert core is not None
    assert core.name == "libggml-9964a741.so.0.9.8"


def test_failed_backend_not_memoized_as_success(monkeypatch, tmp_path):
    """A failed loader is terminally FAILED; selection advances to fallback."""
    import ctypes

    from anki_miner.services.asr import _engine

    states = dict.fromkeys(_engine._GGML_BACKEND_STATES, _engine._BackendState.UNTRIED)
    monkeypatch.setattr(_engine, "_GGML_BACKEND_STATES", states)
    monkeypatch.setattr(_engine, "_find_ggml_vulkan_lib", lambda: tmp_path / "libggml-vulkan.so")
    monkeypatch.setattr(_engine, "_ggml_lib_search_dirs", lambda: [tmp_path])
    monkeypatch.setattr(_engine, "_find_ggml_core_lib", lambda _dirs: tmp_path / "libggml.so")

    calls = []

    def load_from_path(_path):
        calls.append("from_path")
        raise OSError("broken backend")

    def load_fallback():
        calls.append("fallback")

    class FakeLib:
        ggml_backend_load_all_from_path = staticmethod(load_from_path)
        ggml_backend_load_all = staticmethod(load_fallback)

    monkeypatch.setattr(ctypes, "CDLL", lambda *args, **kwargs: FakeLib())

    _engine.ensure_ggml_backends_loaded()

    assert states["ggml_backend_load_all_from_path"] is _engine._BackendState.FAILED
    assert states["ggml_backend_load_all"] is _engine._BackendState.SUCCEEDED
    _engine.ensure_ggml_backends_loaded()
    assert calls == ["from_path", "fallback"]
